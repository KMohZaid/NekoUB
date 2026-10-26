import asyncio
import importlib.util
import os
import signal
import sys
import threading
from pathlib import Path

from pyrogram.enums import ParseMode

import config
from core.plugins import Plugin
from core.plugins.client import PluginClient
from core.plugins.manager import PluginManager
from utils import get_logger

logger = get_logger(__name__)

# Global variables
app: PluginClient | None = None
loaded_plugins = {}
loaded_plugin_handlers = {}  # Track legacy public plugin handlers by module.
plugin_manager = None
running_tasks = {}  # Track running exec/eval tasks for .kill command


try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT signals with forced shutdown."""
    signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    logger.info(f"Received {signal_name}, shutting down...")

    # Cancel all asyncio tasks
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            logger.info("Cancelling all asyncio tasks...")
            for task in asyncio.all_tasks(loop):
                task.cancel()
    except Exception as e:
        logger.debug(f"Error cancelling tasks: {e}")

    # Try graceful stop
    if app and hasattr(app, "stop"):
        try:
            app.stop()
            logger.info("Bot stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")

    # Force exit after 2 seconds if still running
    def hard_exit():
        import time

        time.sleep(2)
        logger.warning("⚠️ Hard exit after 2s timeout")
        os._exit(0)

    threading.Thread(target=hard_exit, daemon=True).start()

    sys.exit(0)


def get_parse_mode():
    """Get ParseMode enum from config string."""
    parse_mode_map = {
        "markdown": ParseMode.MARKDOWN,
        "md": ParseMode.MARKDOWN,
        "html": ParseMode.HTML,
        "combined": ParseMode.DEFAULT,
        "default": ParseMode.DEFAULT,
        "disabled": ParseMode.DISABLED,
        "none": ParseMode.DISABLED,
    }
    return parse_mode_map.get(config.PARSE_MODE, ParseMode.MARKDOWN)


def load_plugin(plugin_path: Path) -> bool:
    """Load a public legacy plugin or activate a managed plugin declaration."""
    try:
        plugin_name = plugin_path.stem

        # Skip __init__.py and non-python files
        if plugin_name.startswith("__") or not plugin_path.suffix == ".py":
            return False

        # Determine the module prefix based on parent directory
        parent_dir = plugin_path.parent.name
        if parent_dir == "private_plugins":
            logger.warning("Private plugin loading is disabled")
            return False
        module_prefix = "plugins"

        # Load the module
        spec = importlib.util.spec_from_file_location(
            f"{module_prefix}.{plugin_name}", plugin_path
        )
        module = importlib.util.module_from_spec(spec)

        # Add to sys.modules so it can import other modules
        sys.modules[f"{module_prefix}.{plugin_name}"] = module

        # Execute the module
        spec.loader.exec_module(module)

        managed_classes = [
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, Plugin)
            and value is not Plugin
            and value.__module__ == module.__name__
        ]
        if managed_classes:
            if plugin_manager is None:
                raise RuntimeError("Managed plugin manager is not initialized")
            plugin_manager.activate_module(module, plugin_path)

        # Store the loaded module
        loaded_plugins[plugin_name] = module

        logger.info(f"Loaded plugin: {plugin_name}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to load plugin {plugin_path.name}: {e}", exc_info=True
        )
        return False


def load_all_plugins(*, skip_paths: set[Path] | None = None):
    """Load public plugins, optionally skipping already managed source files."""
    skip_paths = {path.resolve() for path in (skip_paths or set())}
    global loaded_plugins

    # Discover public plugins only. private_plugins is intentionally excluded.
    plugin_dirs = [("plugins", Path(__file__).parent / "plugins")]

    public_loaded = 0
    public_total = 0
    for dir_type, plugins_dir in plugin_dirs:
        if not plugins_dir.exists():
            if dir_type == "plugins":
                logger.warning("Plugins directory not found!")
            continue

        # Load all .py files in directory
        all_files = list(plugins_dir.glob("*.py"))

        # Filter out __init__.py and other excluded files
        plugin_files = [
            f for f in all_files
            if not f.stem.startswith("_") and f.suffix == ".py"
        ]

        loaded_count = 0
        for plugin_file in plugin_files:
            if plugin_file.resolve() in skip_paths:
                continue
            if load_plugin(plugin_file):
                loaded_count += 1

        # Track separately
        public_loaded = loaded_count
        public_total = len(plugin_files)

        if plugin_files:
            logger.info(
                f"Loaded {loaded_count}/{len(plugin_files)} plugins from {plugins_dir.name}/"
            )

    # Log totals
    logger.info("Loaded %s/%s public plugins", public_loaded, public_total)


def reload_plugins():
    """Reload managed plugins without duplicating legacy registrations."""
    managed_paths: set[Path] = {
        Path(getattr(module, "__file__", "")).resolve()
        for module in loaded_plugins.values()
        if getattr(module, "__file__", None)
    }
    if plugin_manager is not None:
        for plugin_id in list(plugin_manager.active):
            active = plugin_manager.active[plugin_id]
            if active.source_path is not None:
                managed_paths.add(active.source_path.resolve())
            try:
                plugin_manager.reload(plugin_id)
            except Exception as e:
                logger.error(
                    "Failed to reload managed plugin %s: %s",
                    plugin_id,
                    e,
                    exc_info=True,
                )

    # Legacy modules register at import time, but their handlers are not yet
    # tracked by module. Avoid re-importing them and installing duplicates.
    if loaded_plugins:
        logger.warning(
            "Legacy public plugins cannot be hot-reloaded safely; restart to reload them"
        )
    try:
        load_all_plugins(skip_paths=managed_paths)
        logger.info("Managed plugins reloaded successfully")
    except Exception as e:
        logger.error("Error loading plugins: %s", e, exc_info=True)


def main():
    """Main entry point."""
    global app, plugin_manager

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("\n" + "=" * 50)
    print("   🐱 NekoUB - Neko Userbot")
    print("   Nya~ Starting up...")
    print("=" * 50 + "\n")

    logger.info("Starting NekoUB - Neko Userbot")
    logger.info(f"Command prefix: {config.CMD_PREFIX}")
    logger.info(f"Client: {config.CLIENT_NAME} v{config.APP_VERSION}")
    logger.info(f"Workers: {config.WORKERS}")
    logger.info(f"Parse mode: {config.PARSE_MODE}")

    # Get parse mode
    parse_mode = get_parse_mode()

    # Create Kurigram client with custom settings
    if config.SESSION_STRING:
        app = PluginClient(
            config.CLIENT_NAME,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=config.SESSION_STRING,
            app_version=config.APP_VERSION,
            workers=config.WORKERS,
            parse_mode=parse_mode,
        )
    else:
        app = PluginClient(
            config.CLIENT_NAME,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            app_version=config.APP_VERSION,
            workers=config.WORKERS,
            parse_mode=parse_mode,
        )

    # Initialize the managed public plugin manager.
    plugin_manager = PluginManager(
        app,
        Path(__file__).parent / "plugins",
        prefix=config.CMD_PREFIX,
        config={key: value for key, value in vars(config).items() if key.isupper()},
    )

    # Make app globally accessible for plugins
    sys.modules[__name__].app = app

    # Register main module so plugins can import from it
    # When run as __main__, plugins need to import from "main"
    if __name__ == "__main__":
        sys.modules["main"] = sys.modules["__main__"]

    # Forward logs to a Telegram chat when DEBUG_CHAT_ID is configured.
    try:
        from utils.telegram_logging import install as install_log_forwarder

        install_log_forwarder(app.loop, app)
    except Exception as exc:
        logger.error(f"Could not install Telegram log forwarder: {exc}")

    # Load public plugins only. Existing private plugins are intentionally excluded.
    load_all_plugins()

    # Start the client
    logger.info("🐱 NekoUB started successfully! Nya~")
    print("\n✨ NekoUB is now online and ready to serve!\n")
    app.run()


if __name__ == "__main__":
    main()
