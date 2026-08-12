"""
Optional keep-alive health endpoint.

Used when the bot is deployed as a Render *Web Service* (ENABLE_WEB_SERVER=true).
Render's free web services spin down after 15 minutes of inactivity, so you
typically pair this with an external pinger (e.g. UptimeRobot hitting /health).

When deploying as a *Background Worker* (recommended for free tier) this is not
needed and ENABLE_WEB_SERVER should stay false.

The server runs in a dedicated daemon thread with its own asyncio event loop,
so it never conflicts with the loop that python-telegram-bot's run_polling()
controls.
"""
import asyncio
import threading

from aiohttp import web

from config import Config


async def health(_: web.Request) -> web.Response:
    return web.Response(text="OK", status=200)


def start_health_server_in_thread(config: Config, logger) -> threading.Thread:
    """Start the /health server in a background thread with its own event loop."""
    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_get("/", health)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "0.0.0.0", config.PORT)
        loop.run_until_complete(site.start())
        logger.info("Health server listening on port %s", config.PORT)
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(runner.cleanup())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
