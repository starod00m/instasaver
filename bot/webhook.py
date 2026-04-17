"""Telegram webhook HTTP handler.

Implements the ``POST /webhook`` endpoint Telegram posts updates to. The
handler validates the ``X-Telegram-Bot-Api-Secret-Token`` header using
:func:`hmac.compare_digest` and feeds valid updates into the aiogram
dispatcher.
"""

import hmac
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiohttp import web

logger = logging.getLogger(__name__)


TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def make_webhook_handler(
    dispatcher: Dispatcher,
    bot: Bot,
    webhook_secret: str,
) -> web.RequestHandler:
    """Build an aiohttp handler for Telegram webhook POSTs.

    :param dispatcher: Configured aiogram dispatcher.
    :type dispatcher: Dispatcher
    :param bot: Bot instance that owns the token for this webhook.
    :type bot: Bot
    :param webhook_secret: Expected value of the
        ``X-Telegram-Bot-Api-Secret-Token`` header.
    :type webhook_secret: str
    :return: Async aiohttp request handler.
    :rtype: aiohttp.web.RequestHandler
    """

    async def handle(request: web.Request) -> web.Response:
        """Validate secret and dispatch the incoming Telegram update.

        :param request: Incoming aiohttp request.
        :type request: aiohttp.web.Request
        :return: Empty response with HTTP 200 on success, HTTP 401 on secret
            mismatch, HTTP 400 on malformed body.
        :rtype: aiohttp.web.Response
        """
        provided_secret = request.headers.get(TELEGRAM_SECRET_HEADER, "")
        if not hmac.compare_digest(provided_secret, webhook_secret):
            logger.warning(
                f"Webhook request with invalid or missing secret token "
                f"from {request.remote}"
            )
            return web.Response(status=401, text="unauthorized")

        try:
            payload = await request.json()
        except Exception as e:
            logger.warning(f"Failed to parse webhook payload as JSON: {e}")
            return web.Response(status=400, text="bad request")

        try:
            update = Update.model_validate(payload, context={"bot": bot})
        except Exception as e:
            logger.warning(f"Failed to validate Telegram update: {e}")
            return web.Response(status=400, text="bad request")

        try:
            await dispatcher.feed_webhook_update(bot=bot, update=update)
        except Exception as e:
            # feed_webhook_update normally swallows handler errors, but defend
            # against unexpected dispatcher-level failures so we do not leak 500s
            # to Telegram (Telegram would keep retrying).
            logger.exception(f"Unhandled dispatcher error: {e}")

        return web.Response(status=200, text="ok")

    return handle
