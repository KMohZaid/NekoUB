"""Owner-managed sudo/share roles and sudo-managed command grants."""

from __future__ import annotations

import html
import re
from typing import Any

from core.plugins import Plugin, handlers as h


_COMMAND_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def parse_command_names(value: str) -> list[str]:
    """Parse a comma-separated list of command names and optional prefixes."""
    names = [part.strip().lstrip(".#?").casefold() for part in value.split(",")]
    if not names or any(not name or not _COMMAND_NAME.fullmatch(name) for name in names):
        raise ValueError("Provide comma-separated command names")
    return list(dict.fromkeys(names))


def canonicalize_command_names(names: list[str], commands: list[dict]) -> list[str]:
    """Resolve command aliases to their declared trigger for grant storage."""
    aliases = {
        alias.casefold(): command["trigger"].casefold()
        for command in commands
        for alias in command.get("aliases", ())
    }
    triggers = {
        command["trigger"].casefold()
        for command in commands
    }
    return list(dict.fromkeys(aliases.get(name, name) if name not in triggers else name for name in names))


async def resolve_target_user(client: Any, message: Any, target: str | None) -> Any:
    """Resolve a replied-to user, numeric ID, or Telegram username."""
    reply = getattr(message, "reply_to_message", None) if message is not None else None
    replied_user = getattr(reply, "from_user", None)
    if replied_user is not None and getattr(replied_user, "id", None) is not None:
        return replied_user
    if target is None or not target.strip():
        raise ValueError("Reply to a user's message or provide their username/user ID")

    target = target.strip()
    if target.lstrip("-").isdigit():
        user_id = int(target)
        try:
            users = await client.get_users(user_id)
        except Exception:
            users = None
        if isinstance(users, (list, tuple)):
            users = users[0] if users else None
        if users is not None and getattr(users, "id", None) is not None:
            return users
        return type(
            "ResolvedUser",
            (),
            {"id": user_id, "first_name": None, "last_name": None},
        )()
    username = target.removeprefix("@").strip()
    if not username or not _COMMAND_NAME.fullmatch(username):
        raise ValueError("Invalid username or user ID")
    users = await client.get_users(username)
    if isinstance(users, (list, tuple)):
        if not users:
            raise ValueError("Could not resolve that username")
        users = users[0]
    user_id = getattr(users, "id", None)
    if user_id is None:
        raise ValueError("Could not resolve that username")
    return users


async def resolve_target_user_id(client: Any, message: Any, target: str | None) -> int:
    """Resolve a target using the shared User-returning resolver."""
    user = await resolve_target_user(client, message, target)
    return int(user.id)


def _user_list_text(title: str, users: list[Any]) -> str:
    if not users:
        return f"{title}: none"

    lines = [f"{title}:"]
    for user in users:
        user_id = user.user_id
        name = " ".join(
            part for part in (user.first_name, user.last_name) if part
        ).strip()
        label = html.escape(name or str(user_id))
        lines.append(
            f'<a href="tg://user?id={user_id}">{label}</a> '
            f"(ID: <code>{user_id}</code>)"
        )
    return "\n".join(lines)


async def _ignored_chat_list_text(ctx: Any) -> str:
    store = ctx.get_config("ACCESS_STORE")
    if store is None:
        return "Ignored chats: none"
    chats = await store.list_ignored_chats()
    if not chats:
        return "Ignored chats: none"

    lines = []
    for chat in chats:
        chat_id = chat.chat_id
        internal_id = str(chat_id).removeprefix("-100")
        title = html.escape(chat.title or str(chat_id))
        lines.append(
            f"<b>{title}</b> (ID: <code>{chat_id}</code>) "
            f'(<a href="https://t.me/c/{internal_id}/1">First msg</a>)'
        )
    return "\n".join(lines)


async def _grant_list_text(ctx: Any) -> str:
    store = ctx.get_config("ACCESS_STORE")
    if store is None:
        return "Granted commands: none"
    grants = await store.list_grants_by_user()
    if not grants:
        return "Granted commands: none"

    lines = []
    for user_id, first_name, last_name, commands in grants:
        name = " ".join(part for part in (first_name, last_name) if part).strip()
        label = html.escape(name or str(user_id))
        lines.append(
            f'<a href="tg://user?id={user_id}">{label}</a> '
            f"(ID: <code>{user_id}</code>)"
        )
        lines.append("- " + ", ".join(html.escape(command) for command in commands))
    return "\n".join(lines)


