import html
import re
from datetime import datetime, timezone
from typing import Optional

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


def _build_chat_link(chat_id: int) -> str:
    return f"https://t.me/c/{_get_chat_link_id(chat_id)}"


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


def create_label(name: str = TODOIST_LABEL_NAME, color: str = TODOIST_LABEL_COLOR):
    global LABEL
    labels = API.get_labels()
    for label in _iter_items(labels):
        if getattr(label, "name", "") == name:
            LABEL = label
            return

    LABEL = API.add_label(name=name, color=color, item_order=0, is_favorite=True)
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
        priority = int(priority_matches[-1])

    content_without_priority = PRIORITY_PATTERN.sub(" ", raw_content)

    due_string = None
    due_matches = DUE_PATTERN.findall(content_without_priority)
    if due_matches:
        due_string = due_matches[-1].strip()

    content_without_tags = DUE_PATTERN.sub(" ", content_without_priority)
    content = " ".join(content_without_tags.split()).strip()
    return content, priority, due_string


def build_description(message: Message) -> Optional[str]:
    reply = message.reply_to_message
    if not reply:
        return None

    chat_id = message.chat.id
    chat_title = message.chat.title or message.chat.first_name or "Chat"
    chat_link = _build_chat_link(chat_id)
    message_link = _build_message_link(chat_id, message.id)
    replied_message_link = _build_message_link(chat_id, reply.id)

    replied_text = _extract_message_text(reply).strip()
    replied_html = (
        html.escape(replied_text) if replied_text else "<i>(non-text message)</i>"
    )
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return (
        f'Chat [Task originates from] : <a href="{chat_link}">'
        f"{html.escape(chat_title)} #{chat_id}</a>\n"
        f"Message [Task cmd message] : #{message.id} - {message_link}\n\n"
        "=== Replied Message (If any) ===\n"
        f'<a href="{replied_message_link}">Message #{reply.id}</a>\n\n'
        "=========================================\n\n"
        f"{replied_html}\n\n"
        "=========================================\n\n\n"
        f"Task added on : {timestamp_utc}"
    )


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
        logger.error(f"[TODOIST] Failed to log error to Saved Messages: {log_error}")


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
            "`#pN` : specify task priority in content eg. #p4 for very urgent\n"
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
            "`#pN` : specify task priority in content eg. #p4 for very urgent\n"
            "`#due |...|` : specify due date ...."
        )
        return

    try:
        ensure_initialized()
        description = build_description(message)

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

        API.add_task(**create_task_kwargs)
        await send_result_feedback(client, message, success=True)
    except Exception as exc:
        logger.error(f"[TODOIST] Failed to create task: {exc}", exc_info=True)
        await log_error_to_saved_messages(
            client,
            context=f"chat={message.chat.id} message={message.id}",
            error=exc,
        )
        await send_result_feedback(client, message, success=False)


initialize_todoist()
