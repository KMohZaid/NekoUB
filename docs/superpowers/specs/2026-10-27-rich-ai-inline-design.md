# Rich AI Inline Responses

## Intent

Render `.ai` answers with Telegram Rich Markdown and deliver them as messages authored by the assistant bot, even in groups where that bot is not a member. Keep the useful opening visible while Telegram collapses long overflow.

## Goals

- Preserve Markdown formatting from the model instead of flattening it to plain text.
- Use Telegram Rich Markdown through `InputRichMessageContent` in an inline result.
- Keep the original `.ai` command message untouched; add the `👨‍💻` processing reaction at the start, then insert only the assistant bot's result.
- Encourage well-structured rich Markdown, using formatting features where helpful rather than forcing every feature into every answer.
- Keep roughly the first 4,000 characters directly visible; rely on Telegram clients' native “Show more” overflow behavior for additional text.
- Use `<details open><summary>…</summary>…</details>` for useful, initially expanded sections; reserve collapsed `<details>` for genuinely optional detail.
- Continue inserting the result into the current chat from the userbot, following the existing help inline-result pattern.

## Non-goals

- Add a new user-facing inline AI query mode.
- Send rich answers directly from the assistant bot to group chats.
- Implement streaming rich drafts or rich media attachments.
- Guarantee identical rendering on every Telegram client.

## Approaches

1. **Inline rich result (chosen):** `.ai` stages the completed response, asks the assistant bot for a matching inline result, then has the userbot insert it. This fits the bot’s group-membership constraint and existing help flow. The installed Kurigram version lacks the rich-result content type, so use its supported inline-query retrieval/insertion and the Bot API for the rich inline result response.
2. **Direct bot message:** send a rich message directly with the assistant bot. This avoids the inline-result handoff but does not fit chats where the bot is not a member.
3. **Split the answer into multiple results/messages:** avoids one oversized result but adds chat clutter and complicates delivery.

## Behavior and constraints

- Add explicit rich-Markdown instructions to the AI system prompt with Telegram's syntax reference: bold (`**`/`__`), italic (`*`/`_`), strike (`~~`), inline code, marked text (`==`), spoilers (`||`), inline URLs/e-mail/phone/user mentions/custom emoji/date-time, math `$…$` and `$$…$$`, headings, paragraphs, fenced code, horizontal rules, unordered/ordered/task lists, block quotations and expandable quotations, media syntax, tables, reference footnotes, HTML-compatible extras (underline, subscript, superscript, anchors, pull quotes, details, maps, collage/slideshow, and inline buttons with URL styles). Tell the model to use only relevant features, make URL buttons useful and label them clearly, use `<details open>` to organize sections initially expanded, and math code fences with language `math` where suitable. Do not wrap the whole answer in a collapsed section.
- Preserve the model's Markdown and compose one rich inline result for the answer, without the current plain-text cleanup, HTML escaping, or question footer being applied to the answer body.
- Leave the initial approximately 4,000 characters outside details and let Telegram's native rich-message UI provide “Show more” for overflow. Do not manually truncate answers at 4,000 characters.
- Do not edit the `.ai` command message. Add a best-effort `👨‍💻` reaction before the model request; continue if reaction APIs are unavailable or the reaction fails.
- Telegram documents a 32,768 UTF-8 character rich-message limit. If an answer exceeds it, trim at a safe boundary and indicate that content was shortened; do not silently split it across chat messages.
- Keep web-search summaries on their current behavior unless a separate follow-up expands scope.

## Implementation notes

- Reuse the assistant bot identity and `get_inline_bot_results` / `send_inline_bot_result` flow already used in `plugins/help.py`.
- Stage results with an unpredictable, short-lived identifier and consume them once in the bot inline-query handler; avoid putting answer text in the inline query string.
- Add focused tests for rich-result payload creation, staging/lookup/expiry behavior, over-limit handling, and AI formatting instructions.
- No private plugin folder changes. Do not commit until explicitly requested.