class SudoAndSharePlugin(Plugin):
    id = "sudo_and_share"
    category = "Core"
    description = "Manage sudo, share, and per-command access."

    async def _store(self, ctx: Any) -> Any:
        store = ctx.get_config("ACCESS_STORE")
        if store is None:
            await ctx.send("Access database is unavailable.", reply_to=ctx.update)
        return store

    @h.command("ignorechat", sudo=True, description="Ignore commands in this chat for share users and grants.")
    async def ignore_chat(self, client, message, ctx, args):
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            await ctx.send("Could not determine this chat.", reply_to=message)
            return
        store = await self._store(ctx)
        if store is None:
            return
        added = await store.add_ignored_chat(chat_id, getattr(chat, "title", None))
        await ctx.send(
            f"{'Ignored' if added else 'Already ignored'}: <code>{chat_id}</code>",
            reply_to=message,
        )

    @h.command("dontignorechat", sudo=True, description="Allow share users and grants in this chat again.")
    async def dont_ignore_chat(self, client, message, ctx, args):
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        if chat_id is None:
            await ctx.send("Could not determine this chat.", reply_to=message)
            return
        store = await self._store(ctx)
        if store is None:
            return
        removed = await store.remove_ignored_chat(chat_id)
        await ctx.send(
            f"{'No longer ignoring' if removed else 'Chat was not ignored'}: <code>{chat_id}</code>",
            reply_to=message,
        )

    @h.command("ignorechatlist", sudo=True, description="List ignored chats.")
    async def ignore_chat_list(self, client, message, ctx, args):
        store = await self._store(ctx)
        if store is None:
            return
        text = await _ignored_chat_list_text(ctx)
        if ctx.actor_is_owner:
            await ctx.edit(text, message=message)
        else:
            await ctx.send(text, reply_to=message)

    @h.command("grantlist", sudo=True, description="List users and their granted commands.")
    async def grant_list(self, client, message, ctx, args):
        store = await self._store(ctx)
        if store is None:
            return
        text = await _grant_list_text(ctx)
        if ctx.actor_is_owner:
            await ctx.edit(text, message=message)
        else:
            await ctx.send(text, reply_to=message)

    async def _target(self, client: Any, message: Any, args: Any) -> Any:
        tail = " ".join(args.positional).strip()
        return await resolve_target_user(client, message, tail or None)

    @h.command("addsudo", sudo=False, share=False, grantable=False, description="Add a sudo user.")
    async def add_sudo(self, client, message, ctx, args):
        try:
            user = await self._target(client, message, args)
            user_id = int(user.id)
            store = await self._store(ctx)
            if store is None:
                return
            added = await store.add_sudoer(
                user_id,
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
            )
            await ctx.send(f"{'Added' if added else 'Already a sudo user'}: <code>{user_id}</code>", reply_to=message)
        except Exception as error:
            await ctx.send(f"Could not add sudo user: {error}", reply_to=message)

    @h.command("rmsudo", sudo=False, share=False, grantable=False, description="Remove a sudo user.")
    async def remove_sudo(self, client, message, ctx, args):
        try:
            user = await self._target(client, message, args)
            user_id = int(user.id)
            store = await self._store(ctx)
            if store is None:
                return
            removed = await store.remove_sudoer(user_id)
            await ctx.send(f"{'Removed' if removed else 'Not a sudo user'}: <code>{user_id}</code>", reply_to=message)
        except Exception as error:
            await ctx.send(f"Could not remove sudo user: {error}", reply_to=message)

    @h.command("sudolist", sudo=True, description="List sudo users.")
    async def sudo_list(self, client, message, ctx, args):
        store = await self._store(ctx)
        if store is None:
            return
        users = await store.list_sudoers()
        text = _user_list_text("Sudo users", users)
        if ctx.actor_is_owner:
            await ctx.edit(text, message=message)
        else:
            await ctx.send(text, reply_to=message)

    @h.command("addshare", sudo=True, description="Add a share user.")
    async def add_share(self, client, message, ctx, args):
        try:
            user = await self._target(client, message, args)
            user_id = int(user.id)
            store = await self._store(ctx)
            if store is None:
                return
            added = await store.add_share_user(
                user_id,
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
            )
            await ctx.send(f"{'Added' if added else 'Already a share user'}: <code>{user_id}</code>", reply_to=message)
        except Exception as error:
            await ctx.send(f"Could not add share user: {error}", reply_to=message)

    @h.command("rmshare", sudo=True, description="Remove a share user.")
    async def remove_share(self, client, message, ctx, args):
        try:
            user = await self._target(client, message, args)
            user_id = int(user.id)
            store = await self._store(ctx)
            if store is None:
                return
            removed = await store.remove_share_user(user_id)
            await ctx.send(f"{'Removed' if removed else 'Not a share user'}: <code>{user_id}</code>", reply_to=message)
        except Exception as error:
            await ctx.send(f"Could not remove share user: {error}", reply_to=message)

    @h.command("sharelist", sudo=True, description="List share users.")
    async def share_list(self, client, message, ctx, args):
        store = await self._store(ctx)
        if store is None:
            return
        users = await store.list_share_users()
        text = _user_list_text("Share users", users)
        if ctx.actor_is_owner:
            await ctx.edit(text, message=message)
        else:
            await ctx.send(text, reply_to=message)

    @h.command("grant", sudo=True, description="Grant commands to a user.")
    async def grant_commands(self, client, message, ctx, args):
        await self._change_grants(client, message, ctx, args, revoke=False)

    @h.command("revoke", sudo=True, description="Revoke command grants.")
    async def revoke_commands(self, client, message, ctx, args):
        await self._change_grants(client, message, ctx, args, revoke=True)

    async def _change_grants(self, client, message, ctx, args, *, revoke: bool) -> None:
        try:
            positional = list(args.positional)
            reply = getattr(message, "reply_to_message", None)
            has_reply_target = reply is not None and getattr(reply, "from_user", None) is not None
            if len(positional) < (1 if has_reply_target else 2):
                raise ValueError("Usage: command user_id command[,command...]")
            if reply is not None and getattr(reply, "from_user", None) is not None:
                target = None
            else:
                target = positional.pop(0)
            user_id = await resolve_target_user_id(client, message, target)
            names = parse_command_names(" ".join(positional))
            from main import plugin_manager, private_plugin_manager

            commands = [
                command
                for manager in (plugin_manager, private_plugin_manager)
                if manager is not None
                for command in manager.command_help()
            ]
            names = canonicalize_command_names(names, commands)
            if not revoke:
                command_by_name = {
                    name.casefold(): command
                    for command in commands
                    for name in (command["trigger"], *command.get("aliases", ()))
                }
                blocked = [
                    name for name in names
                    if name in command_by_name
                    and not command_by_name[name].get("grantable", True)
                ]
                if blocked:
                    raise ValueError(
                        "Commands are not grantable: " + ", ".join(blocked)
                    )
            store = await self._store(ctx)
            if store is None:
                return
            results = []
            for name in names:
                changed = await (store.revoke(user_id, name) if revoke else store.grant(user_id, name))
                if revoke:
                    results.append(f"{name}: {'revoked' if changed else 'not granted'}")
                else:
                    results.append(f"{name}: {'granted' if changed else 'already granted'}")
            await ctx.send("\n".join(results), reply_to=message)
        except Exception as error:
            await ctx.send(f"Could not {'revoke' if revoke else 'grant'} commands: {error}", reply_to=message)

    @h.command("resetgrant", sudo=True, description="Remove all explicit command grants for a user.")
    async def reset_grants(self, client, message, ctx, args):
        try:
            user = await self._target(client, message, args)
            user_id = int(user.id)
            store = await self._store(ctx)
            if store is None:
                return
            count = await store.reset_grants(user_id)
            await ctx.send(f"Removed {count} explicit command grant(s) for <code>{user_id}</code>.", reply_to=message)
        except Exception as error:
            await ctx.send(f"Could not reset grants: {error}", reply_to=message)
