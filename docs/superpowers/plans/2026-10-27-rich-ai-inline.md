# Rich AI Inline Responses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `.ai` answers as assistant-bot inline results using Telegram Rich Markdown, leave the command message untouched, react with `👨‍💻` during processing, and rely on Telegram's native Show More behavior after approximately 4,000 visible characters.

**Architecture:** The userbot generates and stages the model response with a short-lived random key, requests an inline result from the assistant bot, and inserts it into the current chat using the existing help flow. Since installed Kurigram 2.2.26 lacks rich inline-result content, answer the bot inline query using Telegram Bot API `answerInlineQuery` with `InputRichMessageContent`, retaining Kurigram for query retrieval and result insertion.

**Tech Stack:** Python, Kurigram 2.2.26, httpx, Telegram Bot API, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-rich-ai-inline-design.md`

## Global Constraints

- Rich-message limit: 32,768 UTF-8 characters.
- Leave the first approximately 4,000 characters outside `<details>`; rely on Telegram's native “Show more” overflow UI, not manual truncation at 4,000.
- Use `<details open><summary>…</summary>…</details>` for useful sections shown expanded initially; reserve collapsed details for optional content and never collapse the whole answer.
- Add a best-effort `👨‍💻` processing reaction; do not edit the `.ai` command message; reaction failure must not block the response.
- Preserve Markdown and encourage all relevant Telegram Rich Markdown syntax, including styled URL buttons, tables, headings/lists, math `$…$`, `$$…$$`, and fenced `math` blocks.
- Over-limit answers are safely shortened with an explicit notice at Telegram's 32,768 UTF-8 character cap; do not split across messages.
- Keep web-search summaries unchanged and do not modify private plugin folders.
- Do not expose the bot token or place answer text in inline query strings.

## Review Focus

- Empty or whitespace-only model output must not become an invalid rich inline result; test owning render/delivery task.
- Formatting-heavy Markdown over 32,768 UTF-8 bytes, including multibyte characters, must be shortened without splitting a UTF-8 character; test rich-content preparation task.
- Inline handoff key guessed, expired, or reused must not return another user’s answer; test staging task.
- Missing assistant bot configuration must retain a useful `.ai` error path; test delivery task.
- Bot API errors or malformed responses must not leave `.ai` stuck at “Thinking…” or claim successful insertion; test delivery task.

---

### Task 1: Stage AI responses for one-time inline retrieval

**Files:**
- Modify: `plugins/ai.py`
- Test: `tests/test_ai_inline.py`

**Interfaces:**
- Produces: `_stage_inline_response(text: str) -> str` returns an unpredictable opaque key; `_take_inline_response(key: str) -> str | None` consumes the staged response once; entries expire after 5 minutes.

- [ ] **Step 1: Write failing tests** for unique opaque keys, retrieval of the matching response, one-time consumption, expiry, and unknown keys.
- [ ] **Step 2: Run tests and verify they fail** with missing helper behavior.
- [ ] **Step 3: Implement the staging helpers** in `plugins/ai.py`, using a cryptographically secure random key and monotonic expiry; purge expired entries during retrieval/staging.
- [ ] **Step 4: Run `uv run --with pytest --with pytest-asyncio python -m pytest tests/test_ai_inline.py -q`** and verify all staging tests pass.

### Task 2: Return rich inline results from the assistant bot

**Files:**
- Modify: `plugins/ai.py`
- Test: `tests/test_ai_inline.py`

**Interfaces:**
- Consumes: Task 1 `_take_inline_response(key: str) -> str | None`.
- Produces: A `@h.bot_inline_query` handler for the AI handoff prefix; an internal async helper answers Bot API inline queries with one `InlineQueryResultArticle` whose input content is `InputRichMessageContent(rich_message={"markdown": text})`.

- [ ] **Step 1: Write failing tests** for a valid staged key yielding one rich article, an unknown/expired key yielding an empty safe result, and Bot API failures being surfaced as errors.
- [ ] **Step 2: Run tests and verify they fail** on the missing handler/helper.
- [ ] **Step 3: Implement the handler** using `ctx.bot` configuration and a Bot API URL built from the configured token; send `answerInlineQuery` as JSON. Use a short cache time and personal results. Never log token-bearing URLs or response text.
- [ ] **Step 4: Run `uv run --with pytest --with pytest-asyncio python -m pytest tests/test_ai_inline.py -q`** and verify tests pass.

### Task 3: Preserve rich Markdown, prompt for useful structure, and insert results

**Files:**
- Modify: `plugins/ai.py`
- Test: `tests/test_ai_inline.py`

**Interfaces:**
- Consumes: Task 1 staging helpers and Task 2 handler.
- Produces: `_prepare_rich_answer(content: str) -> str` enforces valid non-empty text and the 32,768 UTF-8-character limit, with a clear shortening marker; `.ai` inserts the staged inline rich result into the invoking chat.

- [ ] **Step 1: Write failing tests** asserting comprehensive Telegram Rich Markdown instructions (formatting, links/buttons, tables, math including `$$…$$` and fenced `math`, details open, expandable quotations and available syntax), sensible optional use, preserved Markdown, visible-prefix handling without 4k truncation, safe shortened output at the API limit, best-effort processing reaction, unchanged `.ai` message, and unchanged search-summary behavior.
- [ ] **Step 2: Run tests and verify they fail** for current plain-text cleanup and response sending.
- [ ] **Step 3: Implement rich-answer preparation and delivery**: add Telegram rich-markdown guidance to the system prompt; preserve Markdown; safely shorten over-limit UTF-8 content at a sensible boundary; stage the final content; call `get_inline_bot_results` using only the opaque key; insert the selected result with `send_inline_bot_result` and reply parameters as the help plugin does. At command start, best-effort react with `👨‍💻`; do not edit the `.ai` message during processing or completion. Keep the current conversation history behavior and do not change `_ai_search` delivery.
- [ ] **Step 4: Run focused tests** and verify result insertion, processing reaction, untouched command message, rich syntax instruction coverage, short/long answers, and over-limit handling.

### Task 4: Full verification

**Files:**
- Test: `tests/test_ai_inline.py`, full existing suite.

- [ ] **Step 1: Run `uv run --with pytest --with pytest-asyncio python -m pytest -q`** and confirm the full suite passes.
- [ ] **Step 2: Run `git diff --check`** and confirm there are no whitespace errors.
- [ ] **Step 3: Inspect the final diff** to confirm no web-search behavior, private plugin folders, secrets, or unrelated files changed.
