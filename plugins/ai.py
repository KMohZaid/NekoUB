import asyncio
import html
import re
import secrets
import time
from urllib.parse import urlparse

import httpx
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineQuery, Message, ReplyParameters

import config
from core.plugins import Example, Plugin, PluginContext, ParsedOptions, handlers as h
from utils import (
    get_logger,
    is_user_premium,
    split_text_into_chunks,
)
from utils.websearch import search as web_search

logger = get_logger(__name__)

REQUEST_TIMEOUT = 120.0
FREE_MESSAGE_LIMIT = 3700
PREMIUM_MESSAGE_LIMIT = 7700
MAX_HISTORY = 16  # messages kept per chat (8 exchanges)
MAX_REPLY_CONTEXT = 2000
MAX_POLLS = 30  # deferred-queue polls before giving up
SEARCH_RESULTS = 8  # results fed to the model for `.ai search`
MAX_AI_MESSAGES = 3
INLINE_RESPONSE_TTL = 300.0

RICH_MARKDOWN_DIRECTIVE = (
    "Write answers using Telegram Rich Markdown, preserving its syntax rather "
    "than flattening it. Use formatting such as **bold**, *italic*, ~~strike~~, "
    "`inline code`, ||spoilers||, headings, paragraphs, horizontal rules, "
    "unordered/ordered/task lists, blockquotes and expandable blockquotes where "
    "useful. For important external resources, use actual URL buttons with "
    "Telegram's HTML syntax, for example <tg-button type=\"url\" "
    "style=\"success\" url=\"https://example.com\">Open resource</tg-button>; "
    "a Markdown [label](url) is only an inline text link, not a button. Use buttons "
    "sparingly and only when the destination is useful. Also use inline links, "
    "email/phone/user links, and emoji or date-time links where relevant. Use "
    "tables only when they clarify comparisons. "
    "Use inline math with $...$, display math with $$...$$, or fenced ```math "
    "blocks for equations. Use fenced code blocks with an accurate language tag "
    "(for example ```python); Telegram may render them monospaced without syntax "
    "coloring, so do not claim color highlighting is guaranteed. Use "
    "footnotes, underline, superscript, subscript, and other supported Rich "
    "Markdown syntax when useful, not as decoration. Organize long answers into "
    "useful <details open><summary>Section</summary>...</details> sections, "
    "keeping the opening about 4,000 characters outside details; Telegram handles "
    "long-message Show More natively, so never truncate at 4,000 characters. "
    "Use <details> collapsed only for optional material; do not hide the whole "
    "answer. Do not force every syntax into every response."
)
PLAIN_TEXT_DIRECTIVE = RICH_MARKDOWN_DIRECTIVE
RICH_MESSAGE_LIMIT = 32768
RICH_MESSAGE_TRUNCATION = "\n\n[Response shortened to fit Telegram's rich-message limit.]"

# chat_id -> list of {"role", "content"}
_history: dict[int, list] = {}
# chat_id -> {"model": str | None, "system": str | None}
_overrides: dict[int, dict] = {}
# opaque key -> (expiry timestamp, rich response)
_inline_responses: dict[str, tuple[float, str]] = {}


def _stage_inline_response(text: str) -> str:
    now = time.monotonic()
    for key, (expires_at, _) in list(_inline_responses.items()):
        if expires_at <= now:
            _inline_responses.pop(key, None)
    key = secrets.token_urlsafe(24)
    _inline_responses[key] = (now + INLINE_RESPONSE_TTL, text)
    return key


def _format_token_count(value: int) -> str:
    exact = f"{value:,}"
    if value < 1000:
        return exact
    suffixes = ((1_000_000_000, "B"), (1_000_000, "M"), (1000, "k"))
    for scale, suffix in suffixes:
        if value >= scale:
            short = f"{value / scale:.1f}".rstrip("0").rstrip(".")
            return f"{exact} ({short}{suffix})"
    return exact


