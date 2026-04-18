import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from pyrogram import filters
from pyrogram.types import Message
from todoist_api_python.api import TodoistAPI

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

SECTION = None
LABEL = None
PROJECT = None
API = None
INIT_ERROR = None

TODOIST_SECTION_NAME = "📩 From Telegram [UserBot]"
TODOIST_LABEL_NAME = "📩 From Telegram [UserBot]"
TODOIST_LABEL_COLOR = "blue"

PRIORITY_PATTERN = re.compile(r"(?i)(?:^|\s)#p([1-4])\b")
DUE_PATTERN = re.compile(r"(?i)(?:^|\s)#due\s*\|([^|]+)\|")
PRIORITY_MAP = {1: 4, 2: 3, 3: 2, 4: 1}
MAX_TASK_CONTENT_LEN = 500
MAX_TASK_DESCRIPTION_LEN = 16383


def _iter_items(payload):
    for item in payload:
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                yield nested
            continue
        yield item


def _get_chat_link_id(chat_id: int) -> str:
    chat_id_text = str(chat_id)
    if chat_id_text.startswith("-100"):
        return chat_id_text[4:]
    return chat_id_text.lstrip("-")


def _build_message_link(chat_id: int, message_id: int) -> str:
    return f"https://t.me/c/{_get_chat_link_id(chat_id)}/{message_id}"


