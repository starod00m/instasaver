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

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from bot.config import Config
from bot.downloader import (
    can_bot_delete_messages,
    cleanup_file,
    cleanup_info_json,
    download_video,
    extract_video_description,
    get_video_dimensions,
)
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
) -> None:
    """Handle plain text messages, extract Instagram/TikTok URLs, send videos.

    :param message: Incoming message.
    :type message: Message
    :param bot: Bot instance injected by aiogram.
    :type bot: Bot
    :param config: Runtime configuration injected by the dispatcher.
    :type config: Config
    :param stats_tracker: Google Sheets stats client injected by the dispatcher.
    :type stats_tracker: GoogleSheetsStats
    :return: None
    """
    if message.text is None:
        return
    if message.from_user is None:
        return

    instagram_match = INSTAGRAM_REELS_PATTERN.search(message.text)
    tiktok_match = TIKTOK_PATTERN.search(message.text)

    if instagram_match is None and tiktok_match is None:
        return

    video_url: Optional[str] = None
    platform: Optional[str] = None
    use_proxy = False

    if instagram_match is not None:
        video_url = instagram_match.group(0)
        platform = "Instagram"
        use_proxy = False
    elif tiktok_match is not None:
        video_url = tiktok_match.group(0)
        platform = "TikTok"
        use_proxy = True  # TikTok requires a proxy in most regions

    if video_url is None or platform is None:
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
        video_path, error_msg = await download_video(
            url=video_url,
            temp_dir=config.temp_dir,
            proxy_url=config.proxy_url,
            use_proxy=use_proxy,
        )

        if video_path is None:
            logger.error(
                f"Failed to download video from {platform}. "
                f"URL: {video_url}. Error: {error_msg}"
            )

            error_text = "❌ Не удалось скачать видео."

            if error_msg is not None:
                error_msg_lower = error_msg.lower()

                if (
                    "this content may be inappropriate" in error_msg_lower
                    or "inappropriate" in error_msg_lower
                ):
                    error_text += (
                        "\n\n💬 Видео может содержать контент для взрослых или "
                        "материалы, которые требуют входа в аккаунт для просмотра."
                    )
                    logger.info(f"Inappropriate content error for URL: {video_url}")
                elif "private" in error_msg_lower or "приватн" in error_msg_lower:
                    error_text += (
                        "\n\n🔒 Видео является приватным и недоступно для скачивания."
                    )
                    logger.info(f"Private content error for URL: {video_url}")
                elif "not available" in error_msg_lower or "unavailable" in error_msg_lower:
                    error_text += (
                        "\n\n🚫 Видео недоступно. Возможно, оно было удалено или скрыто автором."
                    )
                    logger.info(f"Content not available for URL: {video_url}")
                elif "age" in error_msg_lower and "restrict" in error_msg_lower:
                    error_text += (
                        "\n\n🔞 Видео имеет возрастные ограничения и требует входа в аккаунт."
                    )
                    logger.info(f"Age-restricted content for URL: {video_url}")
                elif "login" in error_msg_lower or "sign in" in error_msg_lower:
                    error_text += (
                        "\n\n🔑 Для скачивания этого видео требуется авторизация в аккаунте."
                    )
                    logger.info(f"Login required for URL: {video_url}")
                elif (
                    "geo" in error_msg_lower
                    or "region" in error_msg_lower
                    or "country" in error_msg_lower
                ):
                    error_text += (
                        "\n\n🌍 Видео недоступно в вашем регионе из-за географических ограничений."
                    )
                    logger.info(f"Geo-restricted content for URL: {video_url}")
                elif (
                    "429" in error_msg
                    or "rate limit" in error_msg_lower
                    or "too many requests" in error_msg_lower
                ):
                    error_text += (
                        "\n\n⏱️ Слишком много запросов. Пожалуйста, попробуйте позже."
                    )
                    logger.info(f"Rate limit error for URL: {video_url}")
                else:
                    error_text += f"\n\n⚠️ Техническая информация:\n{error_msg}"
                    logger.warning(f"Unknown error type for URL: {video_url}")
            else:
                error_text += "\n\n❓ Возможно, контент недоступен или является приватным."
                logger.warning(f"No error message provided for failed download: {video_url}")

            await status_message.edit_text(text=error_text)

            asyncio.create_task(
                stats_tracker.log_download_error(
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    platform=platform,
                    url=video_url,
                    error_msg=error_msg or "Unknown error",
                )
            )
            return

        logger.info(
            f"Video downloaded: {video_path.name} ({video_path.stat().st_size // 1024} KB)"
        )

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
        await status_message.edit_text(text="❌ Произошла ошибка при обработке запроса.")
        if video_path is not None:
            await cleanup_info_json(video_path=video_path)
            await cleanup_file(file_path=video_path)
