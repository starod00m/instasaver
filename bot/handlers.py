"""Telegram update handlers.

Handlers receive the :class:`bot.config.Config` and
:class:`bot.stats.GoogleSheetsStats` instances via aiogram's dependency
injection — they are registered on the dispatcher under the keys ``config``
and ``stats_tracker`` in :mod:`bot.__main__`.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from bot.config import Config
from bot.downloader import (
    can_bot_delete_messages,
    cleanup_file,
    cleanup_info_json,
    download_direct_url,
    download_video,
    extract_video_description,
    get_video_dimensions,
)
from bot.hikerapi_client import HikerAPIClient
from bot.stats import GoogleSheetsStats

logger = logging.getLogger(__name__)

router = Router(name="instasaver")


INSTAGRAM_REELS_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/(reel|p|tv)/[\w-]+/?",
    re.IGNORECASE,
)

TIKTOK_PATTERN = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/[@\w\-/.?=&]+",
    re.IGNORECASE,
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Handle the ``/start`` command.

    :param message: Incoming message.
    :type message: Message
    :return: None
    """
    await message.answer(
        "👋 Привет! Я бот для скачивания видео из Instagram и TikTok.\n\n"
        "Просто отправь мне ссылку на Instagram Reels или TikTok видео, "
        "и я скачаю его для тебя.\n\n"
        "Также можешь добавить меня в группу или канал - "
        "я буду автоматически скачивать все видео из отправленных ссылок.\n\n"
        "Используй /help для получения дополнительной информации."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle the ``/help`` command.

    :param message: Incoming message.
    :type message: Message
    :return: None
    """
    await message.answer(
        "ℹ️ <b>Как использовать бота:</b>\n\n"
        "<b>Вариант 1: Личный чат</b>\n"
        "Просто отправьте мне ссылку на Instagram Reels или TikTok видео, "
        "и я скачаю его для вас.\n\n"
        "<b>Вариант 2: Группа или канал</b>\n"
        "1. Добавьте меня в группу или канал\n"
        "2. Убедитесь, что Privacy Mode отключен (см. @BotFather)\n"
        "3. Отправьте ссылку на видео\n"
        "4. Бот скачает видео и отправит его в ответ\n\n"
        "<b>Поддерживаемые ссылки:</b>\n"
        "• instagram.com/reel/...\n"
        "• instagram.com/p/...\n"
        "• instagram.com/tv/...\n"
        "• tiktok.com/@username/video/...\n"
        "• vm.tiktok.com/...\n"
        "• vt.tiktok.com/...\n\n"
        "<b>Примечание:</b> Работает только с публичным контентом.",
        parse_mode="HTML",
    )


@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    config: Config,
    stats_tracker: GoogleSheetsStats,
) -> None:
    """Handle the ``/stats`` command (admin only).

    Shows last-30-day usage stats. Available only to the administrator whose
    Telegram id is configured via ``ADMIN_USER_ID``.

    :param message: Incoming message.
    :type message: Message
    :param config: Runtime configuration injected by the dispatcher.
    :type config: Config
    :param stats_tracker: Google Sheets stats client injected by the dispatcher.
    :type stats_tracker: GoogleSheetsStats
    :return: None
    """
    if message.from_user is None:
        return

    admin_id = config.admin_user_id
    if admin_id is None or str(message.from_user.id) != admin_id:
        logger.debug(
            f"Stats request from non-admin user {message.from_user.id}, ignoring. "
            f"Admin ID: {admin_id}"
        )
        return

    logger.info(f"Stats request from admin user {message.from_user.id}")

    status_msg = await message.answer("📊 Получаю статистику...")

    try:
        logger.debug("Requesting stats for 30 days")
        stats_data = await stats_tracker.get_stats(days=30)

        if stats_data is not None:
            logger.info(
                f"Stats retrieved successfully: {stats_data['total']} total records, "
                f"{stats_data['success']} successful"
            )
            formatted_message = stats_tracker.format_stats_message(stats=stats_data)
            await status_msg.edit_text(text=formatted_message, parse_mode="HTML")
            logger.info(f"Stats sent to admin {message.from_user.id}")
        else:
            logger.warning("Stats data is None, Google Sheets may not be configured")
            await status_msg.edit_text(
                text=(
                    "❌ Не удалось получить статистику.\n"
                    "Проверьте настройки Google Sheets API."
                )
            )

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        logger.debug(f"Exception type: {type(e).__name__}")
        await status_msg.edit_text(text="❌ Произошла ошибка при получении статистики.")


@router.message(F.text)
async def handle_message(
    message: Message,
    bot: Bot,
    config: Config,
    stats_tracker: GoogleSheetsStats,
    hikerapi_client: Optional[HikerAPIClient],
    http_session: aiohttp.ClientSession,
) -> None:
    """Handle plain text messages, extract Instagram/TikTok URLs, send videos.

    Instagram URLs are resolved through :class:`HikerAPIClient` (Meta CDN
    direct mp4 URL) and downloaded via :func:`download_direct_url`. TikTok
    URLs keep the original ``yt-dlp`` path through :func:`download_video`,
    since yt-dlp + proxy works for TikTok in our deployment environment.

    :param message: Incoming message.
    :type message: Message
    :param bot: Bot instance injected by aiogram.
    :type bot: Bot
    :param config: Runtime configuration injected by the dispatcher.
    :type config: Config
    :param stats_tracker: Google Sheets stats client injected by the dispatcher.
    :type stats_tracker: GoogleSheetsStats
    :param hikerapi_client: HikerAPI client injected by the dispatcher; may be
        ``None`` when ``HIKERAPI_KEY`` is unset — in that case Instagram URLs
        get a configuration-error message back.
    :type hikerapi_client: Optional[HikerAPIClient]
    :param http_session: Shared aiohttp session for direct CDN downloads.
    :type http_session: aiohttp.ClientSession
    :return: None
    """
    if message.text is None:
        return
    if message.from_user is None:
        return

    instagram_match = INSTAGRAM_REELS_PATTERN.search(message.text)
    tiktok_match = TIKTOK_PATTERN.search(message.text)

    video_url: str
    platform: str
    if instagram_match is not None:
        video_url = instagram_match.group(0)
        platform = "Instagram"
    elif tiktok_match is not None:
        video_url = tiktok_match.group(0)
        platform = "TikTok"
    else:
        return

    logger.info(f"Detected {platform} URL: {video_url}")

    logger.debug(
        f"Checking bot delete permissions in chat {message.chat.id} "
        f"(type={message.chat.type})"
    )
    bot_can_delete = await can_bot_delete_messages(message=message, bot=bot)
    logger.debug(f"bot_can_delete={bot_can_delete}")

    status_message = await message.reply(text="⏳ Скачиваю видео...")

    video_path: Optional[Path] = None
    try:
        if platform == "Instagram":
            video_path, error_code = await _download_instagram(
                instagram_url=video_url,
                temp_dir=config.temp_dir,
                hikerapi_client=hikerapi_client,
                http_session=http_session,
                status_message=status_message,
            )
            error_detail = _instagram_error_detail(error_code=error_code)
        else:
            video_path, yt_dlp_error = await download_video(
                url=video_url,
                temp_dir=config.temp_dir,
                proxy_url=config.proxy_url,
                use_proxy=True,
            )
            error_detail = _tiktok_error_detail(error_msg=yt_dlp_error)
            # Keep the raw yt-dlp stderr for stats only; don't show it to users.
            error_code = yt_dlp_error

        if video_path is None:
            logger.error(
                f"Failed to download video from {platform}. "
                f"URL: {video_url}. Error: {error_code}"
            )
            error_text = (
                error_detail
                if error_detail is not None
                else ("❌ Не удалось скачать видео.")
            )
            await status_message.edit_text(text=error_text)

            asyncio.create_task(
                stats_tracker.log_download_error(
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    platform=platform,
                    url=video_url,
                    error_msg=_safe_stats_error(error_code=error_code),
                )
            )
            return

        logger.info(
            f"Video downloaded: {video_path.name} ({video_path.stat().st_size // 1024} KB)"
        )

        # Instagram path goes through HikerAPI + direct CDN download, so there's
        # no yt-dlp .info.json file — caption is just the original reel URL so
        # the user can jump back to the source. TikTok still uses yt-dlp and
        # keeps its richer description.
        if platform == "Instagram":
            description: Optional[str] = video_url
        else:
            description = await extract_video_description(video_path=video_path)
            if description is not None:
                logger.debug(f"Description extracted: {len(description)} chars")
            else:
                logger.debug("Description: None")

        width, height = await get_video_dimensions(video_path=video_path)
        logger.debug(f"Video dimensions: {width}x{height}")

        video_file = FSInputFile(path=video_path)

        if bot_can_delete:
            await message.answer_video(
                video=video_file,
                width=width if width > 0 else None,
                height=height if height > 0 else None,
                caption=description,
            )
            await status_message.delete()
            try:
                await message.delete()
            except (TelegramBadRequest, TelegramForbiddenError) as e:
                logger.warning(
                    f"Could not delete original message {message.message_id}: {e}"
                )
        else:
            await message.reply_video(
                video=video_file,
                width=width if width > 0 else None,
                height=height if height > 0 else None,
                caption=description,
            )
            await status_message.delete()

        asyncio.create_task(
            stats_tracker.log_download_success(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                platform=platform,
                url=video_url,
            )
        )

        await cleanup_info_json(video_path=video_path)
        await cleanup_file(file_path=video_path)

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await status_message.edit_text(
            text="❌ Произошла ошибка при обработке запроса."
        )
        if video_path is not None:
            await cleanup_info_json(video_path=video_path)
            await cleanup_file(file_path=video_path)


async def _download_instagram(
    instagram_url: str,
    temp_dir: Path,
    hikerapi_client: Optional[HikerAPIClient],
    http_session: aiohttp.ClientSession,
    status_message: Message,
) -> tuple[Optional[Path], Optional[str]]:
    """Resolve an Instagram URL via HikerAPI, then stream the mp4 from CDN.

    Updates ``status_message`` between the two stages so the user isn't
    staring at "Скачиваю видео..." for up to 90 seconds in the worst case.

    :param instagram_url: Instagram reel/post URL matched by the handler.
    :param temp_dir: Directory to place downloaded files in.
    :param hikerapi_client: Configured client, or ``None`` when the key is
        not set.
    :param http_session: Shared aiohttp session for the CDN GET.
    :param status_message: Reply message we're editing for progress updates.
    :return: ``(path_to_video, error_code)``. ``error_code`` is a stable
        token from :mod:`bot.hikerapi_client` or :func:`download_direct_url`,
        never a raw exception string.
    """
    if hikerapi_client is None:
        return None, "hikerapi_key_missing"

    direct_url, error_code = await hikerapi_client.get_reel_media_url(
        instagram_url=instagram_url,
    )
    if direct_url is None:
        return None, error_code

    # Stage transition: user sees something happened without us sending a
    # second message (edit_text is free of rate-limit concerns for a single
    # chat and keeps the UI compact).
    try:
        await status_message.edit_text(text="⬇️ Загружаю видео...")
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.debug(f"Could not update status message: {e}")

    return await download_direct_url(
        direct_url=direct_url,
        temp_dir=temp_dir,
        session=http_session,
    )


def _instagram_error_detail(error_code: Optional[str]) -> Optional[str]:
    """Map an Instagram-path error code to a user-facing message.

    Covers codes emitted by :mod:`bot.hikerapi_client` and
    :func:`download_direct_url`. Anything unknown falls through to a generic
    "technical error" message — raw exception strings must never reach users.

    :param error_code: Stable error token, or ``None``.
    :return: User-facing message, or ``None`` to signal "use the default".
    """
    if error_code is None:
        return None
    table: dict[str, str] = {
        "hikerapi_key_missing": (
            "⚙️ Instagram временно не обслуживается: не задан HIKERAPI_KEY. "
            "Сообщите администратору."
        ),
        "not_found": "❌ Не удалось скачать видео.\n\n🚫 Пост недоступен или был удалён.",
        "rate_limited": (
            "❌ Не удалось скачать видео.\n\n"
            "⏱️ Сервис временно перегружен, попробуйте через минуту."
        ),
        "payment_required": (
            "❌ Не удалось скачать видео.\n\n"
            "💳 Временные проблемы с сервисом, сообщите администратору."
        ),
        "unauthorized": (
            "❌ Не удалось скачать видео.\n\n"
            "🔑 Сервис отклонил авторизацию, сообщите администратору."
        ),
        "no_video_url": "❌ Не удалось скачать видео.\n\n🖼️ В посте нет видео для скачивания.",
        "timeout": (
            "❌ Не удалось скачать видео.\n\n"
            "⏳ Не удалось получить видео, попробуйте позже."
        ),
        "disallowed_host": (
            "❌ Не удалось скачать видео.\n\n"
            "⚠️ Технический сбой, сообщите администратору."
        ),
        "forbidden": "❌ Не удалось скачать видео.\n\n🚫 Видео недоступно для скачивания.",
        "file_too_large": (
            "❌ Не удалось скачать видео.\n\n"
            "📦 Видео слишком большое для Telegram (>50 МБ)."
        ),
        "technical_error": "❌ Не удалось скачать видео.\n\n⚠️ Технический сбой, попробуйте позже.",
    }
    return table.get(
        error_code,
        "❌ Не удалось скачать видео.\n\n⚠️ Технический сбой, попробуйте позже.",
    )


def _tiktok_error_detail(error_msg: Optional[str]) -> Optional[str]:
    """Classify a yt-dlp stderr string for TikTok into a user-facing message.

    yt-dlp returns free-form stderr; we only pattern-match on enough markers
    to give the user a meaningful hint, and fall through to a generic message
    otherwise. Raw stderr is **never** shown to the user — it may contain
    proxy URLs or full video IDs.

    :param error_msg: yt-dlp stderr text, or ``None``.
    :return: User-facing message, or ``None`` when nothing's available.
    """
    if error_msg is None:
        return "❌ Не удалось скачать видео.\n\n❓ Возможно, контент недоступен или является приватным."

    lower = error_msg.lower()
    if "private" in lower or "приватн" in lower:
        return "❌ Не удалось скачать видео.\n\n🔒 Видео является приватным и недоступно для скачивания."
    if "not available" in lower or "unavailable" in lower:
        return "❌ Не удалось скачать видео.\n\n🚫 Видео недоступно. Возможно, оно было удалено или скрыто автором."
    if "age" in lower and "restrict" in lower:
        return "❌ Не удалось скачать видео.\n\n🔞 Видео имеет возрастные ограничения и требует входа в аккаунт."
    if "login" in lower or "sign in" in lower:
        return "❌ Не удалось скачать видео.\n\n🔑 Для скачивания этого видео требуется авторизация в аккаунте."
    if "geo" in lower or "region" in lower or "country" in lower:
        return "❌ Не удалось скачать видео.\n\n🌍 Видео недоступно в вашем регионе из-за географических ограничений."
    if "429" in error_msg or "rate limit" in lower or "too many requests" in lower:
        return "❌ Не удалось скачать видео.\n\n⏱️ Слишком много запросов. Пожалуйста, попробуйте позже."
    return "❌ Не удалось скачать видео.\n\n⚠️ Технический сбой, попробуйте позже."


# Allowlist of stable error codes we accept into stats. Anything not on this
# list (raw exception strings, yt-dlp stderr, unknown tokens) becomes
# ``"other"`` — stats must not become a dumping ground for traceback strings.
_SAFE_STATS_ERROR_CODES: frozenset[str] = frozenset(
    {
        "hikerapi_key_missing",
        "not_found",
        "rate_limited",
        "payment_required",
        "unauthorized",
        "no_video_url",
        "timeout",
        "disallowed_host",
        "forbidden",
        "file_too_large",
        "technical_error",
    }
)


def _safe_stats_error(error_code: Optional[str]) -> str:
    """Return a stats-safe error label.

    :param error_code: Error token; yt-dlp stderr or raw exception string.
    :return: ``error_code`` if it's a known stable token, else ``"other"``.
    """
    if error_code is None:
        return "unknown"
    if error_code in _SAFE_STATS_ERROR_CODES:
        return error_code
    return "other"
