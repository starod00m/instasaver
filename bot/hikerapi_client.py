"""Asynchronous client for HikerAPI (Instagram media resolver).

HikerAPI is a commercial wrapper over Instagram's private API (based on
``instagrapi``). We use it to resolve an Instagram reel/post URL to a direct
mp4 CDN URL that can then be downloaded without touching ``yt-dlp``.

The client talks to ``GET /v1/media/by/url``; the response is the Instagram
``Media`` object with ``video_url`` at the top level (see
docs/plans/2026-04-18-hikerapi-instagram-fallback.md and the endpoint memory).
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

HIKERAPI_BASE_URL = "https://api.hikerapi.com"
HIKERAPI_MEDIA_BY_URL = "/v1/media/by/url"
HIKERAPI_REQUEST_TIMEOUT_SECONDS = 30.0


class HikerAPIClient:
    """Thin async wrapper around HikerAPI for resolving Instagram media URLs.

    The client never raises on network/HTTP errors — failures are logged and
    returned as ``(None, error_msg)`` tuples so the handler layer can decide
    how to report them to the user. This mirrors :func:`bot.downloader.download_video`.

    The underlying :class:`aiohttp.ClientSession` is owned by the caller
    (created in the bot's startup hook, closed in shutdown). We do **not** own
    or close it here.
    """

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Initialise the client with a pre-created session and API key.

        :param session: Shared ``aiohttp.ClientSession`` owned by the caller.
        :type session: aiohttp.ClientSession
        :param api_key: HikerAPI access key (sent as ``x-access-key`` header).
        :type api_key: str
        :return: None
        """
        self._session: aiohttp.ClientSession = session
        self._api_key: str = api_key

    async def get_reel_media_url(
        self,
        instagram_url: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """Resolve an Instagram reel/post URL to a direct mp4 CDN URL.

        :param instagram_url: Public Instagram reel/post URL (``/reel/…``,
            ``/p/…``, ``/tv/…``).
        :type instagram_url: str
        :return: ``(mp4_url, error_msg)``. On success ``error_msg is None``;
            on any failure ``mp4_url is None`` and ``error_msg`` is a short
            human-readable string suitable for further classification.
        :rtype: tuple[Optional[str], Optional[str]]
        """
        request_url = f"{HIKERAPI_BASE_URL}{HIKERAPI_MEDIA_BY_URL}"
        headers = {
            "accept": "application/json",
            "x-access-key": self._api_key,
        }
        params = {"url": instagram_url}

        logger.debug(
            f"HikerAPI request: GET {HIKERAPI_MEDIA_BY_URL}?url={instagram_url}"
        )

        timeout = aiohttp.ClientTimeout(total=HIKERAPI_REQUEST_TIMEOUT_SECONDS)

        try:
            async with self._session.get(
                url=request_url,
                headers=headers,
                params=params,
                timeout=timeout,
            ) as response:
                status = response.status
                if status == 200:
                    payload = await response.json()
                    return self._extract_video_url(payload=payload)

                # Log response body for non-2xx to aid debugging, but only at
                # ERROR level without the API key (never in headers dump).
                body_text = await response.text()
                # Trim overly long bodies to keep logs readable.
                body_preview = body_text[:500]
                logger.error(
                    f"HikerAPI non-200 response: status={status}, body={body_preview!r}"
                )

                if status == 404:
                    return None, "not_found"
                if status == 429:
                    return None, "rate_limited"
                if status == 402:
                    return None, "payment_required"
                if status == 401 or status == 403:
                    return None, "unauthorized"
                return None, f"http_{status}"

        except aiohttp.ClientResponseError as e:
            logger.error(f"HikerAPI client response error: {e}")
            return None, f"client_error: {e}"
        except aiohttp.ClientError as e:
            logger.error(f"HikerAPI network error: {e}")
            return None, f"network_error: {e}"
        except TimeoutError:
            logger.error(
                f"HikerAPI timeout after {HIKERAPI_REQUEST_TIMEOUT_SECONDS}s: "
                f"{instagram_url}"
            )
            return None, "timeout"
        except Exception as e:
            logger.error(f"HikerAPI unexpected error: {e}")
            return None, f"unexpected: {e}"

    def _extract_video_url(
        self,
        payload: object,
    ) -> tuple[Optional[str], Optional[str]]:
        """Pull the direct mp4 URL out of a HikerAPI ``Media`` JSON payload.

        The v1 endpoint returns a flat object with ``video_url`` at the top
        level for single videos (reels and video posts). For carousels
        (``media_type == 8``) the top-level ``video_url`` is absent and the
        video lives inside ``resources[i].video_url``; we pick the first
        video resource in that case.

        :param payload: Parsed JSON response from HikerAPI.
        :type payload: object
        :return: ``(mp4_url, None)`` on success, ``(None, error_msg)`` when
            the payload has no usable video URL.
        :rtype: tuple[Optional[str], Optional[str]]
        """
        if not isinstance(payload, dict):
            logger.error(f"HikerAPI unexpected payload type: {type(payload).__name__}")
            return None, "unexpected_payload"

        media_code = payload.get("code")
        media_type = payload.get("media_type")
        product_type = payload.get("product_type")

        video_url = payload.get("video_url")
        if isinstance(video_url, str) and video_url != "":
            logger.info(
                f"HikerAPI ok: code={media_code} media_type={media_type} "
                f"product_type={product_type} video_url=<present>"
            )
            return video_url, None

        # Carousel — pick the first video resource.
        resources = payload.get("resources")
        if isinstance(resources, list):
            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                resource_video_url = resource.get("video_url")
                if isinstance(resource_video_url, str) and resource_video_url != "":
                    logger.info(
                        f"HikerAPI ok (carousel): code={media_code} "
                        f"media_type={media_type} product_type={product_type} "
                        f"video_url=<present>"
                    )
                    return resource_video_url, None

        logger.warning(
            f"HikerAPI response has no video_url: code={media_code} "
            f"media_type={media_type} product_type={product_type}"
        )
        return None, "no_video_url"
