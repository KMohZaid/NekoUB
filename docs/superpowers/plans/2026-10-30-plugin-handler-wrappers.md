# Shared Plugin Handler Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify `core/plugins/*` so every managed plugin event uses one straightforward shared wrapper, while preserving current behavior and the `h.*` authoring API.

**Architecture:** Keep decorators declarative: they attach event/options metadata. The manager binds each plugin method and declaration to one shared runtime wrapper, then creates/registers the matching Pyrogram handler with Pyrogram filters. `Plugin.declarations()` and `PluginRegistry.register()` remain compatible; the registry does not become a new execution layer.

**Tech Stack:** Python, Pyrogram/Kurigram handlers and filters, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-plugin-handler-wrappers-design.md`

## Global Constraints

- Preserve `@h.command(...)` and existing event decorators, plugin authoring API, and current plugins.
- Preserve command parsing, sudo/share/grant authorization, owner-only/public handling, context helpers, exception logging, propagation, and user/bot client separation.
- Pyrogram evaluates registered filters. Preserve command filter composition: owner command filter OR delegated command filter, combined with any custom filter.
- Preserve `Plugin.declarations()` and `PluginRegistry.register()` behavior and return shapes.
- Preserve existing handler keyword behavior; do not forward previously ignored handler kwargs as incidental scope.
- Preserve staged activation/reload cleanup and current best-effort rollback behavior, including logging restoration failures; do not claim stronger atomicity.
- Keep the wrapper linear with ordinary `if`/`else`; no central dispatcher and no wrapper-per-event permission implementations.
- Add concise comments only for behavior whose lifecycle/propagation cannot be made self-evident.

## Review Focus

- A decorator stacked multiple times on one method must still register each declaration and bind the correct event/options to the shared wrapper. (Task 1.)
- A command with both a delegated prefix and a custom filter must retain the current grouped filter semantics; direct invocation must not bypass access authorization. (Task 2.)
- Bot and user handlers must receive the correct client in both the handler call and `PluginContext`, including guest-message handlers. (Task 2.)
- Failed activation/reload after one or more registrations must leave registrations and active state consistent with current best-effort rollback behavior. (Task 3.)
- Exception handling and `ContinuePropagation` must remain consistent for successful calls, denied access, parse errors, and plugin exceptions. (Tasks 2–3.)

---

## File Structure

- `core/plugins/api.py`: retain public decorators and declaration compatibility; keep one shared handler invocation function with explicit readable branches.
- `core/plugins/manager.py`: retain discovery/validation/lifecycle responsibilities; bind declaration and method to the shared wrapper and build/register Pyrogram handlers.
- `core/plugins/registry.py`: keep compatibility registration/declaration storage; remove no public behavior and do not make it part of runtime dispatch.
- `tests/test_plugin_api.py`: wrapper/context/access/event behavior.
- `tests/test_plugin_manager.py`: handler construction, registration, reload, and rollback behavior.
- `tests/test_plugin_api.py`: existing `PluginRegistry` compatibility coverage (the repository has no separate `test_plugin_registry.py`).

## Interfaces

- Decorator API remains `h.command(...)`, `h.message(...)`, `h.edited_message(...)`, `h.callback_query(...)`, `h.inline_query(...)`, `h.bot_command(...)`, `h.bot_callback_query(...)`, `h.bot_inline_query(...)`, and `h.bot_guest_message(...)`.
- `Plugin.declarations() -> tuple[HandlerDeclaration, ...]` and `PluginRegistry.register(plugin) -> PluginDeclaration` remain callable with unchanged return shape.
- The shared runtime wrapper accepts a bound plugin method, event declaration, and runtime dependencies (clients, config, access store, owner id); exact smallest internal binding representation is a builder choice.
- The manager continues to construct Pyrogram handler classes from `HANDLER_TYPES` and applies filters before registration.

### Task 1: Make declaration-to-wrapper binding explicit

**Files:**
- Modify: `core/plugins/api.py`
- Modify: `core/plugins/manager.py`
- Test: `tests/test_plugin_api.py`
- Test: `tests/test_plugin_manager.py`

- [ ] **Step 1: Add failing tests for a shared wrapper binding each stacked declaration to its own method/event/options.** Assert that stacked decorators produce the same declaration count and that registered callbacks invoke the intended bound method with the declaration-specific event/context; preserve `Plugin.declarations()` return type and ordering behavior currently tested.
- [ ] **Step 2: Run the focused tests and verify they fail for the missing/incorrect wrapper binding behavior.**

Run: `pytest -q tests/test_plugin_api.py tests/test_plugin_manager.py`

Expected: focused regression assertion fails before implementation; unrelated tests pass.

- [ ] **Step 3: Implement the minimum direct binding path.** Use one shared runtime wrapper function for all declarations. Keep decorator metadata declarative and preserve the public declaration inspection API. The manager binds the original method and declaration when registering each handler; avoid introducing a second runtime dispatch table.
- [ ] **Step 4: Run the focused tests.**

Run: `pytest -q tests/test_plugin_api.py tests/test_plugin_manager.py`

Expected: PASS.

- [ ] **Step 5: Commit.**

Commit message: `refactor(plugins): bind declarations to shared handler wrapper`

### Task 2: Preserve authorization, filters, parsing, contexts, and propagation in the shared wrapper

**Files:**
- Modify: `core/plugins/api.py`
- Modify: `core/plugins/manager.py`
- Test: `tests/test_plugin_api.py`
- Test: `tests/test_plugin_manager.py`
- Test: `tests/test_managed_access.py`

- [ ] **Step 1: Add failing behavioral tests** for delegated sudo/share/grant allow and deny paths, owner/public-only bot handlers, command parsing and parse errors, custom filter composition, user/bot client and context selection, plugin exceptions, and `ContinuePropagation`.
- [ ] **Step 2: Run the focused tests and verify the new assertions fail for the intended behavior.**

Run: `pytest -q tests/test_plugin_api.py tests/test_plugin_manager.py tests/test_managed_access.py`

Expected: newly added assertions fail before implementation, without collection/setup errors.

- [ ] **Step 3: Move runtime behavior into the one shared wrapper with ordinary conditionals.** Keep filter creation/evaluation in Pyrogram/manager as specified; wrapper owns shared access checks, context/argument construction, method invocation, logging, and propagation. Do not change filter grouping or handler kwargs.
- [ ] **Step 4: Run focused tests.**

Run: `pytest -q tests/test_plugin_api.py tests/test_plugin_manager.py tests/test_managed_access.py`

Expected: PASS.

- [ ] **Step 5: Commit.**

Commit message: `refactor(plugins): preserve handler behavior in shared wrapper`

### Task 3: Remove redundant runtime indirection and verify lifecycle compatibility

**Files:**
- Modify: `core/plugins/api.py`
- Modify: `core/plugins/manager.py`
- Modify: `core/plugins/registry.py` only if a redundant runtime use remains
- Test: `tests/test_plugin_api.py`
- Test: `tests/test_plugin_manager.py`
- Test: `tests/test_plugin_api.py` (contains current registry compatibility coverage)

- [ ] **Step 1: Add failing regression tests** for `PluginRegistry.register()` compatibility, staged activation cleanup, reload success/failure rollback, and deactivation using the shared wrapper registration shape.
- [ ] **Step 2: Run focused lifecycle tests and verify the new assertions fail for the intended reason.**

Run: `pytest -q tests/test_plugin_api.py tests/test_plugin_manager.py`

Expected: missing/inconsistent lifecycle behavior is shown by failing assertions.

- [ ] **Step 3: Remove only runtime indirection made redundant by the shared wrapper.** Keep compatibility registry APIs and declaration access intact. Add brief explanatory comments only around unavoidable rollback or propagation behavior.
- [ ] **Step 4: Run targeted and full tests.**

Run: `pytest -q tests/test_plugin_api.py tests/test_plugin_manager.py tests/test_managed_access.py && pytest -q`

Expected: both commands PASS.

- [ ] **Step 5: Check formatting and commit.**

Run: `git diff --check`

Expected: no output and exit 0.

Commit message: `refactor(plugins): simplify managed handler lifecycle`
