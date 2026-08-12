"""
Application entry point.

Wires together configuration, storage, the Ollama client, memory manager, tools
and Telegram handlers, then runs long-polling. Designed to start cleanly on
Render (Background Worker or Web Service) and to shut down gracefully.

Important: `Application.run_polling()` manages its own event loop, so `main()`
is a plain synchronous function and `run_polling()` is called directly (never
awaited inside `asyncio.run`). All async initialization (DB, get_me, command
registration) happens inside `post_init`, which PTB runs within its loop.
"""
import logging

from telegram.ext import Application

from config import Config
from logging_config import setup_logging
from storage import Storage
from ollama_client import OllamaClient
from memory import MemoryManager
from tools import Tools
from handlers import BotContext, register_handlers
from keep_alive import start_health_server_in_thread
from commands import register_commands


async def _post_init(application: Application) -> None:
    """Async setup that PTB runs inside its own event loop, before polling."""
    config = application.bot_data["config"]
    logger = application.bot_data["logger"]
    storage = application.bot_data["storage"]
    ollama = application.bot_data["ollama"]
    memory = application.bot_data["memory"]
    tools = application.bot_data["tools"]

    # Resolve the bot username at runtime (used for mention detection).
    me = await application.bot.get_me()
    config.BOT_USERNAME = me.username or ""
    logger.info("Logged in as @%s", config.BOT_USERNAME)

    # Initialize the remote database.
    await storage.init()

    ctx = BotContext(
        config=config,
        storage=storage,
        ollama=ollama,
        memory=memory,
        tools=tools,
        logger=logger,
    )
    register_handlers(application, ctx)

    # Register the scoped command menus (private vs group vs admin).
    await register_commands(application)


async def _shutdown(application: Application) -> None:
    """Release connections on shutdown (runs inside PTB's loop)."""
    storage = application.bot_data.get("storage")
    tools = application.bot_data.get("tools")
    if tools is not None:
        try:
            await tools._http.aclose()
        except Exception:
            pass
    if storage is not None:
        try:
            await storage.close()
        except Exception:
            pass


def main() -> None:
    config = Config.from_env()
    logger = setup_logging(config.LOG_LEVEL)
    logger.info("Starting bot (free-tier friendly)...")

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Refusing to start.")
        return

    # Synchronous construction of the components (no loop needed yet).
    storage = Storage(config)
    ollama = OllamaClient(config, logger)
    memory = MemoryManager(storage, config, ollama, logger)
    tools = Tools(ollama, config, logger)

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_shutdown)
        .build()
    )
    app.bot_data.update(
        config=config,
        logger=logger,
        storage=storage,
        ollama=ollama,
        memory=memory,
        tools=tools,
    )

    # Optional keep-alive health endpoint (Web Service mode). Runs in its own
    # thread/event loop so it never conflicts with run_polling's loop.
    if config.ENABLE_WEB_SERVER:
        start_health_server_in_thread(config, logger)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    # Not awaited, not wrapped in asyncio.run(): run_polling owns the loop.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger("bot").info("Bot stopped.")
    except Exception:
        logging.getLogger("bot").exception("Fatal startup error")
