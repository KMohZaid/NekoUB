import asyncio
import html

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
import main
from userbot.utils import (
    continue_propagation,
    get_logger,
    is_user_premium,
    split_text_into_chunks,
)
from userbot.websearch import fetch_text
from userbot.websearch import search as _shared_search

logger = get_logger(__name__)

PAGE_SIZE = 10  # results per page
MAX_OFFSET = 9  # provider pagination cap (offset is in multiples of count)
SNIPPET_LEN = 240
FREE_MESSAGE_LIMIT = 3700
PREMIUM_MESSAGE_LIMIT = 7700
MAX_PAGE_MESSAGES = 3

# chat_id -> {"query": str, "page": int, "results": {number: result}}
_state: dict[int, dict] = {}
# results-message-id -> {global result number: result}
_msg_results: dict[int, dict[int, dict]] = {}


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
    lines.append("<blockquote>")
    for local_index, result in enumerate(results):
        index = page * PAGE_SIZE + local_index + 1
        snippet = result["snippet"]
        if len(snippet) > SNIPPET_LEN:
            snippet = snippet[:SNIPPET_LEN].rstrip() + "…"
        lines.append(f"<b>{index}. {_esc(result['title'])}</b>")
        if result["url"]:
            lines.append(_esc(result["url"]))
        if snippet:
            lines.append(_esc(snippet))
        lines.append("")
    lines.append("</blockquote>")
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

    page_results = {
        offset * PAGE_SIZE + index + 1: result
        for index, result in enumerate(results)
    }
    state = _state.setdefault(
        message.chat.id, {"query": query, "page": offset, "results": {}}
    )
    if state["query"] != query:
        state["results"] = {}
    state["query"] = query
    state["page"] = offset
    state["results"].update(page_results)
    _msg_results[message.id] = page_results
    await message.edit(
        _render_results(query, offset, results),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# TODO: Think of a way to show text but with proper formatting and without unnecessary footer or unnecessary tasks.
async def _send_page_content(
    client, message: Message, title: str, url: str, text: str
) -> None:
    try:
        is_premium = await is_user_premium(client)
    except Exception as exc:
        logger.warning(f"[WEB] Could not determine Premium status; using free limit: {exc}")
        is_premium = False

    # TODO: Improve length accounting to ignore formatting markup and use the exact Telegram limit.
    body_limit = PREMIUM_MESSAGE_LIMIT if is_premium else FREE_MESSAGE_LIMIT
    chunks = split_text_into_chunks(text, body_limit)
    total = len(chunks)
    send_count = min(total, MAX_PAGE_MESSAGES)
    for part, chunk in enumerate(chunks[:send_count], 1):
        pagination = f" ({part}/{total})" if total > 1 else ""
        if part == send_count and total > send_count:
            pagination = f" ({part}/{total} - stopped here, but got more responses)"
        rendered = (
            f"📄 <b>{_esc(title)}</b>{pagination}\n"
            f"{_esc(url)}\n\n"
            f"<blockquote expandable>{_esc(chunk)}</blockquote>"
        )
        if part == 1:
            await message.edit(
                rendered,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        else:
            await asyncio.sleep(1.5)
            await message.reply_text(
                rendered,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )


async def _open_result(
    client, message: Message, index: int, result: dict | None = None
) -> None:
    state = _state.get(message.chat.id)
    if result is None and state:
        result = state["results"].get(index)
    if result is None:
        if state and state["results"]:
            valid = sorted(state["results"])
            if index < valid[0]:
                hint = f"The earliest saved result is {valid[0]}."
            elif index > valid[-1]:
                hint = f"The latest saved result is {valid[-1]}."
            else:
                hint = "That result number is not available."
        else:
            hint = (
                f"Start with <code>{config.CMD_PREFIX}web &lt;query&gt;</code>."
            )
        await message.edit(
            f"😿 No saved result {index}. {hint}", parse_mode=ParseMode.HTML
        )
        return

    await message.edit(
        f"📄 Opening <b>{_esc(result['title'])}</b>…",
        parse_mode=ParseMode.HTML,
    )
    try:
        text = await _fetch_text(result["url"])
    except Exception as exc:
        logger.error(f"[WEB] open failed: {exc}")
        snippet = result.get("snippet") or "No search snippet was provided."
        await message.edit(
            f"⚠️ Could not fetch the full page ({_esc(type(exc).__name__)}). "
            f"Showing the search result instead.\n\n"
            f"<b>{_esc(result['title'])}</b>\n{_esc(result['url'])}\n\n"
            f"<blockquote expandable>{_esc(snippet)}</blockquote>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    if not text:
        await message.edit(
            f"😿 No readable text at {_esc(result['url'])}.",
            parse_mode=ParseMode.HTML,
        )
        return

    await _send_page_content(
        client, message, result["title"], result["url"], text
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
    await _open_result(client, message, int(arg))


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
            f"Usage: <code>{config.CMD_PREFIX}read</code> https://example.com",
            parse_mode=ParseMode.HTML,
        )
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    await message.edit(
        f"📄 Reading {_esc(url)}…", parse_mode=ParseMode.HTML
    )
    try:
        text = await _fetch_text(url)
    except Exception as exc:
        logger.error(f"[WEB] read failed: {exc}")
        await message.edit(
            f"❌ Failed to read {_esc(url)}\n"
            f"<code>{_esc(type(exc).__name__)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not text:
        await message.edit(
            f"😿 No readable text at {_esc(url)}.",
            parse_mode=ParseMode.HTML,
        )
        return

    await _send_page_content(client, message, "Web page", url, text)


@main.app.on_message(filters.me & filters.reply & filters.regex(r"^\s*\d+\s*$"))
async def number_reply(client, message: Message):
    """Reply to a results message with a number to open that result."""
    reply = message.reply_to_message
    if not reply:
        return
    results = _msg_results.get(reply.id)
    if not results:
        return
    index = int(message.text.strip())
    result = results.get(index)
    if result is None:
        valid = sorted(results)
        await message.edit(
            f"❌ This page has results {valid[0]}–{valid[-1]}.",
            parse_mode=ParseMode.HTML,
        )
        return
    await _open_result(client, message, index, result)
