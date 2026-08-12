"""
Optional keep-alive health endpoint.

Used when the bot is deployed as a Render *Web Service* (ENABLE_WEB_SERVER=true).
Render's free web services spin down after 15 minutes of inactivity, so you
typically pair this with an external pinger (e.g. UptimeRobot hitting /health).

When deploying as a *Background Worker* (recommended for free tier) this is not
needed and ENABLE_WEB_SERVER should stay false.
"""
from aiohttp import web

from config import Config


async def health(_: web.Request) -> web.Response:
    return web.Response(text="OK", status=200)


async def start_health_server(config: Config, logger) -> None:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    logger.info("Health server listening on port %s", config.PORT)
