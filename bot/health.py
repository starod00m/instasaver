"""HTTP health endpoint.

The Docker HEALTHCHECK probes ``GET /health`` via curl. The endpoint must be
registered as early as possible so the container reports healthy once the
HTTP server is up, independently of any outbound Telegram API calls.
"""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def health_handler(request: web.Request) -> web.Response:
    """Return a liveness response.

    This endpoint intentionally does not perform any upstream checks — it
    only proves the HTTP listener is alive.

    :param request: Incoming aiohttp request.
    :type request: aiohttp.web.Request
    :return: JSON response with ``{"status": "ok"}`` and HTTP 200.
    :rtype: aiohttp.web.Response
    """
    return web.json_response(data={"status": "ok"})
