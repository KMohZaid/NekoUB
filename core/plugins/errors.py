"""Command option parsing and user-facing validation errors."""

from __future__ import annotations

import html
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from .api import Option


@dataclass(frozen=True, slots=True)
class ParsedOptions:
    values: dict[str, Any]
    positional: tuple[str, ...]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as error:
            raise AttributeError(name) from error


class OptionParseError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def as_html(self) -> str:
        safe_message = html.escape(self.message)
        safe_message = safe_message.replace("&lt;code&gt;", "<code>").replace(
            "&lt;/code&gt;", "</code>"
        )
        return f"<b>Error</b>: {safe_message}"


def parse_options(
    text: str,
    definitions: Sequence[Option],
    trigger: str | Sequence[str],
    prefixes: str | Sequence[str] = ".",
) -> ParsedOptions:
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        raise OptionParseError("Invalid quoting in command arguments.") from error
    if tokens:
        command_token = tokens[0]
        command_name = command_token.split("@", 1)[0]
        prefix_values = (prefixes,) if isinstance(prefixes, str) else tuple(prefixes)
        matching_prefix = next(
            (prefix for prefix in prefix_values if command_name.startswith(prefix)),
            None,
        )
        command_names = (trigger,) if isinstance(trigger, str) else tuple(trigger)
        if matching_prefix is not None and (
            command_name[len(matching_prefix):].casefold()
            in {name.casefold() for name in command_names}
        ):
            tokens = tokens[1:]

    by_long = {
        f"--{option.name.replace('_', '-').lower()}": option
        for option in definitions
    }
    by_short = {option.short: option for option in definitions if option.short}
    if len(by_long) != len(definitions) or len(by_short) != sum(
        option.short is not None for option in definitions
    ):
        raise ValueError("Duplicate option name or short alias")

    values = {option.name: option.default for option in definitions if option.default is not None}
    seen: set[str] = set()
    positional: list[str] = []
    index = 0
    options_enabled = True

    while index < len(tokens):
        token = tokens[index]
        index += 1
        if options_enabled and token == "--":
            options_enabled = False
            continue
        if not options_enabled or token == "-" or not token.startswith("-"):
            positional.append(token)
            continue

        if token.startswith("--"):
            name, separator, attached = token.partition("=")
            option = by_long.get(name)
            if option is None:
                raise OptionParseError(f"Unknown option <code>{name}</code>.")
            if name in seen and not option.append:
                raise OptionParseError(
                    f"Option <code>{name}</code> was provided more than once."
                )
            if option.action == "store_true":
                if separator:
                    raise OptionParseError(f"Option <code>{name}</code> does not take a value.")
                value: Any = True
            elif option.action == "store_false":
                if separator:
                    raise OptionParseError(f"Option <code>{name}</code> does not take a value.")
                value = False
            else:
                if separator:
                    raw = attached
                else:
                    if index >= len(tokens):
                        raise OptionParseError(
                f"Option <code>{name}</code> requires a value."
            )
                    raw = tokens[index]
                    index += 1
                value = _convert(option, raw, name)
            _store(values, option, value)
            seen.add(name)
            continue

        cluster = token[1:]
        offset = 0
        while offset < len(cluster):
            name = f"-{cluster[offset]}"
            option = by_short.get(name)
            if option is None:
                raise OptionParseError(f"Unknown option <code>{name}</code>.")
            if name in seen and not option.append:
                raise OptionParseError(
                    f"Option <code>{name}</code> was provided more than once."
                )
            if option.action in {"store_true", "store_false"}:
                value = option.action == "store_true"
                offset += 1
            else:
                attached = cluster[offset + 1:]
                if attached.startswith("="):
                    attached = attached[1:]
                if attached:
                    raw = attached
                    offset = len(cluster)
                else:
                    if offset + 1 < len(cluster):
                        raise OptionParseError(
                            f"Value option <code>{name}</code> must be last in a cluster and include its value."
                        )
                    if index >= len(tokens):
                        raise OptionParseError(
                f"Option <code>{name}</code> requires a value."
            )
                    raw = tokens[index]
                    index += 1
                    offset += 1
                value = _convert(option, raw, name)
            _store(values, option, value)
            seen.add(name)

    required_missing = [
        f"--{option.name.replace('_', '-')}"
        for option in definitions
        if option.required and option.name not in values
    ]
    if required_missing:
        rendered = ", ".join(f"<code>{name}</code>" for name in required_missing)
        raise OptionParseError(f"Missing required options: {rendered}.")
    return ParsedOptions(values=values, positional=tuple(positional))


def _convert(option: Option, raw: str, name: str) -> Any:
    try:
        if option.type is bool:
            lowered = raw.lower()
            if lowered not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError
            return lowered in {"true", "1", "yes"}
        return option.type(raw)
    except (TypeError, ValueError) as error:
        raise OptionParseError(
            f"Option <code>{name}</code> must be a valid {option.type.__name__}."
        ) from error


def _store(values: dict[str, Any], option: Option, value: Any) -> None:
    if option.append:
        values.setdefault(option.name, []).append(value)
    else:
        values[option.name] = value
