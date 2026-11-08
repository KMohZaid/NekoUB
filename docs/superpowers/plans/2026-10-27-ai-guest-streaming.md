# AI Guest-Chat Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.ai -s/--stream` that hands a prepared request to the assistant bot through Guest Chat, streams rich drafts, and falls back to the current inline response path after 7 seconds if Guest Chat does not claim the request.

**Architecture:** Extend the plugin API with a bot guest-message handler backed by Kurigram's `GuestMessageHandler`. Keep pending stream requests and atomic claims in `plugins/ai.py`; the userbot posts an opaque bot mention and races a 7-second claim deadline, while the bot streams the matching prepared request using OpenAI-compatible SSE over `httpx`. Use Kurigram rich draft updates for progress and `send_rich_message()` for final persistence.

**Tech Stack:** Python, asyncio, httpx, Kurigram 2.2.26, unittest.

**Spec:** `docs/superpowers/specs/2026-10-27-ai-guest-streaming-design.md`

## Global Constraints

- The 7-second timeout path must use the existing non-streaming request and inline-result delivery, not make a duplicate provider request.
- Pending request ownership must be a single atomic state transition; requests are single-use and bound to the source chat.
- Rich drafts are ephemeral previews with a 30-second lifetime; persist the complete output with `send_rich_message()`.
- On stream errors after output, persist partial output plus a collapsed `❌ Error occurred` explanation.
- Keep current provider configuration and `httpx`; do not add an LLM SDK.
- Include the exact Guest Chat Mode setup note specified in the spec in `.ai` option help.

## Review Focus

- Two concurrent updates or a timeout/update race claim one request at most once: test the claim transition and late trigger.
- Guest Chat events with wrong chat, malformed ID, or unknown ID are ignored: test trigger validation.
- Providers may emit split SSE frames, usage-only frames, `[DONE]`, or malformed JSON: test incremental parsing and graceful errors.
- A long/fast stream must keep the draft alive and avoid excessive updates: test draft update throttling and ensure updates continue before the 30-second expiry.
- Guest Chat disabled, absent assistant bot, or trigger send failure must not leave a permanently pending request: test fallback/cleanup paths.

---

### Task 1: Add typed bot guest-message handler support

**Files:**
- Modify: `core/plugins/api.py`
- Test: `tests/test_plugin_api.py`
- Test: `tests/test_plugin_manager.py`

**Interfaces:**
- Produces: `h.bot_guest_message(...)` declaration and `GuestMessageHandler` installation for assistant bot updates, using the same `PluginContext` convention as other `h.bot_*` handlers.

- [ ] **Step 1: Write failing tests**