def _append_token_usage(
    content: str, usage: dict | None, *, elapsed_seconds: float | None = None
) -> str:
    footer_parts = []
    if isinstance(usage, dict):
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total = usage.get("total_tokens")
            if not isinstance(total, int):
                total = input_tokens + output_tokens
            footer_parts.extend(
                (
                    f"Input tokens: {_format_token_count(input_tokens)}",
                    f"Output tokens: {_format_token_count(output_tokens)}",
                    f"Total: {_format_token_count(total)}",
                )
            )
    if elapsed_seconds is not None:
        footer_parts.append(f"Time: {elapsed_seconds:.1f}s")
    return f"{content}\n\n{' · '.join(footer_parts)}" if footer_parts else content


def _prepare_rich_answer(content: str) -> str:
    if not content or not content.strip():
        raise ValueError("The model returned an empty rich answer.")
    content = content.strip()
    if len(content.encode("utf-8")) <= RICH_MESSAGE_LIMIT:
        return content
    marker = RICH_MESSAGE_TRUNCATION
    target_bytes = RICH_MESSAGE_LIMIT - len(marker.encode("utf-8"))
    prefix = content.encode("utf-8")[:target_bytes].decode("utf-8", errors="ignore")
    boundary = prefix.rfind("\n")
    if boundary > target_bytes // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip() + marker


def _take_inline_response(key: str) -> str | None:
    staged = _inline_responses.pop(key, None)
    if staged is None:
        return None
    expires_at, text = staged
    if expires_at <= time.monotonic():
        return None
    return text


async def _answer_ai_inline_query(client, query_id: str, key: str, bot_token: str) -> None:
    text = _take_inline_response(key)
    results = []
    if text is not None:
        results.append(
            {
                "type": "article",
                "id": key,
                "title": "AI response",
                "input_message_content": {"rich_message": {"markdown": text}},
            }
        )
    response = await client.post(
        f"https://api.telegram.org/bot{bot_token}/answerInlineQuery",
        json={
            "inline_query_id": query_id,
            "results": results,
            "cache_time": 0,
            "is_personal": True,
        },
    )
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError("Telegram returned a non-JSON inline-query response") from exc
    if response.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
        detail = data.get("description", "Telegram inline-query request failed") if isinstance(data, dict) else "Telegram inline-query request failed"
        raise RuntimeError(detail)


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


# --- Markdown/HTML/LaTeX stripping (AI answers are sent as plain text) ---
_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_RULE_RE = re.compile(r"^[ \t]{0,3}(?:[-*_=][ \t]*){2,}$", re.MULTILINE)
_QUOTE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_ITALIC_UND_RE = re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)")
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_LATEX_DELIM_RE = re.compile(r"\\[()\[\]]|\$\$")
_LATEX_WORDS = {
    r"\times": "×",
    r"\cdot": "·",
    r"\approx": "≈",
    r"\geq": "≥",
    r"\leq": "≤",
    r"\rightarrow": "→",
    r"\to": "→",
    r"\infty": "∞",
    r"\pm": "±",
    r"\ldots": "…",
    r"\dots": "…",
}
_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped.startswith("|") and stripped.endswith("|")


def _parse_table_row(line: str) -> list[str]:
    row = line.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") else row
    return [_HTML_BR_RE.sub(" ", cell).strip() for cell in row.split("|")]


def _is_sep_row(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL_RE.fullmatch(c.replace(" ", "")) for c in cells)


def _render_table(block: list[str]) -> list[str]:
    parsed = [_parse_table_row(line) for line in block]
    header = None
    if len(parsed) >= 2 and _is_sep_row(parsed[1]):
        header = parsed[0]
        parsed = parsed[2:]
    else:
        parsed = [row for row in parsed if not _is_sep_row(row)]
    lines: list[str] = []
    for cells in parsed:
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        if len(cells) == 2 and all(cells):
            lines.append(f"• {cells[0]}: {cells[1]}")
        elif header and len(header) == len(cells):
            lines.append(f"• {cells[0] or '—'}")
            for index in range(1, len(cells)):
                if cells[index]:
                    label = header[index].strip() or f"Column {index + 1}"
                    lines.append(f"  {label}: {cells[index]}")
        else:
            lines.append("• " + " — ".join(nonempty))
    return lines


