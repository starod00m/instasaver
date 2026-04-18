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

HIKERAPI_MEDIA_BY_URL = "https://api.hikerapi.com/v1/media/by/url"
HIKERAPI_REQUEST_TIMEOUT_SECONDS = 30.0


class HikerAPIClient:
    """Thin async wrapper around HikerAPI for resolving Instagram media URLs.

    The client never raises on network/HTTP errors — failures are logged and
    returned as ``(None, error_msg)`` tuples so the handler layer can decide
    how to report them to the user. Error codes are stable identifiers
    (``not_found``, ``rate_limited``, …), never raw exception strings — those
    can carry URLs/headers and must not leak to users or stats.
    """

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Initialise the client with a pre-created session and API key.

        :param session: Shared ``aiohttp.ClientSession`` owned by the caller.
        :param api_key: HikerAPI access key (sent as ``x-access-key`` header).
        """
        self._session: aiohttp.ClientSession = session
        self._api_key: str = api_key

    async def get_reel_media_url(
        self,
        instagram_url: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """Resolve an Instagram reel/post URL to a direct mp4 CDN URL.

        :param instagram_url: Public Instagram reel/post URL.
        :return: ``(mp4_url, error_code)``. On success ``error_code is None``;
            on failure ``mp4_url is None`` and ``error_code`` is one of the
            stable tokens defined in the module docstring.
        """
        headers = {
            "accept": "application/json",
            "x-access-key": self._api_key,
        }
        params = {"url": instagram_url}

        logger.debug(f"HikerAPI request: GET /v1/media/by/url?url={instagram_url}")

        timeout = aiohttp.ClientTimeout(total=HIKERAPI_REQUEST_TIMEOUT_SECONDS)

        try:
            async with self._session.get(
                url=HIKERAPI_MEDIA_BY_URL,
                headers=headers,
                params=params,
                timeout=timeout,
            ) as response:
                status = response.status
                if status == 200:
                    payload = await response.json()
                    return self._extract_video_url(payload=payload)

                # Don't log the response body: HikerAPI errors can echo
                # request-derived data, and a malicious/misconfigured upstream
                # could reflect auth headers back into the body.
                logger.error(f"HikerAPI non-200 response: status={status}")

                if status == 404:
                    return None, "not_found"
                if status == 429:
                    return None, "rate_limited"
                if status == 402:
                    return None, "payment_required"
                if status == 401 or status == 403:
                    return None, "unauthorized"
                return None, "technical_error"

        except TimeoutError:
            logger.error(f"HikerAPI timeout after {HIKERAPI_REQUEST_TIMEOUT_SECONDS}s")
            return None, "timeout"
        except aiohttp.ClientError as e:
            # Log type only — aiohttp error messages can contain the request
            # URL and, in rare redirect/reflect scenarios, headers.
            logger.error(f"HikerAPI network error: {type(e).__name__}")
            return None, "technical_error"
        except Exception as e:
            logger.error(f"HikerAPI unexpected error: {type(e).__name__}")
            return None, "technical_error"

    def _extract_video_url(
        self,
        payload: object,
    ) -> tuple[Optional[str], Optional[str]]:
        """Pull the direct mp4 URL out of a HikerAPI ``Media`` JSON payload.

        For single videos (reels, video posts) the v1 endpoint returns a flat
        object with ``video_url`` at the top level. Carousels aren't handled
        here — per the plan we skip them.

        :param payload: Parsed JSON response from HikerAPI.
        :return: ``(mp4_url, None)`` on success, ``(None, error_code)`` when
            the payload has no usable video URL.
        """
        if not isinstance(payload, dict):
            logger.error(f"HikerAPI unexpected payload type: {type(payload).__name__}")
            return None, "technical_error"

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

        logger.warning(
            f"HikerAPI response has no video_url: code={media_code} "
            f"media_type={media_type} product_type={product_type}"
        )
        return None, "no_video_url"
