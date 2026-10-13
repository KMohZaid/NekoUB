import html

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger
from userbot.websearch import fetch_text
from userbot.websearch import search as _shared_search

logger = get_logger(__name__)

PAGE_SIZE = 10  # results per page
MAX_OFFSET = 9  # provider pagination cap (offset is in multiples of count)
SNIPPET_LEN = 240
MAX_PAGE_TEXT = 3500  # keep the final message under Telegram's 4096 limit

# chat_id -> {"query": str, "page": int, "results": [ {...}, ... ]}
_state: dict[int, dict] = {}
# results-message-id -> results (lets users reply with a number to open one)
_msg_results: dict[int, list] = {}


async def _search(query: str, offset: int = 0) -> list[dict]:
    """Search using the shared web-search helper (PAGE_SIZE per page)."""
    return await _shared_search(query, count=PAGE_SIZE, offset=offset)


_fetch_text = fetch_text


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _render_results(query: str, page: int, results: list[dict]) -> str:
    prefix = config.CMD_PREFIX
    lines = [
        f"🌐 <b>{_esc(query)}</b>",
        f"<i>page {page + 1}</i>",
        "",
    ]
    for index, result in enumerate(results, start=1):
        snippet = result["snippet"]
        if len(snippet) > SNIPPET_LEN:
            snippet = snippet[:SNIPPET_LEN].rstrip() + "…"
        lines.append(f"<b>{index}. {_esc(result['title'])}</b>")
        if result["url"]:
            lines.append(f"<code>{_esc(result['url'])}</code>")
        if snippet:
            lines.append(_esc(snippet))
        lines.append("")
    lines.append(
        f"Open: <code>{prefix}open &lt;n&gt;</code> · "
        f"Next: <code>{prefix}web next</code> · "
        f"Prev: <code>{prefix}web prev</code>"
    )
    lines.append("Or just reply to this message with a number.")
    return "\n".join(lines)


