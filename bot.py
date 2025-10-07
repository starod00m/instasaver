"""Instagram Reels downloader bot for Telegram.

This bot monitors messages in groups and channels, detects Instagram Reels URLs,
downloads videos using yt-dlp, and replies with the downloaded content.
"""

import asyncio
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import aiofiles.os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

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
TEMP_DIR = Path("temp")

# Regex pattern for Instagram Reels URLs
INSTAGRAM_REELS_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/(reel|p|tv)/[\w-]+/?",
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


async def download_instagram_video(url: str) -> Optional[Path]:
    """Download Instagram video using yt-dlp.

    :param url: Instagram Reels/post URL
    :type url: str
    :return: Path to downloaded video file or None if download failed
    :rtype: Optional[Path]
    """
    try:
        # Generate unique filename
        output_template = str(TEMP_DIR / "%(id)s.%(ext)s")

        # Run yt-dlp to download video
        process = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--quiet",
            "--no-warnings",
            "--format",
            "best",
            "--limit-rate",
            "4M",
            "--output",
            output_template,
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"yt-dlp error: {error_msg}")
            return None

        # Find the downloaded file
        files = list(TEMP_DIR.glob("*"))
        if not files:
            logger.error("No file was downloaded")
            return None

        # Get the most recent file
        video_file = max(files, key=lambda p: p.stat().st_mtime)
        logger.info(f"Downloaded: {video_file.name}")
        return video_file

    except Exception as e:
        logger.error(f"Download error: {e}")
        return None


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
        "👋 Привет! Я бот для скачивания Instagram Reels.\n\n"
        "Добавь меня в группу или канал, и я буду автоматически "
        "скачивать и отправлять видео из Instagram Reels.\n\n"
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
        "1. Добавьте меня в группу или канал\n"
        "2. Отправьте ссылку на Instagram Reels\n"
        "3. Бот скачает видео и отправит его в ответ\n\n"
        "<b>Поддерживаемые ссылки:</b>\n"
        "• instagram.com/reel/...\n"
        "• instagram.com/p/...\n"
        "• instagram.com/tv/...\n\n"
        "<b>Примечание:</b> Работает только с публичным контентом.",
        parse_mode="HTML",
    )


@router.message(F.text)
async def handle_message(message: Message) -> None:
    """Handle incoming messages and process Instagram URLs.

    :param message: Incoming message
    :type message: Message
    :return: None
    """
    if not message.text:
        return

    # Search for Instagram Reels URLs
    matches = INSTAGRAM_REELS_PATTERN.findall(message.text)
    if not matches:
        return

    # Extract the full URL
    url_match = INSTAGRAM_REELS_PATTERN.search(message.text)
    if not url_match:
        return

    instagram_url = url_match.group(0)
    logger.info(f"Detected Instagram URL: {instagram_url}")

    # Send status message
    status_message = await message.reply("⏳ Скачиваю видео...")

    try:
        # Download video
        video_path = await download_instagram_video(instagram_url)

        if not video_path:
            await status_message.edit_text(
                "❌ Не удалось скачать видео. Возможно, контент недоступен "
                "или является приватным."
            )
            return

        # Send video as reply to original message
        video_file = FSInputFile(video_path)
        await message.reply_video(video_file)

        # Delete status message
        await status_message.delete()

        # Cleanup temporary file
        await cleanup_file(video_path)

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await status_message.edit_text(
            "❌ Произошла ошибка при обработке запроса."
        )


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
