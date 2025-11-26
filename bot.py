"""Instagram Reels and TikTok downloader bot for Telegram.

This bot monitors messages in private chats, groups, and channels, detects Instagram Reels
and TikTok URLs, downloads videos using yt-dlp, and replies with the downloaded content.
"""

import asyncio
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Optional

import aiofiles.os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

from stats import GoogleSheetsStats

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Bot configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
TEMP_DIR = Path("temp")

# Initialize stats tracker
stats = GoogleSheetsStats()

# Log environment variables status
if TELEGRAM_BOT_TOKEN:
    logger.info("TELEGRAM_BOT_TOKEN loaded from environment")
else:
    logger.warning("TELEGRAM_BOT_TOKEN not found in environment")

if PROXY_URL:
    logger.info("PROXY_URL loaded from environment")
else:
    logger.info("PROXY_URL not set (optional)")

# Regex patterns for supported platforms
INSTAGRAM_REELS_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/(reel|p|tv)/[\w-]+/?",
    re.IGNORECASE,
)

TIKTOK_PATTERN = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/[@\w\-/.?=&]+",
    re.IGNORECASE,
)

# Initialize router
router = Router()


def validate_config() -> None:
    """Validate bot configuration.

    :raises SystemExit: If TELEGRAM_BOT_TOKEN is not set
    :return: None
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env file")
        sys.exit(1)


def ensure_temp_directory() -> None:
    """Create temp directory if it doesn't exist.

    :return: None
    """
    TEMP_DIR.mkdir(exist_ok=True)
    logger.info(f"Temp directory: {TEMP_DIR.absolute()}")


async def get_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Extract video dimensions using ffprobe.

    :param video_path: Path to video file
    :type video_path: Path
    :return: Tuple of (width, height)
    :rtype: tuple[int, int]
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(video_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            output = stdout.decode().strip()
            width, height = map(int, output.split("x"))
            logger.info(f"Video dimensions: {width}x{height}")
            return width, height
        else:
            logger.warning("Could not extract video dimensions")
            return 0, 0

    except Exception as e:
        logger.error(f"Error extracting video dimensions: {e}")
        return 0, 0


async def download_video(
    url: str, use_proxy: bool = False, max_retries: int = 3
) -> tuple[Optional[Path], Optional[str]]:
    """Download video from Instagram or TikTok using yt-dlp with retry support.

    :param url: Instagram Reels/post or TikTok URL
    :type url: str
    :param use_proxy: Whether to use proxy for download (required for TikTok)
    :type use_proxy: bool
    :param max_retries: Maximum number of retry attempts
    :type max_retries: int
    :return: Tuple of (Path to downloaded video file or None if download failed, error message or None)
    :rtype: tuple[Optional[Path], Optional[str]]
    """
    # Rate limits to try in order (yt-dlp format: 8M = 8 MiB/s)
    # Start with 8M, then try lower rates if rate-limit errors occur
    rate_limits = ["8M", "4M", "2M", "1M"]

    last_error_msg: Optional[str] = None
    current_rate_limit_index = 0

    # Generate unique download identifier to avoid file collisions
    download_id = str(uuid.uuid4())[:8]

    for attempt in range(max_retries):
        try:
            # Determine rate limit based on rate-limit errors
            current_rate_limit = rate_limits[min(current_rate_limit_index, len(rate_limits) - 1)]

            if attempt > 0:
                logger.info(
                    f"Retry attempt {attempt + 1}/{max_retries} with rate-limit: {current_rate_limit}"
                )
                # Add fixed delay before retry
                await asyncio.sleep(2)

            # Generate unique filename with download_id prefix
            output_template = str(TEMP_DIR / f"{download_id}_%(id)s.%(ext)s")

            # Build yt-dlp command
            cmd = [
                "yt-dlp",
                "--quiet",
                "--no-warnings",
                "--format",
                "best",
                "--limit-rate",
                current_rate_limit,
                "--output",
                output_template,
            ]

            # Add proxy if needed and available
            if use_proxy and PROXY_URL:
                cmd.extend(["--proxy", PROXY_URL])
                if attempt == 0:
                    logger.info("Using proxy for download")
            elif use_proxy and not PROXY_URL:
                logger.warning("Proxy requested but PROXY_URL not set in environment")

            cmd.append(url)

            # Run yt-dlp to download video
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                last_error_msg = error_msg

                # Check if it's a rate-limit error
                error_msg_lower = error_msg.lower()
                is_rate_limit_error = (
                    "429" in error_msg
                    or "rate limit" in error_msg_lower
                    or "rate-limit" in error_msg_lower
                    or "too many requests" in error_msg_lower
                )

                if is_rate_limit_error:
                    logger.warning(
                        f"Rate-limit error detected (attempt {attempt + 1}/{max_retries}): {error_msg}"
                    )
                    # Reduce rate limit for next attempt
                    current_rate_limit_index += 1
                    # Continue to retry with lower rate limit
                    if attempt < max_retries - 1:
                        continue
                else:
                    logger.error(f"yt-dlp error (attempt {attempt + 1}/{max_retries}): {error_msg}")
                    # For non-rate-limit errors, retry might still help
                    if attempt < max_retries - 1:
                        continue

                # Last attempt failed
                return None, error_msg

            # Find the downloaded file with download_id prefix
            files = list(TEMP_DIR.glob(f"{download_id}_*"))
            if not files:
                error_msg = "No file was downloaded"
                last_error_msg = error_msg
                if attempt < max_retries - 1:
                    logger.warning(f"{error_msg} (attempt {attempt + 1}/{max_retries})")
                    continue
                logger.error(f"{error_msg} - all retries exhausted")
                return None, error_msg

            # Get the most recent file (should be only one with our download_id)
            video_file = max(files, key=lambda p: p.stat().st_mtime)
            logger.info(f"Downloaded: {video_file.name} (attempt {attempt + 1})")
            return video_file, None

        except Exception as e:
            error_msg = str(e)
            last_error_msg = error_msg
            logger.error(f"Download error (attempt {attempt + 1}/{max_retries}): {error_msg}")
            if attempt < max_retries - 1:
                continue

    # If we've exhausted all retries
    return None, last_error_msg or "Download failed after all retry attempts"


async def cleanup_file(file_path: Path) -> None:
    """Delete temporary file asynchronously.

    :param file_path: Path to file to delete
    :type file_path: Path
    :return: None
    """
    try:
        await aiofiles.os.remove(file_path)
        logger.info(f"Cleaned up: {file_path.name}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Handle /start command.

    :param message: Incoming message
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
    """Handle /help command.

    :param message: Incoming message
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
async def cmd_stats(message: Message) -> None:
    """Handle /stats command (admin only).

    Показывает статистику использования бота за последние 30 дней.
    Доступна только администратору, указанному в ADMIN_USER_ID.

    :param message: Incoming message
    :type message: Message
    :return: None
    """
    # Проверяем, что команду отправил админ
    if not ADMIN_USER_ID or str(message.from_user.id) != ADMIN_USER_ID:
        logger.debug(
            f"Stats request from non-admin user {message.from_user.id}, ignoring. "
            f"Admin ID: {ADMIN_USER_ID}"
        )
        return

    logger.info(f"Stats request from admin user {message.from_user.id}")

    # Отправляем сообщение о загрузке
    status_msg = await message.answer("📊 Получаю статистику...")

    try:
        # Получаем статистику за последние 30 дней
        logger.debug("Requesting stats for 30 days")
        stats_data = await stats.get_stats(days=30)

        if stats_data:
            logger.info(
                f"Stats retrieved successfully: {stats_data['total']} total records, "
                f"{stats_data['success']} successful"
            )
            # Форматируем и отправляем статистику
            formatted_message = stats.format_stats_message(stats_data)
            await status_msg.edit_text(formatted_message, parse_mode="HTML")
            logger.info(f"Stats sent to admin {message.from_user.id}")
        else:
            logger.warning("Stats data is None, Google Sheets may not be configured")
            await status_msg.edit_text(
                "❌ Не удалось получить статистику.\n"
                "Проверьте настройки Google Sheets API."
            )

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        logger.debug(f"Exception type: {type(e).__name__}")
        await status_msg.edit_text("❌ Произошла ошибка при получении статистики.")