async def _do_search(message: Message, query: str, offset: int) -> None:
    offset = max(0, min(offset, MAX_OFFSET))
    await message.edit(f"🌐 Searching for <b>{_esc(query)}</b>…",
                       parse_mode=ParseMode.HTML)
    try:
        results = await _search(query, offset)
    except Exception as exc:
        logger.error(f"[WEB] search failed: {exc}")
        await message.edit(
            f"❌ Search failed: <code>{_esc(type(exc).__name__)}</code>\n"
            f"{_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return

    if not results:
        await message.edit(
            f"😿 No results for <b>{_esc(query)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    _state[message.chat.id] = {
        "query": query,
        "page": offset,
        "results": results,
    }
    _msg_results[message.id] = results
    await message.edit(
        _render_results(query, offset, results), parse_mode=ParseMode.HTML
    )


async def _open_result(message: Message, index: int) -> None:
    state = _state.get(message.chat.id)
    results = state["results"] if state else None
    if not results:
        await message.edit(
            f"😿 No saved results. Start with "
            f"<code>{config.CMD_PREFIX}web &lt;query&gt;</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    if index < 1 or index > len(results):
        await message.edit(
            f"❌ Pick a number between 1 and {len(results)}.",
            parse_mode=ParseMode.HTML,
        )
        return

    result = results[index - 1]
    await message.edit(
        f"📄 Opening <b>{_esc(result['title'])}</b>…",
        parse_mode=ParseMode.HTML,
    )
    try:
        text = await _fetch_text(result["url"])
    except Exception as exc:
        logger.error(f"[WEB] open failed: {exc}")
        await message.edit(
            f"❌ Could not open <code>{_esc(result['url'])}</code>\n"
            f"<code>{_esc(type(exc).__name__)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not text:
        await message.edit(
            f"😿 No readable text at <code>{_esc(result['url'])}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    body = text[:MAX_PAGE_TEXT]
    if len(text) > MAX_PAGE_TEXT:
        body += "\n… (truncated)"
    await message.edit(
        f"📄 <b>{_esc(result['title'])}</b>\n"
        f"<code>{_esc(result['url'])}</code>\n\n{_esc(body)}",
        parse_mode=ParseMode.HTML,
    )


@main.app.on_message(
    filters.me & filters.command(["web", "search"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def web_command(client, message: Message):
    """Search the web / page through results.

    Usage:
        .web <query>
        .web next | prev
    """
    prefix = config.CMD_PREFIX
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if not arg:
        await message.edit(
            f"🌐 <b>Web search</b>\n\n"
            f"<code>{prefix}web &lt;query&gt;</code> - search\n"
            f"<code>{prefix}web next</code> / "
            f"<code>{prefix}web prev</code> - paginate\n"
            f"<code>{prefix}open &lt;n&gt;</code> - read a result\n"
            f"<code>{prefix}read &lt;url&gt;</code> - read any URL",
            parse_mode=ParseMode.HTML,
        )
        return

    lowered = arg.lower()
    state = _state.get(message.chat.id)

    if lowered in ("next", "more") and state:
        await _do_search(message, state["query"], state["page"] + 1)
        return
    if lowered in ("prev", "back") and state:
        await _do_search(message, state["query"], state["page"] - 1)
        return

    await _do_search(message, arg, 0)


@main.app.on_message(
    filters.me & filters.command(["next"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def next_command(client, message: Message):
    """Next page of the last search. Usage: .next"""
    state = _state.get(message.chat.id)
    if not state:
        await message.edit(
            f"😿 No search to page through. Try "
            f"<code>{config.CMD_PREFIX}web &lt;query&gt;</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    await _do_search(message, state["query"], state["page"] + 1)


@main.app.on_message(
    filters.me & filters.command(["prev"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def prev_command(client, message: Message):
    """Previous page of the last search. Usage: .prev"""
    state = _state.get(message.chat.id)
    if not state:
        await message.edit(
            f"😿 No search to page through. Try "
            f"<code>{config.CMD_PREFIX}web &lt;query&gt;</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    await _do_search(message, state["query"], state["page"] - 1)


@main.app.on_message(
    filters.me & filters.command(["open"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def open_command(client, message: Message):
    """Read a search result. Usage: .open <n>"""
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg.isdigit():
        await message.edit(
            f"Usage: <code>{config.CMD_PREFIX}open &lt;n&gt;</code> "
            f"(n from the last {config.CMD_PREFIX}web)",
            parse_mode=ParseMode.HTML,
        )
        return
    await _open_result(message, int(arg))


@main.app.on_message(
    filters.me
    & filters.command(["read", "browse", "url"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def read_command(client, message: Message):
    """Read any URL as plain text. Usage: .read <url>"""
    parts = (message.text or "").split(maxsplit=1)
    url = parts[1].strip() if len(parts) > 1 else ""
    if not url:
        await message.edit(
            f"Usage: <code>{config.CMD_PREFIX}read &lt;url&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    await message.edit(
        f"📄 Reading <code>{_esc(url)}</code>…", parse_mode=ParseMode.HTML
    )
    try:
        text = await _fetch_text(url)
    except Exception as exc:
        logger.error(f"[WEB] read failed: {exc}")
        await message.edit(
            f"❌ Failed to read <code>{_esc(url)}</code>\n"
            f"<code>{_esc(type(exc).__name__)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not text:
        await message.edit(
            f"😿 No readable text at <code>{_esc(url)}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    body = text[:MAX_PAGE_TEXT]
    if len(text) > MAX_PAGE_TEXT:
        body += "\n… (truncated)"
    await message.edit(
        f"📄 <code>{_esc(url)}</code>\n\n{_esc(body)}",
        parse_mode=ParseMode.HTML,
    )


@main.app.on_message(filters.me & filters.reply & filters.regex(r"^\s*\d+\s*$"))
async def number_reply(client, message: Message):
    """Reply to a results message with a number to open that result."""
    reply = message.reply_to_message
    if not reply or reply.id not in _msg_results:
        return
    await _open_result(message, int(message.text.strip()))
