import asyncio
import html
from urllib.parse import urlparse

import httpx
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger
from userbot.websearch import search as web_search

logger = get_logger(__name__)

REQUEST_TIMEOUT = 120.0
MAX_REPLY_CHARS = 3600
MAX_HISTORY = 16  # messages kept per chat (8 exchanges)
MAX_REPLY_CONTEXT = 2000
MAX_POLLS = 30  # deferred-queue polls before giving up
SEARCH_RESULTS = 8  # results fed to the model for `.ai search`
MAX_SUMMARY_CHARS = 3000

# chat_id -> list of {"role", "content"}
_history: dict[int, list] = {}
# chat_id -> {"model": str | None, "system": str | None}
_overrides: dict[int, dict] = {}


def _origin() -> str:
    parsed = urlparse(config.AI_API_URL)
    return f"{parsed.scheme}://{parsed.netloc}"


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"
    return headers


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _model(chat_id: int) -> str:
    return (_overrides.get(chat_id, {}).get("model")) or config.AI_MODEL


def _system(chat_id: int) -> str:
    return (
        _overrides.get(chat_id, {}).get("system") or config.AI_SYSTEM_PROMPT
    ).strip()


def _reply_text(message: Message) -> str:
    reply = message.reply_to_message
    if not reply:
        return ""
    text = reply.text or reply.caption or ""
    if not text:
        # Describe non-text media so the model still gets a hint.
        if reply.sticker:
            return f"[sticker: {reply.sticker.emoji or ''}]".strip()
        if reply.photo:
            return "[photo]"
        if reply.video:
            return "[video]"
        if reply.document:
            return f"[document: {reply.document.file_name or ''}]"
        return ""
    return text[:MAX_REPLY_CONTEXT]


def _extract_content(payload: dict) -> str | None:
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except Exception:
        return response.text[:500] or f"HTTP {response.status_code}"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return error.get("message") or str(error)
        if error:
            return str(error)
        if data.get("message"):
            return str(data["message"])
    return f"HTTP {response.status_code}"


async def _complete(messages: list, chat_id: int) -> tuple[str | None, str | None]:
    """Return (content, error). Handles the gateway's 202 deferred queue."""
    model = _model(chat_id)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            f"{config.AI_API_URL.rstrip('/')}/chat/completions",
            headers=_headers(),
            json={"model": model, "messages": messages},
        )

        # Deferred queue: 202 + poll the result endpoint until it's ready.
        if response.status_code == 202:
            try:
                info = response.json()
            except Exception:
                info = {}
            result_url = info.get("result_url") or (
                f"/v1/requests/{info.get('id')}"
            )
            delay = float(info.get("retry_after") or 5)
            for _ in range(MAX_POLLS):
                await asyncio.sleep(max(1.0, delay))
                poll = await client.get(
                    _origin() + result_url, headers=_headers()
                )
                if poll.status_code == 202:
                    try:
                        delay = float(
                            poll.json().get("retry_after") or delay
                        )
                    except Exception:
                        pass
                    continue
                response = poll
                break
            else:
                return None, "Timed out waiting for the deferred response."

        if response.status_code != 200:
            return None, _error_message(response)

        try:
            data = response.json()
        except Exception:
            return None, "The endpoint returned a non-JSON response."

        content = _extract_content(data)
        if not content:
            return None, _error_message(response) if "error" in data else (
                "The model returned an empty response."
            )
        return content, None


