"""Public plugin discovery, validation, and managed handler activation."""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from pyrogram import Client, filters

from .api import HANDLER_TYPES, Plugin, _wrap_handler
from .registry import HandlerDeclaration, PluginDeclaration, PluginRegistry

logger = logging.getLogger(__name__)


@dataclass
class ActivePlugin:
    instance: Plugin
    declaration: PluginDeclaration
    handlers: list[tuple[Any, int]]
    module_name: str
    module: ModuleType | None
    source_path: Path | None = None


class PluginManager:
    """Owns public plugin imports and the handlers they install."""

    def __init__(
        self,
        client: Client,
        plugin_dir: Path,
        *,
        prefix: str | list[str] = ".",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.plugin_dir = plugin_dir.resolve()
        self.prefix = prefix
        self.command_prefixes = (
            list(prefix)
            if isinstance(prefix, (list, tuple))
            else [prefix]
        )
        self.config = config if config is not None else {}
        self.registry = PluginRegistry()
        self.active: dict[str, ActivePlugin] = {}
        self.status: dict[str, str] = {}
        self._module_owners: dict[str, str] = {}

    def load_all(self) -> dict[str, str]:
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.plugin_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                self.load_path(path)
            except Exception as error:
                self.status[path.stem] = f"failed: {type(error).__name__}: {error}"
                logger.exception("Could not load public plugin %s", path.stem)
        return dict(self.status)

    def activate_module(self, module: ModuleType, path: Path) -> PluginDeclaration:
        """Activate the single Plugin subclass declared by an imported module."""
        instances = [
            value()
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, Plugin)
            and value is not Plugin
            and value.__module__ == module.__name__
        ]
        if len(instances) != 1:
            raise ValueError(
                f"{path.name}: expected exactly one Plugin subclass, found {len(instances)}"
            )
        self.activate(
            instances[0], module_name=module.__name__, module=module, source_path=path
        )
        return self.registry.plugins[instances[0].id]

    def load_path(self, path: Path) -> PluginDeclaration:
        module_name = f"plugins.{path.stem}"
        if path.parent.resolve() != self.plugin_dir.resolve():
            raise ValueError(
                "Plugin path must be a direct child of the configured public directory"
            )
        if not path.is_file() or path.suffix != ".py" or path.stem.startswith("_"):
            raise ValueError(f"Not a discoverable public plugin file: {path.name}")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to create module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        previous_module = sys.modules.get(module_name)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            instances = [
                value()
                for value in vars(module).values()
                if isinstance(value, type)
                and issubclass(value, Plugin)
                and value is not Plugin
                and value.__module__ == module_name
            ]
            if len(instances) != 1:
                raise ValueError(
                    f"Expected exactly one Plugin subclass, found {len(instances)}"
                )
            self.activate(
                instances[0],
                module_name=module_name,
                module=module,
                source_path=path,
            )
            return self.registry.plugins[instances[0].id]
        except Exception:
            if previous_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module
            raise

    def activate(
        self,
        plugin: Plugin,
        *,
        module_name: str = "<memory>",
        module: ModuleType | None = None,
        replace: bool = False,
        source_path: Path | None = None,
    ) -> None:
        plugin.prefix = "".join(self.command_prefixes)
        declaration = self._validate(plugin)
        if any(
            (item.event.startswith("bot_") or item.options.bot)
            and getattr(self.client, "is_bot", False) is False
            for item in declaration.handlers
        ):
            raise NotImplementedError(
                "Bot handlers require a PluginManager configured with a bot client"
            )
        previous = self.active.get(plugin.id)
        if previous is not None and not replace:
            raise ValueError(f"Plugin {plugin.id!r} is already active")

        staged: list[tuple[Any, int]] = []
        try:
            for item in declaration.handlers:
                handler = self._build_handler(plugin, item)
                group = 0
                registration = self.client.register_managed_handler(
                    handler,
                    group,
                    plugin_id=plugin.id,
                    event=item.event,
                )
                staged.append(registration)
        except Exception:
            for handler, group in reversed(staged):
                self.client.remove_managed_handler(handler, group)
            raise

        if previous is not None:
            for handler, group in reversed(previous.handlers):
                self.client.remove_managed_handler(handler, group)
        self.registry.plugins[plugin.id] = declaration
        self.active[plugin.id] = ActivePlugin(
            plugin,
            declaration,
            staged,
            module_name,
            module or sys.modules.get(module_name),
            source_path or (previous.source_path if previous else None),
        )
        self.status[plugin.id] = "active"
        if module_name != "<memory>":
            self._module_owners[module_name] = plugin.id

    def reload(self, plugin_id: str) -> None:
        current = self.active.get(plugin_id)
        if current is None:
            raise KeyError(plugin_id)
        path = current.source_path
        if path is None or not path.is_file():
            raise FileNotFoundError(path or f"source path for plugin {plugin_id!r}")
        # Load into an isolated module name; only swap after staging succeeds.
        candidate_name = f"plugins._candidate_{plugin_id}"
        spec = importlib.util.spec_from_file_location(candidate_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to create module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[candidate_name] = module
        try:
            spec.loader.exec_module(module)
            candidates = [
                value()
                for value in vars(module).values()
                if isinstance(value, type)
                and issubclass(value, Plugin)
                and value is not Plugin
                and value.__module__ == candidate_name
                and value.id == plugin_id
            ]
            if len(candidates) != 1:
                raise ValueError("Reload candidate must declare exactly one matching plugin")
            self.activate(candidates[0], module_name=candidate_name, module=module, replace=True)
        except Exception:
            sys.modules.pop(candidate_name, None)
            raise
        if current.module_name != "<memory>":
            sys.modules.pop(current.module_name, None)
        self._module_owners.pop(current.module_name, None)

    def _validate(self, plugin: Plugin) -> PluginDeclaration:
        declaration = PluginDeclaration(
            plugin_id=plugin.id,
            category=plugin.category,
            description=plugin.description,
            handlers=plugin.declarations(),
        )
        if (
            not declaration.plugin_id
            or not declaration.plugin_id.replace("_", "a").isalnum()
        ):
            raise ValueError("Plugin id must be a non-empty alphanumeric identifier")
        if not declaration.handlers:
            raise ValueError(f"Plugin {plugin.id!r} declares no handlers")
        if plugin.id in self.registry.plugins and plugin.id not in self.active:
            raise ValueError(f"Duplicate plugin id: {plugin.id}")
        names: set[str] = set()
        for item in declaration.handlers:
            if item.event not in HANDLER_TYPES:
                raise ValueError(f"Unsupported handler event {item.event!r}")
            if not item.method_name or not callable(getattr(plugin, item.method_name, None)):
                raise ValueError(f"Handler method {item.method_name!r} is not callable")
            options = item.options
            if item.event.startswith("bot_") or options.bot:
                if getattr(self.client, "is_bot", False):
                    continue
                raise NotImplementedError(
                    "Bot handlers require a PluginManager configured with a bot client"
                )
            if options.command is not None:
                command = options.command
                if (
                    not command.trigger
                    or not command.trigger.replace("_", "a").isalnum()
                    or command.trigger.startswith("-")
                ):
                    raise ValueError(f"Invalid command trigger {command.trigger!r}")
                for name in (command.trigger, *command.aliases):
                    if not name or not name.replace("_", "a").isalnum():
                        raise ValueError(f"Invalid command name or alias {name!r}")
                    key = name.casefold()
                    if key in names:
                        raise ValueError(f"Duplicate command or alias {name!r}")
                    names.add(key)
                _validate_options(command.options)
        return declaration

    def _build_handler(self, plugin: Plugin, declaration: HandlerDeclaration) -> Any:
        options = declaration.options
        filters_value = options.filters
        if options.handler_kwargs:
            unknown = ", ".join(sorted(options.handler_kwargs))
            raise TypeError(f"Unsupported handler options for {declaration.event}: {unknown}")
        command_meta = options.command
        if command_meta is not None:
            command_prefixes = (
                self.command_prefixes
                if command_meta.prefixes == "."
                else [command_meta.prefixes]
                if isinstance(command_meta.prefixes, str)
                else list(command_meta.prefixes)
            )
            command_filters = filters.me & filters.command(
                [command_meta.trigger, *command_meta.aliases],
                prefixes=command_prefixes,
            )
            filters_value = command_filters if filters_value is None else command_filters & filters_value
        callback = _wrap_handler(
            plugin,
            declaration,
            config=self.config,
            command_prefixes=self.command_prefixes,
        )
        handler_type = HANDLER_TYPES[declaration.event]
        try:
            handler = handler_type(callback, filters_value)
        except Exception as error:
            raise TypeError(
                f"Invalid {declaration.event} handler declaration: {error}"
            ) from error
        return handler

    def _deactivate(self, active: ActivePlugin) -> None:
        for handler, group in reversed(active.handlers):
            self.client.remove_managed_handler(handler, group)
        self.registry.unregister(active.declaration.plugin_id)
        self._module_owners.pop(active.module_name, None)
        if active.module_name != "<memory>":
            sys.modules.pop(active.module_name, None)
        self.active.pop(active.declaration.plugin_id, None)


def _validate_options(options: tuple[Any, ...]) -> None:
    long_names: set[str] = set()
    short_names: set[str] = set()
    for option in options:
        long_name = option.name.replace("_", "-").casefold()
        if long_name in long_names:
            raise ValueError(f"Duplicate option --{long_name}")
        long_names.add(long_name)
        if option.short:
            if option.short in short_names:
                raise ValueError(f"Duplicate option {option.short}")
            short_names.add(option.short)
