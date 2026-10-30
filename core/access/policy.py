"""Route decisions shared by command dispatch and access-aware help."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


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
    owner_prefixes: Sequence[str] = (".", "$"),
    delegated_prefixes: Sequence[str] = (",", "#", "?"),
) -> AccessDecision:
    """Authorize a command on its explicitly requested prefix route."""
    if user_id is None:
        return AccessDecision(False, "", False)
    if owner_id is not None and user_id == owner_id:
        return AccessDecision(prefix in owner_prefixes, "owner", True)

    route = "delegated" if prefix in delegated_prefixes else None
    if route is None or store is None:
        return AccessDecision(False, route or "", False)
    try:
        is_sudoer = sudo and await store.is_sudoer(user_id)
        is_share_user = share and await store.is_share_user(user_id)
        has_grant = grantable and await store.has_grant(user_id, command)
        allowed = is_sudoer or is_share_user or has_grant
        route = (
            "sudo" if is_sudoer else "share" if is_share_user else "grant"
            if has_grant else "delegated"
        )
    except Exception:
        return AccessDecision(False, route, False)
    return AccessDecision(bool(allowed), route, False)
