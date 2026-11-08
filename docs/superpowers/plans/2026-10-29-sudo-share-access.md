# Sudo and Share Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent sudo/share roles and standalone command grants to NekoUB, with `#`/`?` dispatch, safe delegated replies, and per-user help visibility.

**Architecture:** Add a SQLAlchemy/PostgreSQL access service with Alembic migrations and an idempotent startup check that provisions the configured database when absent. Integrate access checks into managed command dispatch and help metadata, then provide `.sudo_and_share` administration and opt the approved commands into sudo/share access. Keep owner-only legacy plugins unchanged unless explicitly opted in. Command declarations use `sudo`, `share`, and `grantable` (default true); explicit grants live only as per-user database rows and do not require a declaration-level `grant` attribute.

**Tech Stack:** Python, Kurigram/Pyrogram, SQLAlchemy, Alembic, PostgreSQL, pytest/unittest.

**Spec:** `docs/designs/sudo-and-share-access.md` (approved design, as presented in this session)

## Global Constraints

- Owner uses the configured normal `.` prefix; sudo uses `#`; share users or standalone command-grant users use `?`.
- A standalone grant is sufficient for that command and does not add a role; grants are per-user database rows, while `grantable` is a declaration-level eligibility flag that defaults to true.
- `.grant` adds grants; `.revoke` and `.resetgrant` remove only explicit grants and never role access.
- Unauthorized or wrong-prefix requests are silently ignored.
- Initial share commands are `.say` and `.tts`; initial sudo commands are `.eval` and `.exec`.
- Help shows unavailable commands as spoilers with a bold blockquote explaining that spoilered commands are unavailable to the viewer.
- Delegated users get replies/new messages, never edits/deletes of their command message.
- PostgreSQL is the source of truth. Any unavailable database must fail closed for delegated access.
- Callback identity checks bind clicker to initiator; callback identity never grants command access.
- Do not include credentials in logs, tests, docs, or command output.

## Review Focus

- A message with multiple leading prefix characters or an unauthorized alternate prefix must not reach a handler.
- A reply/mention/numeric target that cannot be resolved must not grant access to an unintended account.
- Concurrent startup attempts to create the same database must safely tolerate the duplicate-create race.
- Database connection or permission failures must not enable cached/stale delegated permissions.
- Delegated `.tts` must not delete a sender's command, and delegated `.eval`/`.exec` results must not edit that message.

---

### Task 1: PostgreSQL access store, schema, and migrations

**Files:**
- Create: `core/access/__init__.py`
- Create: `core/access/database.py`
- Create: `core/access/models.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/<initial_access_schema>.py`
- Modify: `config.py`, `.env.example`, `requirements.txt`
- Test: `tests/test_access_database.py`

**Interfaces:**
- Produces `AccessStore(database_url: str)`, async `initialize()`, async role/grant CRUD, and async context-managed `close()`; later tasks use this interface.
- Tables: `sudoers(user_id BIGINT PRIMARY KEY)`, `share_users(user_id BIGINT PRIMARY KEY)`, `allowed_cmds(user_id BIGINT, command TEXT, PRIMARY KEY(user_id, command))`.
- Config exposes `DATABASE_URL` from `.env`.

- [ ] Write tests for URL parsing, schema/migration contents, idempotent initialization, missing-database creation check, and duplicate-create race handling.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement SQLAlchemy async engine with `postgresql+asyncpg` URL normalization; implement database-existence check against maintenance database, safely quote identifiers, and handle concurrent duplicate creation without swallowing unrelated errors.
- [ ] Add Alembic migration for the three access tables. Startup provisions the target database when absent, then applies Alembic migrations; it does not attempt to provision the PostgreSQL server or roles.
- [ ] Add dependency pins and document `DATABASE_URL` in `.env.example` without including the live URL.
- [ ] Run focused tests, then commit as `feat(access): add postgres access store`.

### Task 2: Central access policy and managed command dispatch

**Files:**
- Modify: `core/plugins/api.py`
- Modify: `core/plugins/manager.py`
- Create: `core/access/policy.py`
- Modify: `core/plugins/context.py`
- Test: `tests/test_managed_access.py`

**Interfaces:**
- Consumes: Task 1 `AccessStore` role/grant query methods.
- Produces command declaration access metadata (`sudo`, `share`, `grantable`) and `PluginContext.actor_is_owner` / `actor_id`.

