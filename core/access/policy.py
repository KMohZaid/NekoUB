"""Route decisions shared by command dispatch and access-aware help."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    route: str
    is_owner: bool


async def authorize_command(
    *,
    store: Any,
    user_id: int | None,
    owner_id: int | None,
    prefix: str,
    command: str,
    sudo: bool = False,
    share: bool = False,
    grantable: bool = True,
) -> AccessDecision:
    """Authorize a command on its explicitly requested prefix route."""
    if user_id is None:
        return AccessDecision(False, "", False)
    if owner_id is not None and user_id == owner_id:
        return AccessDecision(prefix == ".", "owner", True)

    route = {"#": "sudo", "?": "share"}.get(prefix)
    if route is None or store is None:
        return AccessDecision(False, route or "", False)
    try:
        if prefix == "#":
            allowed = sudo and await store.is_sudoer(user_id)
        else:
            has_grant = grantable and await store.has_grant(user_id, command)
            has_share_role = share and await store.is_share_user(user_id)
            allowed = has_grant or has_share_role
    except Exception:
        return AccessDecision(False, route, False)
    return AccessDecision(bool(allowed), route, False)
