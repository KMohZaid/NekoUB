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
    handlers: list[tuple[Client, Any, int]]
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
        user_client: Client | None = None,
        bot_client: Client | None = None,
        access_store: Any = None,
        module_prefix: str = "plugins",
    ) -> None:
        self.client = client
        self.user_client = (
            user_client
            if user_client is not None
            else None if getattr(client, "is_bot", False) is True else client
        )
        self.bot_client = (
            bot_client
            if bot_client is not None
            else client if getattr(client, "is_bot", False) is True else None
        )
        self.plugin_dir = plugin_dir.resolve()
        self.module_prefix = module_prefix.rstrip(".")
        if any(
            not part or not part.replace("_", "a").isalnum()
            for part in self.module_prefix.split(".")
        ):
            raise ValueError("Module prefix must be a dotted identifier")
        self.prefix = prefix
        self.command_prefixes = (
            list(prefix)
            if isinstance(prefix, (list, tuple))
            else [prefix]
        )
        self.config = config if config is not None else {}
        self.access_store = access_store
        self.registry = PluginRegistry()
        self.active: dict[str, ActivePlugin] = {}
        self.status: dict[str, str] = {}
        self._module_owners: dict[str, str] = {}
        self._reload_generation = 0

    def load_all(self, *, skip_paths: set[Path] | None = None) -> dict[str, str]:
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        skip_paths = {path.resolve() for path in (skip_paths or set())}
        for path in sorted(self.plugin_dir.glob("*.py")):
            if path.name.startswith("_") or path.resolve() in skip_paths:
                continue
            try:
                self.load_path(path)
            except Exception as error:
                self.status[path.stem] = f"failed: {type(error).__name__}: {error}"
                logger.exception(
                    "Could not load %s plugin %s",
                    self.module_prefix,
                    path.relative_to(self.plugin_dir.parent),
                )
                continue
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

    def load_path(self, path: Path) -> PluginDeclaration | None:
        module_name = f"{self.module_prefix}.{path.stem}"
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
        if self.module_prefix != "plugins":
            import ast

            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as error:
                raise ValueError(f"Invalid private plugin source: {error}") from error
            except UnicodeError as error:
                raise ValueError(f"Invalid private plugin encoding: {error}") from error
            declares_plugin = any(
                isinstance(node, ast.ClassDef)
                and any(
                    (isinstance(base, ast.Name) and base.id == "Plugin")
                    or (
                        isinstance(base, ast.Attribute)
                        and base.attr == "Plugin"
                    )
                    for base in node.bases
                )
                for node in tree.body
            )
            if not declares_plugin:
                self.status[path.stem] = "skipped: no managed Plugin subclass"
                logger.warning(
                    "Skipped %s plugin %s: no managed Plugin subclass",
                    self.module_prefix,
                    path.relative_to(self.plugin_dir.parent),
                )
                return None
        previous_module = sys.modules.get(module_name)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            instances = [
                value
                for value in vars(module).values()
                if isinstance(value, type)
                and issubclass(value, Plugin)
                and value is not Plugin
                and value.__module__ == module_name
            ]
            if not instances:
                sys.modules.pop(module_name, None)
                self.status[path.stem] = "skipped: no managed Plugin subclass"
                logger.warning(
                    "Skipped %s plugin %s: no managed Plugin subclass",
                    self.module_prefix,
                    path.relative_to(self.plugin_dir.parent),
                )
                return None
            if len(instances) != 1:
                raise ValueError(
                    f"Expected exactly one Plugin subclass, found {len(instances)}"
                )
            plugin = instances[0]()
            disabled_reason = plugin.disabled_reason()
            if disabled_reason:
                sys.modules.pop(module_name, None)
                self.status[path.stem] = f"skipped: {disabled_reason}"
                logger.info(
                    "Skipped %s plugin %s: %s",
                    self.module_prefix,
                    path.relative_to(self.plugin_dir.parent),
                    disabled_reason,
                )
                return None
            self.activate(
                plugin,
                module_name=module_name,
                module=module,
                source_path=path,
            )
            self.status[path.stem] = "active"
            logger.info("Loaded %s plugin %s", self.module_prefix, path.stem)
            return self.registry.plugins[plugin.id]
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
            and self.bot_client is None
            for item in declaration.handlers
        ):
            raise NotImplementedError(
                "Bot handlers require a PluginManager configured with a bot client"
            )
        if any(
            not item.event.startswith("bot_") and not item.options.bot
            for item in declaration.handlers
        ) and self.user_client is None:
            raise NotImplementedError(
                "Userbot handlers require a PluginManager configured with a user client"
            )
        previous = self.active.get(plugin.id)
        if previous is not None and not replace:
            raise ValueError(f"Plugin {plugin.id!r} is already active")

        staged: list[tuple[Any, int]] = []
        try:
            for item in declaration.handlers:
                handler = self._build_handler(plugin, item)
                group = item.options.handler_kwargs.get("group", 0)
                if not isinstance(group, int):
                    raise TypeError("Handler group must be an integer")
                target_client = (
                    self.bot_client
                    if item.event.startswith("bot_") or item.options.bot
                    else self.user_client
                )
                if target_client is None:
                    raise NotImplementedError(
                        f"No client is configured for {item.event} handlers"
                    )
                registration = target_client.register_managed_handler(
                    handler,
                    group,
                    plugin_id=plugin.id,
                    event=item.event,
                )
                staged.append((target_client, *registration))
        except Exception:
            for registered_client, handler, registered_group in reversed(staged):
                registered_client.remove_managed_handler(handler, registered_group)
            raise

        removed_previous: list[tuple[Any, Any, int, str]] = []
        try:
            if previous is not None:
                previous_handlers = zip(
                    previous.handlers, previous.declaration.handlers
                )
                for (registered_client, handler, registered_group), item in reversed(
                    list(previous_handlers)
                ):
                    registered_client.remove_managed_handler(handler, registered_group)
                    removed_previous.append(
                        (registered_client, handler, registered_group, item.event)
                    )
        except Exception:
            for registered_client, handler, registered_group in reversed(staged):
                try:
                    registered_client.remove_managed_handler(handler, registered_group)
                except Exception:
                    logger.exception("Could not roll back staged handler for %s", plugin.id)
            for registered_client, handler, registered_group, event in reversed(
                removed_previous
            ):
                try:
                    registered_client.register_managed_handler(
                        handler,
                        registered_group,
                        plugin_id=plugin.id,
                        event=event,
                    )
                except Exception:
                    logger.exception("Could not restore previous handler for %s", plugin.id)
            raise
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
        self._reload_generation += 1
        candidate_name = (
            f"{self.module_prefix}._candidate_{plugin_id}_{self._reload_generation}"
        )
        canonical_name = f"{self.module_prefix}.{path.stem}"
        previous_canonical = sys.modules.get(canonical_name)
        spec = importlib.util.spec_from_file_location(candidate_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to create module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[candidate_name] = module
        sys.modules[canonical_name] = module
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
            if previous_canonical is None:
                sys.modules.pop(canonical_name, None)
            else:
                sys.modules[canonical_name] = previous_canonical
            raise
        if current.module_name != "<memory>" and current.module_name != canonical_name:
            sys.modules.pop(current.module_name, None)
        self._module_owners.pop(current.module_name, None)
        self._module_owners[canonical_name] = plugin_id

    def command_help(self) -> list[dict[str, Any]]:
        """Return command metadata rendered with the configured prefixes."""
        commands: list[dict[str, Any]] = []
        for plugin_id, declaration in sorted(self.registry.plugins.items()):
            for handler in declaration.handlers:
                command = handler.options.command
                if command is None:
                    continue
                prefixes = (
                    self.command_prefixes
                    if command.prefixes == "."
                    else [command.prefixes]
                    if isinstance(command.prefixes, str)
                    else list(command.prefixes)
                )
                if command.prefixes == "." and isinstance(self.prefix, str):
                    example_prefix = self.prefix
                else:
                    example_prefix = prefixes[0] if prefixes else ""
                prefix = prefixes[0] if prefixes else ""
                entries = command.help_entries or ({},)
                for entry in entries:
                    commands.append(
                        {
                            "plugin": plugin_id,
                            "plugin_category": declaration.category,
                            "plugin_description": declaration.description,
                            "plugin_display_name": getattr(
                                self.active[plugin_id].instance, "display_name", ""
                            ),
                            "prefix": prefix,
                            "prefixes": tuple(prefixes),
                            "sudo": command.sudo,
                            "share": command.share,
                            "grantable": command.grantable,
                            "category": command.category or declaration.category,
                            "trigger": command.trigger,
                            "aliases": command.aliases,
                            "signature": entry.get("signature", command.signature),
                            "description": entry.get("description", command.description),
                            "short_description": entry.get("short_description", command.short_description),
                            "hidden_from_help": command.hidden_from_help,
                            "examples": tuple(
                                example.render(command.trigger, example_prefix)
                                for example in entry.get("examples", command.examples)
                            ),
                            "example_notes": tuple(
                                example.note or "" for example in entry.get("examples", command.examples)
                            ) if entry.get("examples", command.examples) else command.example_notes,
                            "help_options": command.help_options,
                            "options": command.options,
                        }
                    )
        return commands

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
        names: set[tuple[str, str]] = set()
        for item in declaration.handlers:
            if item.event not in HANDLER_TYPES:
                raise ValueError(f"Unsupported handler event {item.event!r}")
            if not item.method_name or not callable(getattr(plugin, item.method_name, None)):
                raise ValueError(f"Handler method {item.method_name!r} is not callable")
            options = item.options
            if item.event.startswith("bot_") or options.bot:
                if self.bot_client is None:
                    raise NotImplementedError(
                        "Bot handlers require a PluginManager configured with a bot client"
                    )
            elif self.user_client is None:
                raise NotImplementedError(
                    "Userbot handlers require a PluginManager configured with a user client"
                )
            if options.command is not None:
                command = options.command
                prefixes = (
                    self.command_prefixes
                    if command.prefixes == "."
                    else [command.prefixes]
                    if isinstance(command.prefixes, str)
                    else list(command.prefixes)
                )
                if not prefixes or any(not prefix for prefix in prefixes):
                    raise ValueError("Commands must declare at least one non-empty prefix")
                if (
                    not command.trigger
                    or not command.trigger.replace("_", "a").isalnum()
                    or command.trigger.startswith("-")
                ):
                    raise ValueError(f"Invalid command trigger {command.trigger!r}")
                command_client = "bot" if options.bot or item.event.startswith("bot_") else "user"
                for name in (command.trigger, *command.aliases):
                    if not name or not name.replace("_", "a").isalnum():
                        raise ValueError(f"Invalid command name or alias {name!r}")
                    key = (command_client, name.casefold())
                    if key in names:
                        raise ValueError(f"Duplicate command or alias {name!r}")
                    names.add(key)
                _validate_options(command.options)
        return declaration

    def _build_handler(self, plugin: Plugin, declaration: HandlerDeclaration) -> Any:
        options = declaration.options
        filters_value = options.filters
        command_meta = options.command
        if command_meta is not None:
            command_prefixes = (
                self.command_prefixes
                if command_meta.prefixes == "."
                else [command_meta.prefixes]
                if isinstance(command_meta.prefixes, str)
                else list(command_meta.prefixes)
            )
            names = [command_meta.trigger, *command_meta.aliases]
            command_filters = filters.command(names, prefixes=command_prefixes)
            if not (options.bot or declaration.event.startswith("bot_")):
                command_filters = filters.me & command_filters
                delegated_prefixes = tuple(
                    prefix
                    for enabled, prefix in (
                        (command_meta.sudo, "#"),
                        (command_meta.share or command_meta.grantable, "?"),
                    )
                    if enabled
                )
                if delegated_prefixes:
                    command_filters = command_filters | filters.command(
                        names, prefixes=delegated_prefixes
                    )
            filters_value = command_filters if filters_value is None else command_filters & filters_value
        callback = _wrap_handler(
            plugin,
            declaration,
            config=self.config,
            command_prefixes=self.command_prefixes,
            user_client=self.user_client,
            bot_client=self.bot_client,
            access_store=self.access_store,
            owner_id=getattr(getattr(self.user_client, "me", None), "id", None),
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
        for client, handler, group in reversed(active.handlers):
            client.remove_managed_handler(handler, group)
        self.registry.unregister(active.declaration.plugin_id)
        self._module_owners.pop(active.module_name, None)
        if active.module_name != "<memory>":
            sys.modules.pop(active.module_name, None)
        self.active.pop(active.declaration.plugin_id, None)
        if active.source_path is not None:
            canonical_name = f"plugins.{active.source_path.stem}"
            if not any(
                other.source_path is not None
                and f"plugins.{other.source_path.stem}" == canonical_name
                for other in self.active.values()
            ):
                sys.modules.pop(canonical_name, None)


def _validate_options(options: tuple[Any, ...]) -> None:
    long_names: dict[str, int] = {}
    short_names: dict[str, int] = {}
    for index, option in enumerate(options):
        long_name = f"--{option.name.replace('_', '-').casefold()}"
        if long_name in long_names:
            raise ValueError(f"Duplicate option {long_name}")
        long_names[long_name] = index
        if option.short:
            if option.short in short_names:
                raise ValueError(f"Duplicate option {option.short}")
            short_names[option.short] = index

    aliases: dict[tuple[str, str], int] = {}
    for index, option in enumerate(options):
        for alias in option.aliases:
            is_long = alias.startswith("--")
            names = long_names if is_long else short_names
            key = ("long" if is_long else "short", alias.casefold())
            owner = names.get(alias)
            alias_owner = aliases.get(key)
            if owner is not None and owner != index or (
                alias_owner is not None and alias_owner != index
            ):
                raise ValueError(f"Duplicate option {alias}")
            aliases[key] = index