- [ ] Write tests for owner `.`, sudo `#`, share `?`, standalone grant `?`, silent rejection for unauthorized senders/wrong prefixes, overlapping roles, and database failure.
- [ ] Run focused tests and verify failures.
- [ ] Extend managed command metadata with `sudo`, `share`, and `grantable=True` by default; explicit per-user grants are database rows. Mark only commands that must not receive standalone grants with `grantable=False`.
- [ ] Remove owner-only sender filtering only for managed commands that opt into delegated routes; central dispatch checks role/grant eligibility, requested prefix, and route-specific command declaration before calling the handler.
- [ ] Populate caller identity in `PluginContext`; fail closed on database errors for non-owner callers and preserve owner command behavior.
- [ ] Run focused tests and commit as `feat(access): authorize managed command routes`.

### Task 3: Sudo/share management and standalone grant commands

**Files:**
- Create: `plugins/sudo_and_share.py`
- Modify: `core/access/database.py`
- Test: `tests/test_sudo_and_share.py`

**Interfaces:**
- Consumes: Task 1 `AccessStore`, Task 2 caller identity and owner/sudo route authorization.
- Commands: `.addsudo`, `.rmsudo`, `.sudolist`, `.addshare`, `.rmshare`, `.sharelist`, `.grant`, `.revoke`, `.resetgrant`.

- [ ] Write tests for reply/username/numeric-ID target resolution, role changes, comma-separated grants, duplicates, revoke of never-granted commands, and reset preserving sudo/share roles.
- [ ] Run focused tests and verify failures.
- [ ] Implement owner-only sudo/share role administration; permit sudo managers to grant/revoke/reset command grants, including powerful commands as approved in the spec. Treat usernames that cannot be resolved and malformed IDs as errors without changing state.
- [ ] Return per-command added/already-granted and revoked/not-granted results; role removal never changes explicit grants and grant removal never changes role membership.
- [ ] Run focused tests and commit as `feat(access): add sudo share and grant commands`.

### Task 4: Enable the approved commands with delegated-safe output

**Files:**
- Modify: `plugins/eval.py`, `plugins/exec.py`, `plugins/tts.py`
- Test: `tests/test_delegated_commands.py`

**Interfaces:**
- Consumes: Task 2 command route metadata and caller identity.
- Opt-ins: eval/exec sudo route; tts and its `say` alias share route.

- [ ] Write tests proving delegated eval/exec output is sent as a reply/new message and delegated TTS does not delete the command; test owner behavior remains unchanged.
- [ ] Run focused tests and verify failures.
- [ ] Declare sudo/share routes and adjust output paths so owner invocations preserve current edits/deletes while non-owner invocations send replies/new messages in the same chat.
- [ ] Run focused tests and commit as `feat(access): enable delegated eval exec and tts`.

### Task 5: Access-aware help and initiator-bound callbacks

**Files:**
- Modify: `plugins/help.py`
- Modify callback-data helpers/managed callback wrapper identified during implementation.
- Test: `tests/test_access_help.py`, `tests/test_callback_ownership.py`

**Interfaces:**
- Consumes: Task 2 policy API and Task 3 access roles/grants.
- Help must share runtime policy; user-bound buttons reject missing/malformed/stale/mismatched initiators.

- [ ] Write tests for command visibility on each route, spoiler markup for unavailable commands, bold blockquote footer, and callback clicker/initiator matching.
- [ ] Run focused tests and verify failures.
- [ ] Render the viewer’s effective command routes in existing help output; preserve existing help navigation and ensure delegated users only receive callback surfaces that use validated initiator binding.
- [ ] Run focused tests and commit as `feat(help): show access-aware commands`.

### Task 6: Configure local database URL and verify project

**Files:**
- Create or modify ignored `.env` in this worktree only.
- Modify documentation only if required for setup instructions.

**Interfaces:**
- Uses `DATABASE_URL` from Task 1. Never print, log, commit, or include the credential in test fixtures.

- [ ] Write/read no credential-bearing test output; confirm `.env` is ignored and mode is owner-only before storing the provided URL.
- [ ] Set the local `.env` database URL without showing its value in output; preserve other existing values if `.env` exists.
- [ ] Run migration tests and the complete test suite; do not connect to or create the remote database during implementation verification.
- [ ] Commit only tracked source, tests, and configuration templates as `feat(access): complete sudo and share access`.

## Final verification

- Run the full test suite and report failures exactly.
- Review the complete branch diff for accidental secret inclusion and confirm `.env` is ignored and untracked.
- Do not push, deploy, or run remote database provisioning without a separately authorized action.
