# AI Guest-Chat Streaming Design

## Intent

Add `.ai -s` / `.ai --stream` so a configured assistant bot can display generated answers as rich-message drafts in the chat, then persist the finished Rich Message. The option must combine with `-c` / `--context` and ordinary reply context.

## Goals

- Assemble prompt, recent message context, reply context, system prompt, and conversation history before requesting bot handling.
- Create a unique pending request and post `@assistantbot <request-id>` in the chat.
- If the bot receives the Guest Chat update within 7 seconds, let it claim the request, send an initial rich draft, update the draft while provider chunks arrive through `httpx`, and persist the complete result with `send_rich_message()`.
- If no Guest Chat handler claims it before the 7-second deadline, atomically claim it for the existing non-streaming request and inline-result delivery path.
- Prevent a late Guest Chat update and timeout fallback from processing the same request.
- If a provider stream fails after yielding content, finalize the partial content and append a collapsed `❌ Error occurred` block explaining the failure.
- Document Guest Chat Mode setup in the stream option help: “NOTE: for this to work, make your assistant have guest mode on. Go to BotFather mini app > select bot > Bot settings > Enable Guest Chat Mode”.

## Non-goals

- Replacing the existing OpenAI-compatible provider configuration or changing non-streaming `.ai` behavior.
- Guaranteeing streamed output from providers that do not implement OpenAI-compatible SSE; those can use the existing full-response fallback.
- Deleting the visible bot trigger message.

## Approaches

1. **Guest Chat bot with request IDs (selected):** userbot posts a bot mention carrying an opaque request ID; the bot validates and claims the matching in-memory pending request, then streams drafts and persists the final rich message. This follows the desired UX and allows Guest Chat delivery to select the streaming path.
2. **Direct userbot streaming:** bypasses Guest Chat and emits drafts directly from the userbot. This does not match the desired assistant-bot identity or Guest Chat behavior.
3. **Always use inline results:** compatible with current behavior but cannot provide the requested live rich draft.

## Constraints and decisions

- Use the installed Kurigram API methods `send_rich_message_draft()` and `send_rich_message()`; the draft is an ephemeral preview with a 30-second lifetime and does not persist the final message.
- The Guest Chat claim and 7-second fallback need a single atomic request-state transition. Pending requests are single-use and scoped to the originating chat and command.
- Build provider streaming with the existing `httpx` dependency and SSE parsing compatible with OpenAI-style `data:` events; do not introduce another LLM client library.
- Rich draft rendering must preserve supported Rich Markdown syntax; the complete persisted message must be an `InputRichMessage`.
- Stream updates should be throttled to avoid excessive Telegram requests and must fit Telegram/Kurigram rich-message size limits.
- The initial thinking draft is a brief progress indicator, not model chain-of-thought.

## Calls left to the builder

- Exact request-state cleanup/expiry durations and draft update throttling interval.
- Behavior when Guest Chat is disabled or the trigger cannot be delivered: after 7 seconds, continue through the existing non-streaming/inline path, without sending a duplicate provider request.
- How to represent unsupported streaming providers: report the provider failure in the final rich message if no streamed content exists; preserve any partial output if content has already arrived.

## Verification

Add tests for option help metadata, compatibility with context parsing, SSE delta extraction and errors, rich draft/final method calls, the Guest Chat claim versus timeout race, late/duplicate trigger rejection, and partial-stream finalization. Run focused tests, the full unittest suite, and `git diff --check`.