def _extract_message_text(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return ""


def find_project():
    global PROJECT
    projects = API.get_projects()
    for project in _iter_items(projects):
        if getattr(project, "is_inbox_project", False):
            PROJECT = project
            return
    raise RuntimeError("Todoist inbox project was not found")


def create_section(name: str = TODOIST_SECTION_NAME):
    global SECTION
    sections = API.get_sections()
    for section in _iter_items(sections):
        if getattr(section, "name", "") == name:
            SECTION = section
            return

    SECTION = API.add_section(name=name, project_id=PROJECT.id)
    logger.info(f"[TODOIST] Created section: {SECTION}")


def create_label(
    name: str = TODOIST_LABEL_NAME, color: str = TODOIST_LABEL_COLOR
):
    global LABEL
    labels = API.get_labels()
    for label in _iter_items(labels):
        if getattr(label, "name", "") == name:
            LABEL = label
            return

    LABEL = API.add_label(
        name=name, color=color, item_order=0, is_favorite=True
    )
    logger.info(f"[TODOIST] Created label: {LABEL}")


def initialize_todoist():
    global API, INIT_ERROR, PROJECT, SECTION, LABEL
    INIT_ERROR = None

    if not config.TODOIST_API_TOKEN:
        INIT_ERROR = "TODOIST_API_TOKEN is missing"
        logger.warning("[TODOIST] TODOIST_API_TOKEN is not configured")
        return

    try:
        API = TodoistAPI(config.TODOIST_API_TOKEN)
        PROJECT = None
        SECTION = None
        LABEL = None
        find_project()
        create_section()
        create_label()
        logger.info("[TODOIST] Initialization completed")
    except Exception as exc:
        INIT_ERROR = str(exc)
        logger.error(f"[TODOIST] Initialization failed: {exc}", exc_info=True)


def ensure_initialized():
    if API and PROJECT and SECTION and LABEL and not INIT_ERROR:
        return
    initialize_todoist()
    if INIT_ERROR:
        raise RuntimeError(INIT_ERROR)


def parse_todo_input(raw_content: str):
    priority = None
    priority_matches = PRIORITY_PATTERN.findall(raw_content)
    if priority_matches:
        parsed_priority = int(priority_matches[-1])
        priority = PRIORITY_MAP.get(parsed_priority)

    content_without_priority = PRIORITY_PATTERN.sub(" ", raw_content)

    due_string = None
    due_matches = DUE_PATTERN.findall(content_without_priority)
    if due_matches:
        due_string = due_matches[-1].strip()

    content_without_tags = DUE_PATTERN.sub(" ", content_without_priority)
    content = " ".join(content_without_tags.split()).strip()
    return content, priority, due_string


def _build_reply_sender_line(reply: Message) -> str:
    sender = getattr(reply, "from_user", None)
    if sender:
        full_name = sender.first_name or ""
        if sender.last_name:
            full_name = f"{full_name} {sender.last_name}".strip()
        line = f"Sender: {full_name} #{sender.id}"
        if sender.username:
            line += f" @{sender.username}"
        return line

    sender_chat = getattr(reply, "sender_chat", None)
    if sender_chat:
        return f"Sender: {sender_chat.title or 'Unknown'} #{sender_chat.id}"

    return "Sender: Unknown"


def build_description(message: Message) -> Optional[str]:
    reply = message.reply_to_message
    if not reply:
        return None

    chat_id = message.chat.id
    chat_title = message.chat.title or message.chat.first_name or "Chat"
    message_link = _build_message_link(chat_id, message.id)
    replied_message_link = _build_message_link(chat_id, reply.id)
    sender_line = _build_reply_sender_line(reply)

    replied_text = _extract_message_text(reply).strip()
    replied_content = replied_text if replied_text else "(non-text message)"
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    description_prefix = (
        f"Chat [Task originates from] : {chat_title} #{chat_id}\n"
        f"Message [Task cmd message] : [Message #{message.id}]({message_link})\n\n"
        "=== Replied Message (If any) ===\n"
        f"[Message #{reply.id}]({replied_message_link})\n\n"
        f"{sender_line}\n\n"
        "=========================================\n\n"
    )
    description_suffix = (
        "\n\n"
        "=========================================\n\n\n"
        f"Task added on : {timestamp_utc}"
    )

    max_replied_len = (
        MAX_TASK_DESCRIPTION_LEN
        - len(description_prefix)
        - len(description_suffix)
    )
    if max_replied_len < 0:
        max_replied_len = 0

    if len(replied_content) > max_replied_len:
        truncation_suffix = "\n... (truncated)"
        keep_len = max_replied_len - len(truncation_suffix)
        if keep_len > 0:
            replied_content = (
                replied_content[:keep_len].rstrip() + truncation_suffix
            )
        else:
            replied_content = replied_content[:max_replied_len]

    description = description_prefix + replied_content + description_suffix
    return description


def _format_todoist_error(error: Exception) -> str:
    if not isinstance(error, httpx.HTTPStatusError):
        return f"{type(error).__name__}: {error}"

    status = "unknown"
    body = ""
    try:
        status = str(error.response.status_code)
        body = error.response.text or ""
    except Exception:
        pass

    if len(body) > 1200:
        body = body[:1200].rstrip() + "... (truncated)"
    if body:
        return f"HTTP {status}: {body}"
    return f"HTTP {status}: {error}"


async def log_error_to_saved_messages(client, context: str, error: Exception):
    try:
        await client.send_message(
            "me",
            (
                "[TODOIST ERROR]\n"
                f"Context: {context}\n"
                f"Error: {type(error).__name__}: {error}"
            ),
        )
    except Exception as log_error:
        logger.error(
            f"[TODOIST] Failed to log error to Saved Messages: {log_error}"
        )


async def send_result_feedback(client, message: Message, success: bool):
    reaction = "👍" if success else "👎"
    fallback_text = "Done" if success else "Failed"

    try:
        if hasattr(message, "react"):
            await message.react(reaction)
            return

        if hasattr(client, "send_reaction"):
            await client.send_reaction(message.chat.id, message.id, reaction)
            return

        raise RuntimeError("Reaction API is unavailable")
    except Exception as reaction_error:
        logger.warning(f"[TODOIST] Reaction failed: {reaction_error}")
        try:
            await message.reply_text(fallback_text)
        except Exception as reply_error:
            logger.error(f"[TODOIST] Feedback reply failed: {reply_error}")


@main.app.on_message(
    filters.me & filters.command("todo", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def todo_command(client, message: Message):
    raw_text = _extract_message_text(message).strip()
    raw_content = raw_text.split(maxsplit=1)

    if len(raw_content) < 2:
        await message.edit(
            "Usage:\n"
            "`.todo {content} + reply`\n\n"
            "Help :\n\n"
            "Reply : add context in description\n"
            "`#pN` : priority, #p1->very urgent, urgent, normal, low\n"
            "`#due |...|` : specify due date ...."
        )
        return

    parsed_content, priority, due_string = parse_todo_input(raw_content[1])
    if not parsed_content:
        await message.edit(
            "Usage:\n"
            "`.todo {content} + reply`\n\n"
            "Help :\n\n"
            "Reply : add context in description\n"
            "`#pN` : priority, #p1->very urgent, urgent, normal, low\n"
            "`#due |...|` : specify due date ...."
        )
        return
    if len(parsed_content) > MAX_TASK_CONTENT_LEN:
        await message.reply_text("title is more than 500 char")
        await send_result_feedback(client, message, success=False)
        return

    try:
        ensure_initialized()
        description = build_description(message)
        todo_message_link = _build_message_link(message.chat.id, message.id)

        create_task_kwargs = {
            "content": parsed_content,
            "section_id": SECTION.id,
            "labels": [LABEL.name],
        }
        if description:
            create_task_kwargs["description"] = description
        if priority:
            create_task_kwargs["priority"] = priority
        if due_string:
            create_task_kwargs["due_string"] = due_string
            create_task_kwargs["due_lang"] = "en"

        API.add_task(**create_task_kwargs)
        await send_result_feedback(client, message, success=True)
    except Exception as exc:
        error_details = _format_todoist_error(exc)
        logger.error(
            f"[TODOIST] Failed to create task: {error_details}", exc_info=True
        )
        await log_error_to_saved_messages(
            client,
            context=(
                f"chat={message.chat.id} message={message.id}\n"
                f"todo_message={todo_message_link}"
            ),
            error=RuntimeError(error_details),
        )
        await send_result_feedback(client, message, success=False)


initialize_todoist()
