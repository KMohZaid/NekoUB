# Plugin Handler Wrappers Design

## Intent

Make `core/plugins/*` easier to understand with one shared, straightforward wrapper for managed handlers. Decorators such as `h.command`, `h.on_message`/`h.message`, and the other event decorators should declare only the event and its options. The shared wrapper performs common permission checks once, uses simple `if`/`else` branches for event- or command-specific behavior, creates `PluginContext` and parsed arguments when needed, calls the plugin method, and stops propagation. The manager should bind/register those handlers rather than recover declarations and assemble a complicated wrapper pipeline.

## Goals

- Keep the existing `h.command`-style plugin authoring API and supported event decorators.
- Preserve command parsing, sudo/share/grant authorization, owner-only and public handler rules, context helpers, exception logging, and propagation behavior.
- Make startup registration straightforward and make the runtime path easy to trace from decorator to plugin method.
- Add comments where lifecycle or propagation behavior cannot be made self-evident.

## Non-goals

- Do not redesign the help navigation or command metadata.
- Do not add a central event dispatcher/router.
- Do not remove plugin features as part of this refactor.
- Do not alter command permissions or user-visible behavior intentionally.

## Approaches

1. **One shared wrapper (selected):** Decorators attach a small handler specification to each plugin method. The manager binds each method and event specification to the same simple wrapper function, then registers the resulting Pyrogram handler. The wrapper uses direct conditionals for command authorization, parsing, and event-specific behavior; plugins do not repeat permission checks. Pyrogram continues to evaluate registered filters.
2. **Separate wrapper per event/feature:** Each decorator builds its own custom wrapper and duplicates common access/context logic. This could make individual paths look short, but creates repeated permission logic and is not selected.
3. **Central dispatcher:** Decorators populate event tables and one handler routes to plugin methods. This reduces registration entries but recreates routing and filter-order behavior outside Pyrogram, so it is not selected.

## Design

Keep `@h.command(...)` and the other `h.*` decorators as the public API. Decorators record a small event/option specification; one shared wrapper handles common runtime behavior for all decorated methods. At plugin activation, the manager binds each decorated method and its specification to that wrapper, then registers the resulting Pyrogram handler with the appropriate user or bot client. Keep the wrapper linear and readable with ordinary `if`/`else` branches rather than layered wrapper factories or repeated per-event permission implementations. A reader should be able to follow: decorator declaration → bind/register shared wrapper → Pyrogram filters → shared access checks and context/argument setup → plugin method → `ContinuePropagation`.

Keep the existing modules where their responsibilities remain clear; reorganize or rename internals only when that reduces indirection. `PluginManager` remains responsible for discovery, validation, activation, reload, and removal. `PluginContext` remains the per-update helper. The registry should not duplicate validation or become an additional execution layer. Preserve the current staged activation/reload cleanup and best-effort rollback behavior, including logging when restoration fails.

## Constraints and verification

- Preserve the existing plugin API, current plugins, command access rules, and user/bot client separation.
- Add or adapt tests to prove actual registered wrapper behavior: each supported handler type calls its method with the expected parameters/context, authorization rejects unauthorized delegated commands, owner/public rules are preserved, and propagation continues as before.
- Run targeted plugin tests and the full suite. The previous environment lacked `pyrogram` in the default Python; use the repository's supported environment if present, otherwise report the test limitation rather than claiming the suite passed.
- Avoid drive-by changes to plugin behavior or unrelated help code.

## Calls left to the implementer

- Choose the smallest internal representation for decorator metadata and method binding; plugin instances are still created during discovery/reload.
- Preserve `Plugin.declarations()` and `PluginRegistry.register()` as compatibility APIs with their current behavior/return shape, even if the manager no longer uses the registry for execution.
- Preserve existing handler keyword behavior; do not start forwarding previously ignored handler kwargs as an incidental change.
- Pyrogram evaluates registered filters. Preserve the current command filter composition: owner command filter OR delegated command filter, combined with any custom filter.
- Preserve the current best-effort rollback behavior and its logging; do not claim stronger atomicity when cleanup/restoration itself fails. Deactivation remains unchanged unless required by the wrapper refactor.
- Add concise comments only where the shared wrapper's event branches, staging/rollback, or `ContinuePropagation` semantics are not self-evident.