@router.message(F.text)
async def handle_message(message: Message) -> None:
    """Handle incoming messages and process Instagram and TikTok URLs.

    :param message: Incoming message
    :type message: Message
    :return: None
    """
    if not message.text:
        return

    # Check for Instagram URLs
    instagram_match = INSTAGRAM_REELS_PATTERN.search(message.text)
    # Check for TikTok URLs
    tiktok_match = TIKTOK_PATTERN.search(message.text)

    if not instagram_match and not tiktok_match:
        return

    # Determine which URL was found
    video_url = None
    platform = None
    use_proxy = False

    if instagram_match:
        video_url = instagram_match.group(0)
        platform = "Instagram"
        use_proxy = False
    elif tiktok_match:
        video_url = tiktok_match.group(0)
        platform = "TikTok"
        use_proxy = True  # TikTok requires proxy

    if video_url is None:
        return

    logger.info(f"Detected {platform} URL: {video_url}")

    # Send status message
    status_message = await message.reply("⏳ Скачиваю видео...")

    try:
        # Download video
        video_path, error_msg = await download_video(video_url, use_proxy=use_proxy)

        if video_path is None:
            # Логируем исходную ошибку от yt-dlp
            logger.error(f"Failed to download video from {platform}. URL: {video_url}. Error: {error_msg}")

            # Формируем понятное сообщение об ошибке на русском
            error_text = "❌ Не удалось скачать видео."

            if error_msg:
                error_msg_lower = error_msg.lower()

                # Проверяем специфичные ошибки и выводим понятные сообщения
                if "this content may be inappropriate" in error_msg_lower or "inappropriate" in error_msg_lower:
                    error_text += "\n\n💬 Видео может содержать контент для взрослых или материалы, которые требуют входа в аккаунт для просмотра."
                    logger.info(f"Inappropriate content error for URL: {video_url}")
                elif "private" in error_msg_lower or "приватн" in error_msg_lower:
                    error_text += "\n\n🔒 Видео является приватным и недоступно для скачивания."
                    logger.info(f"Private content error for URL: {video_url}")
                elif "not available" in error_msg_lower or "unavailable" in error_msg_lower:
                    error_text += "\n\n🚫 Видео недоступно. Возможно, оно было удалено или скрыто автором."
                    logger.info(f"Content not available for URL: {video_url}")
                elif "age" in error_msg_lower and "restrict" in error_msg_lower:
                    error_text += "\n\n🔞 Видео имеет возрастные ограничения и требует входа в аккаунт."
                    logger.info(f"Age-restricted content for URL: {video_url}")
                elif "login" in error_msg_lower or "sign in" in error_msg_lower:
                    error_text += "\n\n🔑 Для скачивания этого видео требуется авторизация в аккаунте."
                    logger.info(f"Login required for URL: {video_url}")
                elif "geo" in error_msg_lower or "region" in error_msg_lower or "country" in error_msg_lower:
                    error_text += "\n\n🌍 Видео недоступно в вашем регионе из-за географических ограничений."
                    logger.info(f"Geo-restricted content for URL: {video_url}")
                elif "429" in error_msg or "rate limit" in error_msg_lower or "too many requests" in error_msg_lower:
                    error_text += "\n\n⏱️ Слишком много запросов. Пожалуйста, попробуйте позже."
                    logger.info(f"Rate limit error for URL: {video_url}")
                else:
                    # Для неизвестных ошибок показываем оригинальное сообщение
                    error_text += f"\n\n⚠️ Техническая информация:\n{error_msg}"
                    logger.warning(f"Unknown error type for URL: {video_url}")
            else:
                error_text += "\n\n❓ Возможно, контент недоступен или является приватным."
                logger.warning(f"No error message provided for failed download: {video_url}")

            await status_message.edit_text(error_text)

            # Логируем ошибку в статистику (не блокирует основной функционал)
            asyncio.create_task(
                stats.log_download_error(
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    platform=platform,
                    url=video_url,
                    error_msg=error_msg or "Unknown error",
                )
            )
            return

        # Get video dimensions
        width, height = await get_video_dimensions(video_path)

        # Send video as reply to original message with correct dimensions
        video_file = FSInputFile(video_path)
        await message.reply_video(
            video_file,
            width=width if width > 0 else None,
            height=height if height > 0 else None,
        )

        # Delete status message
        await status_message.delete()

        # Логируем успешное скачивание в статистику (не блокирует основной функционал)
        asyncio.create_task(
            stats.log_download_success(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                platform=platform,
                url=video_url,
            )
        )

        # Cleanup temporary file
        await cleanup_file(video_path)

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await status_message.edit_text("❌ Произошла ошибка при обработке запроса.")


async def main() -> None:
    """Main function to start the bot.

    :return: None
    """
    validate_config()
    ensure_temp_directory()

    # Initialize bot and dispatcher
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started")

    try:
        # Start polling
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
