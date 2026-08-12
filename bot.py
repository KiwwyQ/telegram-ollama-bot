"""
Application entry point.

Wires together configuration, storage, the Ollama client, memory manager, tools
and Telegram handlers, then runs long-polling. Designed to start cleanly on
Render (Background Worker or Web Service) and to shut down gracefully.
"""
import asyncio
import logging

from telegram.ext import Application

from config import Config
from logging_config import setup_logging
from storage import Storage
from ollama_client import OllamaClient
from memory import MemoryManager
from tools import Tools
from handlers import BotContext, register_handlers
from keep_alive import start_health_server
from commands import register_commands


async def main() -> None:
    config = Config.from_env()
    logger = setup_logging(config.LOG_LEVEL)
    logger.info("Starting bot (free-tier friendly)...")

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Refusing to start.")
        return

    storage = Storage(config)
    await storage.init()

    ollama = OllamaClient(config, logger)
    memory = MemoryManager(storage, config, ollama, logger)
    tools = Tools(ollama, config, logger)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(register_commands).build()

    # Resolve the bot username at runtime (used for mention detection).
    me = await app.bot.get_me()
    config.BOT_USERNAME = me.username or ""
    logger.info("Logged in as @%s", config.BOT_USERNAME)

    ctx = BotContext(
        config=config,
        storage=storage,
        ollama=ollama,
        memory=memory,
        tools=tools,
        logger=logger,
    )
    register_handlers(app, ctx)

    # Close the tools' HTTP client and DB engine on shutdown.
    async def _shutdown() -> None:
        try:
            await tools._http.aclose()
        except Exception:
            pass
        try:
            await storage.close()
        except Exception:
            pass

    try:
        app.post_shutdown = _shutdown
    except Exception:
        logger.debug("post_shutdown hook not supported on this PTB version")

    if config.ENABLE_WEB_SERVER:
        await start_health_server(config, logger)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    await app.run_polling(drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger("bot").info("Bot stopped.")
    except Exception:
        logging.getLogger("bot").exception("Fatal startup error")