Add tests that `@h.bot_guest_message()` declares an event and that `PluginManager.activate()` attaches the wrapped handler using Kurigram `GuestMessageHandler` on the bot client. Assert `ctx.client` is the userbot and `ctx.bot` is the assistant bot.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest discover -s tests -p 'test_plugin_api.py'` and `python -m unittest discover -s tests -p 'test_plugin_manager.py'`
Expected: FAIL because the decorator and handler installation are absent.

- [ ] **Step 3: Implement guest handler declaration and registration**

Add `GuestMessageHandler` import and a `guest_message` decorator in `core/plugins/api.py`. Match existing bot-event registration, bot-only validation, owner context construction, and handler filter behavior.

- [ ] **Step 4: Run focused tests**

Run the two test commands above.
Expected: PASS.

### Task 2: Add robust OpenAI-compatible SSE streaming

**Files:**
- Modify: `plugins/ai.py`
- Test: `tests/test_ai_inline.py`

**Interfaces:**
- Produces: `async def _stream_complete(messages: list, chat_id: int, on_chunk: Callable[[str], Awaitable[None]]) -> tuple[str | None, str | None, dict | None]`.
- Stream via `httpx.AsyncClient.stream("POST", ..., json={"model": model, "messages": messages, "stream": True})`; parse `data:` lines, deltas from `choices[0].delta.content`, optional usage metadata, and `[DONE]`. Continue to the next configured provider only when a provider fails before yielding output; after partial output return that output and an error without replaying the request.

- [ ] **Step 1: Write failing tests**

Test split SSE events, multiple content deltas, `[DONE]`, usage-only events, provider fallback before first content, and partial-content error without provider retry. Inject/mock async stream responses using `httpx` test transports or controlled async context managers.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: FAIL because `_stream_complete` is absent.

- [ ] **Step 3: Implement `_stream_complete`**

Keep the same provider order, headers, deferred-response behavior exclusions for streaming, timeout, and usage return contract. A 202 deferred response is not an SSE stream; return a clear stream unsupported/error result so caller can select the existing full-response path before any draft is finalized.

- [ ] **Step 4: Run focused AI tests**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: PASS.

### Task 3: Add stream option metadata and pending request ownership

**Files:**
- Modify: `plugins/ai.py`
- Test: `tests/test_ai_inline.py`

**Interfaces:**
- Produces: a typed `-s` / `--stream` `Option` in `.ai` help metadata; a pending-request registry keyed by opaque request ID; `_claim_stream_request(request_id, chat_id) -> PendingAIRequest | None`; request state that distinguishes pending, bot-owned, fallback-owned, and completed.

- [ ] **Step 1: Write failing tests**

Test help metadata for `stream`, short alias `-s`, long alias `--stream`, and the exact Guest Chat Mode note. Test `-s` with `-c N` parsing; successful claim once; wrong-chat, unknown, duplicate, and late claims return `None`; timeout fallback and bot claim cannot both win.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: FAIL for absent option and request registry/claim behavior.

- [ ] **Step 3: Implement metadata and claim state**

Add the stream `Option` and an example to `AIPlugin.ai_command`. Parse both option orders without consuming prompt text, preserving current `-c/--context` behavior. Add an asyncio-lock-protected registry with unpredictable IDs, source chat binding, single-claim transition, and bounded cleanup/expiry.

- [ ] **Step 4: Run focused AI tests**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: PASS.

### Task 4: Implement Guest Chat stream handling and rich draft finalization

**Files:**
- Modify: `plugins/ai.py`
- Test: `tests/test_ai_inline.py`

**Interfaces:**
- Consumes: Task 1 `@h.bot_guest_message` and Task 2 `_stream_complete`.
- Produces: a bot guest handler that validates exact `@username request-id` syntax, verifies sender/chat and pending request, atomically claims it, sends a brief thinking draft, streams updates via `bot.send_rich_message_draft(chat_id, draft_id, rich_message=InputRichMessage(markdown=...))`, and persists via `bot.send_rich_message(chat_id, rich_message=InputRichMessage(markdown=...))`.

- [ ] **Step 1: Write failing tests**

Test valid trigger dispatch, wrong bot mention and chat rejection, thinking draft, throttled chunk drafts with a stable nonzero draft ID, final rich-message persistence, empty/unsupported stream error finalization, and partial content plus `<details><summary>❌ Error occurred</summary>...` on stream failure.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: FAIL because no Guest Chat handler or draft delivery exists.

- [ ] **Step 3: Implement guest handler and draft delivery**

Use the assistant bot client from `ctx.bot`. Generate draft IDs using `bot.rnd_id()` when available, otherwise a positive random integer. Throttle draft updates to no more than one every 0.33 seconds, plus one final update before persistence. Limit/prepare content using the existing rich answer helpers. Treat the initial “thinking” draft as a short progress placeholder, never model reasoning.

- [ ] **Step 4: Run focused AI tests**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: PASS.

### Task 5: Wire `.ai -s` handoff and 7-second fallback

**Files:**
- Modify: `plugins/ai.py`
- Test: `tests/test_ai_inline.py`

**Interfaces:**
- Consumes: Task 3 pending request registry and claim helper; Task 4 Guest Chat handler.

- [ ] **Step 1: Write failing tests**

Test that `.ai -s` first assembles recent context and reply context, registers a prepared request, posts `@assistantusername <request-id>` as a reply to the command, and waits at most 7 seconds. Test bot claim wins without userbot `_complete`; timeout wins and uses `_complete` plus inline delivery; Guest Chat disabled or trigger send failure selects the safe full-response path; a late update after fallback is ignored. Ensure `.ai -c N -s` and `.ai -s -c N` work.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: FAIL because command flow does not hand off stream requests.

- [ ] **Step 3: Implement stream command handoff**

After prompt/history assembly and provider configuration checks, create the pending request, post the trigger with `message.reply_text`, and await claim/fallback for 7 seconds. On timeout, claim fallback atomically and use the existing `_complete` and inline path. Maintain history once, only for the winning path, and remove/expire request state on all terminal paths.

- [ ] **Step 4: Run focused AI tests**

Run: `python -m unittest discover -s tests -p 'test_ai_inline.py'`
Expected: PASS.

### Task 6: Full verification

**Files:**
- Test: all tests.

- [ ] **Step 1: Run full suite**

Run: `python -m unittest discover -s tests`
Expected: all tests pass.

- [ ] **Step 2: Check patch formatting and status**

Run: `git diff --check && git status --short`
Expected: no whitespace errors; only intended implementation, tests, design, and plan files changed.
