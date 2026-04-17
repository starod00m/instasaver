"""Instasaver bot entrypoint.

Starts an aiohttp server exposing ``GET /health`` and ``POST /webhook``,
wires up the aiogram dispatcher, idempotently registers the webhook with
Telegram, and handles SIGTERM/SIGINT with a 10-second graceful shutdown
budget.

Run with::

    python -m bot
"""

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiohttp import web

from bot.config import Config
from bot.handlers import router
from bot.health import health_handler
from bot.stats import GoogleSheetsStats
from bot.webhook import make_webhook_handler

logger = logging.getLogger(__name__)


def _configure_logging(level_name: str) -> None:
    """Configure the root logger to write to stdout with the requested level.

    :param level_name: Logging level name (e.g. ``INFO``, ``DEBUG``).
    :type level_name: str
    :return: None
    """
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
        force=True,
    )


async def _ensure_webhook(bot: Bot, config: Config) -> None:
    """Register the webhook with Telegram.

    If ``WEBHOOK_URL`` is not configured, this is a no-op — the bot runs in
    "dev" mode where webhook management is left to the operator (see README).
    Otherwise ``setWebhook`` is called unconditionally on every startup.
    Telegram's ``setWebhook`` is idempotent, so calling it with unchanged
    parameters is cheap and safe; calling it unconditionally also correctly
    handles secret-token rotation (where only ``WEBHOOK_SECRET`` changes).

    :param bot: Bot instance.
    :type bot: Bot
    :param config: Runtime configuration.
    :type config: Config
    :return: None
    """
    if config.webhook_url == "":
        logger.warning("WEBHOOK_URL not set — skipping setWebhook (dev mode)")
        return

    logger.info(f"Registering webhook: {config.webhook_url!r}")
    await bot.set_webhook(
        url=config.webhook_url,
        secret_token=config.webhook_secret,
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query"],
    )


def _build_app(
    dispatcher: Dispatcher,
    bot: Bot,
    config: Config,
) -> web.Application:
    """Build the aiohttp application exposing ``/health`` and ``/webhook``.

    :param dispatcher: Configured aiogram dispatcher.
    :type dispatcher: Dispatcher
    :param bot: Bot instance.
    :type bot: Bot
    :param config: Runtime configuration.
    :type config: Config
    :return: aiohttp application with routes attached.
    :rtype: aiohttp.web.Application
    """
    app = web.Application()

    app.router.add_get(path="/health", handler=health_handler)

    webhook_handler = make_webhook_handler(
        dispatcher=dispatcher,
        bot=bot,
        webhook_secret=config.webhook_secret,
    )
    app.router.add_post(path=config.webhook_path(), handler=webhook_handler)

    return app


async def _run() -> None:
    """Run the HTTP server until a termination signal is received.

    :return: None
    """
    config = Config()
    _configure_logging(level_name=config.log_level)

    logger.info("Bot starting")
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Temp directory: {config.temp_dir}")

    stats_tracker = GoogleSheetsStats()

    bot = Bot(token=config.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router=router)

    # Inject per-request dependencies consumed by handlers via aiogram DI.
    dispatcher["config"] = config
    dispatcher["stats_tracker"] = stats_tracker

    app = _build_app(dispatcher=dispatcher, bot=bot, config=config)

    runner = web.AppRunner(app=app, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(
        runner=runner,
        host="0.0.0.0",
        port=config.port,
        shutdown_timeout=config.shutdown_timeout_seconds,
    )
    await site.start()
    logger.info(f"HTTP server listening on 0.0.0.0:{config.port}")

    try:
        await _ensure_webhook(bot=bot, config=config)
    except Exception as e:
        # Don't crash on Telegram API hiccups at startup — /health must stay up.
        logger.error(f"Failed to ensure webhook: {e}")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal(signum: int) -> None:
        """Signal handler: request graceful shutdown.

        :param signum: Received signal number.
        :type signum: int
        :return: None
        """
        logger.info(f"Received signal {signum}, shutting down")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    try:
        await stop_event.wait()
    finally:
        logger.info(
            f"Shutdown: draining connections "
            f"(deadline {config.shutdown_timeout_seconds}s)"
        )
        try:
            await asyncio.wait_for(
                runner.cleanup(),
                timeout=config.shutdown_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Shutdown: drain deadline exceeded, forcing stop")
        finally:
            await bot.session.close()
            logger.info("Bot stopped")


def main() -> None:
    """Synchronous entrypoint usable with ``python -m bot``.

    :return: None
    """
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        # asyncio.run may re-raise KeyboardInterrupt if it arrives before the
        # signal handler is installed. Treat that as a normal exit.
        pass


if __name__ == "__main__":
    main()