async def _ai_search(message: Message, query: str, chat_id: int) -> None:
    """Search the web, then have the model summarize the results."""
    await message.edit(
        f"🔎 Searching the web for <b>{_esc(query)}</b>…",
        parse_mode=ParseMode.HTML,
    )
    try:
        results = await web_search(query, count=SEARCH_RESULTS)
    except Exception as exc:
        logger.error(f"[AI] search failed: {exc}")
        await message.edit(
            f"❌ Search failed: <code>{_esc(type(exc).__name__)}</code>\n"
            f"{_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return

    if not results:
        await message.edit(
            f"😿 No search results for <b>{_esc(query)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    blocks = []
    for index, result in enumerate(results, 1):
        blocks.append(
            f"[{index}] {result['title']}\n"
            f"URL: {result['url']}\n"
            f"{result['snippet']}"
        )
    context = "\n\n".join(blocks)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a research assistant. Using ONLY the numbered search "
                "results provided, write a concise, well-structured answer to "
                "the user's query. Cite sources inline with their [n] markers. "
                "Never invent facts or sources."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Query: {query}\n\n"
                f"Search results:\n{context}\n\n"
                "Summarize the answer and cite sources as [n]."
            ),
        },
    ]

    await message.edit("🤖 Summarizing…", parse_mode=ParseMode.HTML)
    try:
        summary, error = await _complete(messages, chat_id)
    except Exception as exc:
        logger.error(f"[AI] summarize failed: {exc}")
        await message.edit(
            f"❌ Request failed: <code>{_esc(type(exc).__name__)}</code>\n"
            f"{_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return

    if error:
        await message.edit(f"❌ {_esc(error)}", parse_mode=ParseMode.HTML)
        return

    body = summary[:MAX_SUMMARY_CHARS]
    if len(summary) > MAX_SUMMARY_CHARS:
        body += "\n… (truncated)"
    sources = "\n".join(
        f"{index}. {_esc(result['title'])}\n   {_esc(result['url'])}"
        for index, result in enumerate(results, 1)
    )
    text = (
        f"🔎 <b>{_esc(query)}</b>\n\n"
        f"{_esc(body)}\n\n"
        f"<b>Sources</b>\n{sources}"
    )
    if len(text) > 4000:
        text = text[:3990] + "…"
    await message.edit(text, parse_mode=ParseMode.HTML)


@main.app.on_message(
    filters.me & filters.command(["ai", "ask"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def ai_command(client, message: Message):
    """Chat with the configured AI model. Usage: .ai <prompt> | .ai help"""
    prefix = config.CMD_PREFIX
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    chat_id = message.chat.id
    lowered = arg.lower()

    if lowered in ("help", "?", "-h", "--help"):
        await message.edit(
            f"🤖 <b>AI chat</b>\n\n"
            f"<code>{prefix}ai &lt;prompt&gt;</code> - ask the model\n"
            f"<code>{prefix}ai</code> (reply) - use the replied message\n"
            f"<code>{prefix}ai &lt;prompt&gt;</code> (reply) - add reply as context\n"
            f"<code>{prefix}ai reset</code> - clear conversation\n"
            f"<code>{prefix}ai search &lt;query&gt;</code> - web results + summary\n"
            f"<code>{prefix}ai model [name]</code> - show/set model\n"
            f"<code>{prefix}ai sys [text]</code> - show/set system prompt\n\n"
            f"Model: <code>{_esc(_model(chat_id))}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if lowered in ("reset", "clear", "forget"):
        _history.pop(chat_id, None)
        await message.edit("🧹 Conversation cleared.")
        return

    if lowered == "model" or lowered.startswith("model "):
        new_model = arg[5:].strip()
        if new_model:
            _overrides.setdefault(chat_id, {})["model"] = new_model
            await message.edit(
                f"✅ Model set to <code>{_esc(new_model)}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.edit(
                f"Model: <code>{_esc(_model(chat_id))}</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    if lowered == "sys" or lowered.startswith("sys ") or lowered.startswith(
        "system"
    ):
        new_sys = arg
        for token in ("system", "sys"):
            if new_sys.lower().startswith(token):
                new_sys = new_sys[len(token):].strip()
                break
        if new_sys:
            _overrides.setdefault(chat_id, {})["system"] = new_sys
            await message.edit(
                f"✅ System prompt set to:\n<i>{_esc(new_sys)}</i>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.edit(
                f"System prompt:\n<i>{_esc(_system(chat_id))}</i>",
                parse_mode=ParseMode.HTML,
            )
        return

    if lowered == "search" or lowered.startswith("search "):
        query = arg[len("search"):].strip()
        if not query:
            query = _reply_text(message)
        if not query:
            await message.edit(
                f"🔎 Usage: <code>{prefix}ai search &lt;query&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await _ai_search(message, query, chat_id)
        return

    reply_text = _reply_text(message)
    if not arg and not reply_text:
        await message.edit(
            f"🤖 Usage: <code>{prefix}ai &lt;prompt&gt;</code> "
            f"(or reply to a message) · <code>{prefix}ai help</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    prompt = arg
    if reply_text:
        context = f"Context (replied message):\n{reply_text}\n\n"
        prompt = context + (arg or "Summarize or respond to the above.")

    if not config.AI_API_KEY and "127.0.0.1" not in config.AI_API_URL:
        await message.edit(
            "❌ No AI_API_KEY configured. Set it in .env.",
            parse_mode=ParseMode.HTML,
        )
        return

    history = _history.setdefault(chat_id, [])
    messages = []
    system = _system(chat_id)
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    await message.edit("🤖 Thinking…")
    try:
        content, error = await _complete(messages, chat_id)
    except Exception as exc:
        logger.error(f"[AI] request failed: {exc}")
        await message.edit(
            f"❌ Request failed: <code>{_esc(type(exc).__name__)}</code>\n"
            f"{_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return

    if error:
        await message.edit(f"❌ {_esc(error)}", parse_mode=ParseMode.HTML)
        return

    # Remember the exchange (store the raw prompt, not the context wrapper).
    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": content})
    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    body = content[:MAX_REPLY_CHARS]
    if len(content) > MAX_REPLY_CHARS:
        body += "\n… (truncated)"
    question = (arg or reply_text or "").strip()
    if len(question) > 300:
        question = question[:300] + "…"
    footer = f"\n\n<i>Asked question: {_esc(question)}</i>" if question else ""
    await message.edit(
        f"🤖 {_esc(body)}{footer}",
        parse_mode=ParseMode.HTML,
    )
