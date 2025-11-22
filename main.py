import asyncio
import importlib.util
import os
import signal
import sys
import threading
from pathlib import Path

from pyrogram import Client
from pyrogram.enums import ParseMode

import config
from userbot.utils import get_logger

logger = get_logger(__name__)

# Global variables
app = None
loaded_plugins = {}
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
    """Load a single plugin from file path."""
    try:
        plugin_name = plugin_path.stem

        # Skip __init__.py and non-python files
        if plugin_name.startswith("__") or not plugin_path.suffix == ".py":
            return False

        # Determine the module prefix based on parent directory
        parent_dir = plugin_path.parent.name
        module_prefix = (
            parent_dir
            if parent_dir in ["plugins", "private_plugins"]
            else "plugins"
        )

        # Load the module
        spec = importlib.util.spec_from_file_location(
            f"{module_prefix}.{plugin_name}", plugin_path
        )
        module = importlib.util.module_from_spec(spec)

        # Add to sys.modules so it can import other modules
        sys.modules[f"{module_prefix}.{plugin_name}"] = module

        # Execute the module
        spec.loader.exec_module(module)

        # Store the loaded module
        loaded_plugins[plugin_name] = module

        logger.info(f"Loaded plugin: {plugin_name}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to load plugin {plugin_path.name}: {e}", exc_info=True
        )
        return False


def load_all_plugins():
    """Load all plugins from the plugins and private_plugins directories."""
    global loaded_plugins
    loaded_plugins.clear()

    # Load from both plugins and private_plugins directories
    plugin_dirs = [
        ("plugins", Path(__file__).parent / "plugins"),
        ("private_plugins", Path(__file__).parent / "private_plugins"),
    ]

    public_loaded = 0
    public_total = 0
    private_loaded = 0
    private_total = 0

    for dir_type, plugins_dir in plugin_dirs:
        if not plugins_dir.exists():
            if dir_type == "plugins":
                logger.warning("Plugins directory not found!")
            continue

        # Load all .py files in directory
        all_files = list(plugins_dir.glob("*.py"))

        # Filter out __init__.py and other excluded files
        plugin_files = [f for f in all_files if not f.stem.startswith("__")]

        loaded_count = 0
        for plugin_file in plugin_files:
            if load_plugin(plugin_file):
                loaded_count += 1

        # Track separately
        if dir_type == "plugins":
            public_loaded = loaded_count
            public_total = len(plugin_files)
        else:
            private_loaded = loaded_count
            private_total = len(plugin_files)

        if plugin_files:
            logger.info(
                f"Loaded {loaded_count}/{len(plugin_files)} plugins from {plugins_dir.name}/"
            )

    # Log totals
    total_loaded = public_loaded + private_loaded
    total_files = public_total + private_total
    logger.info(
        f"Total: {total_loaded}/{total_files} plugins ({public_loaded} public, {private_loaded} private)"
    )


def reload_plugins():
    """Reload all plugins."""
    # Remove old handlers by clearing each group
    if app and hasattr(app, "dispatcher"):
        try:
            # Clear handlers from each group
            for group in list(app.dispatcher.groups.keys()):
                if group in app.dispatcher.groups:
                    app.dispatcher.groups[group].clear()
            logger.info("Cleared all handler groups")
        except Exception as e:
            logger.error(f"Error clearing handler groups: {e}")

    # Remove loaded modules from sys.modules
    modules_to_remove = []
    for module_name in list(sys.modules.keys()):
        if module_name.startswith("plugins.") or module_name.startswith(
            "private_plugins."
        ):
            modules_to_remove.append(module_name)

    for module_name in modules_to_remove:
        try:
            del sys.modules[module_name]
            logger.debug(f"Removed module: {module_name}")
        except Exception as e:
            logger.error(f"Error removing module {module_name}: {e}")

    # Load plugins again
    try:
        load_all_plugins()
        logger.info("Plugins reloaded successfully")
    except Exception as e:
        logger.error(f"Error loading plugins: {e}", exc_info=True)


def main():
    """Main entry point."""
    global app

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

    # Create Pyrogram client with custom settings
    if config.SESSION_STRING:
        app = Client(
            config.CLIENT_NAME,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=config.SESSION_STRING,
            app_version=config.APP_VERSION,
            workers=config.WORKERS,
            parse_mode=parse_mode,
        )
    else:
        app = Client(
            config.CLIENT_NAME,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            app_version=config.APP_VERSION,
            workers=config.WORKERS,
            parse_mode=parse_mode,
        )

    # Make app globally accessible for plugins
    sys.modules[__name__].app = app

    # Register main module so plugins can import from it
    # When run as __main__, plugins need to import from "main"
    if __name__ == "__main__":
        sys.modules["main"] = sys.modules["__main__"]

    # Load all plugins
    load_all_plugins()

    # Start the client
    logger.info("🐱 NekoUB started successfully! Nya~")
    print("\n✨ NekoUB is now online and ready to serve!\n")
    app.run()


if __name__ == "__main__":
    main()