def _flatten_tables(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    total = len(lines)
    while index < total:
        if _is_table_line(lines[index]):
            block: list[str] = []
            while index < total and _is_table_line(lines[index]):
                block.append(lines[index])
                index += 1
            out.extend(_render_table(block))
        else:
            out.append(lines[index])
            index += 1
    return "\n".join(out)


def _strip_markdown(text: str) -> str:
    """Convert a model's Markdown answer into clean, readable plain text."""
    if not text:
        return ""
    # Code: drop fences/backticks but keep the code itself.
    text = _FENCE_RE.sub(lambda m: m.group(1), text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    # Images keep their label; links become "label (url)".
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    # Tables -> readable bullet lines (this also consumes in-cell <br>).
    text = _flatten_tables(text)
    # HTML: <br> -> newline, then drop remaining tags and unescape entities.
    text = _HTML_BR_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )
    # LaTeX: drop math delimiters and translate common symbols.
    text = _LATEX_DELIM_RE.sub("", text)
    for command, symbol in _LATEX_WORDS.items():
        text = text.replace(command, symbol)
    # Block structure.
    text = _HEADING_RE.sub("", text)
    text = _RULE_RE.sub("", text)
    text = _QUOTE_RE.sub("", text)
    # Emphasis.
    text = _BOLD_RE.sub(r"\2", text)
    text = _STRIKE_RE.sub(r"\1", text)
    text = _ITALIC_UND_RE.sub(r"\1", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    # Bullets -> "• ".
    text = _BULLET_RE.sub(r"\1• ", text)
    # Tidy up whitespace.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _send_processing_reaction(client, message: Message) -> bool:
    try:
        if hasattr(message, "react"):
            await message.react("👨‍💻")
        elif hasattr(client, "send_reaction"):
            await client.send_reaction(message.chat.id, message.id, "👨‍💻")
        else:
            return False
        return True
    except Exception:
        logger.warning("[AI] Processing reaction failed", exc_info=True)
        return False


async def _send_ai_response(
    client, message: Message, body: str, question: str
) -> None:
    """Send a long AI answer as paginated, line-preserving messages."""
    try:
        is_premium = await is_user_premium(client)
    except Exception as exc:
        logger.warning(f"[AI] Could not determine Premium status; using free limit: {exc}")
        is_premium = False

    # TODO: Improve length accounting to ignore formatting markup and use the exact Telegram limit.
    body_limit = PREMIUM_MESSAGE_LIMIT if is_premium else FREE_MESSAGE_LIMIT
    chunks = split_text_into_chunks(body, body_limit)
    total = len(chunks)
    send_count = min(total, MAX_AI_MESSAGES)
    question = question.strip()
    if len(question) > 300:
        question = question[:300] + "…"

    for index, chunk in enumerate(chunks[:send_count], 1):
        pagination = f" ({index}/{total})" if total > 1 else ""
        if index == send_count and total > send_count:
            pagination = f" ({index}/{total} - stopped here, but got more responses)"
        if len(body) > 1500:
            quote = f"<blockquote expandable>🤖 {_esc(chunk)}</blockquote>"
        elif len(body) > 700:
            quote = f"<blockquote>🤖 {_esc(chunk)}</blockquote>"
        else:
            quote = f"🤖 {_esc(chunk)}"
        footer = (
            f"\n\n<b>Asked question{pagination}:</b> "
            f"<i>{_esc(question)}</i>"
            if question
            else f"\n\n{pagination.strip()}"
        )
        rendered = quote + footer
        if index == 1:
            await message.edit(rendered, parse_mode=ParseMode.HTML)
        else:
            await asyncio.sleep(1.5)
            await message.reply_text(rendered, parse_mode=ParseMode.HTML)


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


async def _complete(messages: list, chat_id: int) -> tuple[str | None, str | None, dict | None]:
    """Return (content, error, usage). Handles the gateway's 202 deferred queue."""
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
                return None, "Timed out waiting for the deferred response.", None

        if response.status_code != 200:
            return None, _error_message(response), None

        try:
            data = response.json()
        except Exception:
            return None, "The endpoint returned a non-JSON response.", None

        content = _extract_content(data)
        if not content:
            return None, _error_message(response) if "error" in data else (
                "The model returned an empty response."
            ), None
        usage = data.get("usage")
        return content, None, usage if isinstance(usage, dict) else None


async def _ai_search(client, message: Message, query: str, chat_id: int) -> None:
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
                "Never invent facts or sources. "
                + PLAIN_TEXT_DIRECTIVE
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
        summary, error, _usage = await _complete(messages, chat_id)
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

    body = _strip_markdown(summary)
    sources = "\n".join(
        f"{index}. {result['title']}\n   {result['url']}"
        for index, result in enumerate(results, 1)
    )
    text = f"🔎 {query}\n\n{body}\n\nSources\n{sources}"
    await _send_ai_response(client, message, text, query)


class AIPlugin(Plugin):
    id = "ai"
    category = "AI & Tools"
    description = "Chat with the configured AI model and search the web."

    @h.bot_inline_query(filters=filters.regex(r"^ai\."), public=True)
    async def ai_inline_query(
        self,
        bot: Client,
        query: InlineQuery,
        ctx: PluginContext["AIPlugin"],
    ) -> None:
        key = query.query.partition(".")[2]
        token = getattr(ctx.bot, "bot_token", None) or config.BOT_TOKEN
        if not token:
            await query.answer([])
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await _answer_ai_inline_query(client, query.id, key, token)
        except Exception as exc:
            logger.warning(f"[AI] inline query failed: {type(exc).__name__}")
            raise

    @h.command(
        "ai",
        aliases=("ask",),
        short_description="Chat with the configured AI or search the web.",
        description=(
            "Send a prompt to the configured AI model. Reply to a message to include it as context, "
            "or use the fixed subcommands `search`, `reset`, `model`, and `sys` to search the web, "
            "clear the conversation, or view and update AI settings. With no prompt, reply to a message "
            "to ask about it."
        ),
        examples=(
            Example("{cmd} Explain this error", "Ask the configured model a direct question."),
            Example("{cmd} search latest Python release", "Search the web and get a summary with sources."),
            Example("{cmd} reset", "Clear the saved conversation for this chat."),
        ),
        prefixes=config.CMD_PREFIX,
        filters=filters.me,
    )
    async def ai_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["AIPlugin"],
        args: ParsedOptions,
    ) -> None:
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
            await _ai_search(client, message, query, chat_id)
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
            system = f"{system}\n\n{PLAIN_TEXT_DIRECTIVE}".strip()
        else:
            system = PLAIN_TEXT_DIRECTIVE
        messages.append({"role": "system", "content": system})
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        await _send_processing_reaction(client, message)
        started_at = time.monotonic()
        try:
            content, error, usage = await _complete(messages, chat_id)
        except Exception as exc:
            logger.error(f"[AI] request failed: {type(exc).__name__}")
            await message.reply_text(
                f"❌ Request failed: <code>{_esc(type(exc).__name__)}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if error:
            await message.reply_text(f"❌ {_esc(error)}", parse_mode=ParseMode.HTML)
            return

        # Remember the exchange (store the raw prompt, not the context wrapper).
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": content})
        if len(history) > MAX_HISTORY:
            del history[: len(history) - MAX_HISTORY]

        elapsed_seconds = time.monotonic() - started_at
        content = _append_token_usage(
            content, usage, elapsed_seconds=elapsed_seconds
        )
        key = _stage_inline_response(_prepare_rich_answer(content))
        try:
            if ctx.bot is None:
                raise RuntimeError("Assistant bot is not configured")
            bot_info = await ctx.bot.get_me()
            results = await client.get_inline_bot_results(
                bot_info.username, f"ai.{key}"
            )
            if not results.results:
                raise RuntimeError("Assistant bot returned no AI response")
            await client.send_inline_bot_result(
                message.chat.id,
                results.query_id,
                results.results[0].id,
                reply_parameters=ReplyParameters(message_id=message.id),
            )
        except Exception as exc:
            logger.error(f"[AI] inline delivery failed: {type(exc).__name__}")
            await message.reply_text(
                f"❌ Could not deliver the AI response (<code>{_esc(type(exc).__name__)}</code>).",
                parse_mode=ParseMode.HTML,
            )
