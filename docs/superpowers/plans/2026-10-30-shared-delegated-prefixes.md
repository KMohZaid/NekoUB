# Shared Delegated Prefixes Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Configure `.` and `$` as owner prefixes, and `,`, `#`, and `?` as interchangeable sudo/share prefixes while preserving role-based authorization.

**Architecture:** Add configured prefix strings and pass both groups into managed plugin dispatch. Normalize delegated authorization so any delegated marker checks the caller's actual sudo/share role or explicit command grant. Reflect the available prefixes in help metadata and cover policy/filter behavior with tests.

**Tech Stack:** Python, Pyrogram/Kurigram filters, SQLAlchemy-backed access policy, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-shared-delegated-prefixes-design.md`

## Global Constraints

- `CMD_PREFIX` defaults to `".$"`.
- `CMD_PREFIX_FOR_SUDO_AND_SHARE` defaults to `",#?"`.
- Only commands with declared `sudo`, `share`, or `grantable` access metadata use delegated prefixes.
- A prefix does not grant a role; authorization remains role/grant based.

## Review Focus

- Prefix-only commands still dispatch for the owner and remain unavailable to other users.
- Both sudo and share roles can use their authorized commands under every delegated prefix.
- A user with neither role can still use a command only through an explicit grant when it is grantable.
- Custom prefix strings retain their existing character-per-prefix behavior.
- Help route labels do not imply a role is authorized merely because a prefix is accepted.

---

### Task 1: Prefix configuration and filter matching

**Files:** `config.py`, `main.py`, `core/plugins/manager.py`, `core/plugins/api.py`, `tests/test_plugin_manager.py`

- [ ] Add `CMD_PREFIX` default `".$"` and `CMD_PREFIX_FOR_SUDO_AND_SHARE` default `",#?"`.
- [ ] Pass delegated prefix config into plugin manager and use it for filter registration and option parsing, while ordinary owner filters continue to use `CMD_PREFIX`.
- [ ] Test filters accept each configured prefix for a command declaring delegated access, while a command with no delegated metadata remains owner-only.
- [ ] Run focused manager tests.

### Task 2: Role-based authorization across delegated prefixes

**Files:** `core/access/policy.py`, `core/plugins/api.py`, `tests/test_managed_access.py`, `tests/test_access_dispatch.py`

- [ ] Make every configured delegated marker evaluate sudo role, share role, and explicit command grant consistently; commands declaring both sudo and share accept either corresponding role.
- [ ] Test all prefixes against sudo-only, share-only, both-role, grant-only, and unauthorized callers.
- [ ] Test owner route accepts each configured normal prefix and preserves owner-only behavior.
- [ ] Run focused access tests.

### Task 3: Help output and full verification

**Files:** `plugins/help.py`, `tests/test_access_help.py`, `tests/test_plugin_manager.py`

- [ ] Render role/grant-specific usable prefix examples from configured prefix strings without changing authorization semantics.
- [ ] Add tests for generated help metadata and custom configured prefixes.
- [ ] Run the full test suite and `git diff --check`; commit only task changes and this plan/spec if required by the user request.
