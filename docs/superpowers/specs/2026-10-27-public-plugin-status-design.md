# Public Plugin Status Design

## Intent

Add a `.plugins` help command backed by the public plugin loader so the owner can see which public plugin files loaded, which did not, why they were skipped or failed, and how many managed commands are available.

## Goals

- Track each discovered direct public plugin file as loaded, intentionally skipped, or failed, with a concise note for skips and failures.
- Report public loaded, not-loaded, and total file counts, plus the active managed command count.
- Render non-loaded public plugin filenames in bold within an HTML blockquote.
- Keep `private_plugins/` untouched and show only that private plugin status/details are intentionally unavailable for now.
- Keep the help menu's Core category populated with `.alive` and `.plugins`; list `.spam` under Fun / Spam.

## Non-goals

- Do not read, import, register, or report per-file details for private plugins.
- Do not infer environment requirements from plugin source. Use captured loader errors and each managed plugin's readiness hook for actionable skip reasons.
- Do not inspect, import, or report per-file details for private plugins.

## Approaches

1. **Track loader outcomes (selected):** record outcomes at each public loader decision point, then render counts and notes from that state. This reports what actually happened and makes failures actionable.
2. **Maintain a fixed status list:** simple to render, but it can drift from real startup outcomes and command registration.

The user selected tracking loader status. Only public plugin outcomes are recorded; private plugins remain out of scope.

## Design

- `Plugin.disabled_reason()` is an optional synchronous readiness hook. It returns `None` when available or a human-readable reason to skip activation; implementations may calculate it from current environment/configuration values.
- `main.py` owns public plugin status keyed by filename. Each record contains `loaded`, `skipped`, or `failed`, plus a concise note for non-loaded results. Managed plugin readiness is checked before handler activation; import/runtime exceptions are captured as failures.
- The plugin manager remains the source for active managed command counts. `.plugins` reports that command count alongside public file counts and the not-loaded notes.
- `.plugins` is available as a command and is listed under Core. Private section displays a neutral unavailable message only, with no private filenames, counts, or reasons.
- Output uses escaped HTML with `<blockquote>` notes and `<b>` filenames.
- Core help lists `.alive` and `.plugins`; Fun / Spam lists `.spam`. Tests cover the hook, counts, escaping/formatting, and category entries.

## Constraints and calls left to implementation

- Keep existing public plugin discovery/loading semantics unchanged.
- Treat successfully loaded files and active managed commands as different measures: a legacy public module may load without contributing managed command metadata.
- Do not restart the running bot unless the user asks for it; do not commit this work.
