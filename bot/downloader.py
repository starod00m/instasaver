"""Video download helpers built on top of yt-dlp and ffprobe.

All functions here are I/O-bound and asynchronous. They never raise on
transient errors — failures are returned as values so the handler layer can
report them to the user.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional

import aiofiles.os
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatMemberAdministrator, Message

logger = logging.getLogger(__name__)


def _is_ytdlp_artifact(path: Path) -> bool:
    """Return True if ``path`` is a yt-dlp helper/partial artifact, not a
    finished media file.

    yt-dlp leaves ``.part`` / ``.ytdl`` files (and fragment partials such as
    ``.part-Frag0``) on interrupted or fragmented downloads, plus the
    ``.info.json`` metadata file. ``download_id`` is shared across retries, so
    a stale partial must not be mistaken for a finished video nor mask the
    ``file_too_large`` branch.

    :param path: Candidate file produced under ``temp_dir``.
    :type path: Path
    :return: ``True`` for yt-dlp artifacts, ``False`` for real media files.
    :rtype: bool
    """
    suffix = path.suffix
    return (
        suffix == ".json"
        or suffix.startswith(".part")
        or suffix.startswith(".ytdl")
    )


async def get_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Extract video dimensions using ffprobe.

    :param video_path: Path to the video file.
    :type video_path: Path
    :return: Tuple of ``(width, height)``; ``(0, 0)`` when detection fails.
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
    url: str,
    temp_dir: Path,
    proxy_url: Optional[str] = None,
    use_proxy: bool = False,
    max_retries: int = 3,
) -> tuple[Optional[Path], Optional[str]]:
    """Download a video from Instagram or TikTok using yt-dlp, with retries.

    Retries up to ``max_retries`` times; on HTTP 429/rate-limit errors the
    per-attempt ``--limit-rate`` is gradually reduced (8M -> 4M -> 2M -> 1M).

    :param url: Instagram Reels/post or TikTok URL.
    :type url: str
    :param temp_dir: Directory to place downloaded files in.
    :type temp_dir: Path
    :param proxy_url: SOCKS5/HTTP proxy URL. Used only when ``use_proxy`` is
        true. ``None`` disables proxying even if ``use_proxy`` is true.
    :type proxy_url: Optional[str]
    :param use_proxy: Whether a proxy should be attempted for this download
        (usually true for TikTok, false for Instagram).
    :type use_proxy: bool
    :param max_retries: Maximum number of attempts.
    :type max_retries: int
    :return: Tuple of ``(path_to_video, error_msg)``. On success the second
        element is ``None``; on failure the first element is ``None``. When
        the video exceeds ``--max-filesize`` (50M), yt-dlp aborts silently
        with exit code 0 and no video file, and ``error_msg`` is the literal
        marker ``"file_too_large"`` (not retried).
    :rtype: tuple[Optional[Path], Optional[str]]
    """
    rate_limits = ["8M", "4M", "2M", "1M"]

    last_error_msg: Optional[str] = None
    current_rate_limit_index = 0

    download_id = str(uuid.uuid4())[:8]

    for attempt in range(max_retries):
        try:
            current_rate_limit = rate_limits[
                min(current_rate_limit_index, len(rate_limits) - 1)
            ]

            if attempt > 0:
                logger.info(
                    f"Retry attempt {attempt + 1}/{max_retries} with rate-limit: {current_rate_limit}"
                )
                await asyncio.sleep(2)

            output_template = str(temp_dir / f"{download_id}_%(id)s.%(ext)s")

            cmd = [
                "yt-dlp",
                "--quiet",
                "--no-warnings",
                "--format",
                # Prefer a format that already fits Telegram's 50 MB limit
                # (exact size first, then approximate — many IG/TikTok
                # formats only declare filesize_approx) so a smaller
                # rendition (e.g. 720p) is chosen over the best one when the
                # best one wouldn't fit. Falls back to plain "best" when no
                # format declares a size at all; --max-filesize below still
                # guards that case.
                "best[filesize<=50M]/best[filesize_approx<=50M]/best",
                # Mirrors the Telegram Bot API's 50 MB upload limit so yt-dlp
                # refuses to download a file we couldn't send anyway,
                # protecting the tmpfs-backed temp_dir from oversized files.
                "--max-filesize",
                "50M",
                "--limit-rate",
                current_rate_limit,
                "--output",
                output_template,
                # Written before the media download, so a leftover .info.json
                # with no video file is our only signal that yt-dlp aborted on
                # --max-filesize (the file_too_large branch below relies on it).
                "--write-info-json",
                # Read-only rootfs: no writable cache directory is guaranteed.
                "--no-cache-dir",
            ]

            if use_proxy and proxy_url is not None:
                cmd.extend(["--proxy", proxy_url])
                if attempt == 0:
                    logger.info("Using proxy for download")
            elif use_proxy and proxy_url is None:
                logger.warning("Proxy requested but PROXY_URL not set in environment")

            cmd.append(url)

            logger.info(
                f"Starting yt-dlp (attempt {attempt + 1}/{max_retries}, "
                f"rate-limit={current_rate_limit}): {url}"
            )

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()
            logger.info(
                f"yt-dlp finished (attempt {attempt + 1}/{max_retries}), "
                f"returncode={process.returncode}"
            )

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                last_error_msg = error_msg

                error_msg_lower = error_msg.lower()
                is_rate_limit_error = (
                    "429" in error_msg
                    or "rate limit" in error_msg_lower
                    or "rate-limit" in error_msg_lower
                    or "too many requests" in error_msg_lower
                )

                if is_rate_limit_error:
                    logger.warning(
                        f"Rate-limit error detected (attempt {attempt + 1}/{max_retries}): "
                        f"{error_msg}"
                    )
                    current_rate_limit_index += 1
                    if attempt < max_retries - 1:
                        continue
                else:
                    logger.error(
                        f"yt-dlp error (attempt {attempt + 1}/{max_retries}): {error_msg}"
                    )
                    if attempt < max_retries - 1:
                        continue

                return None, error_msg

            all_files = list(temp_dir.glob(f"{download_id}_*"))
            files = [p for p in all_files if not _is_ytdlp_artifact(path=p)]
            if not files:
                # yt-dlp aborts silently (exit code 0, --quiet swallows the
                # "Aborting" message) when --max-filesize is exceeded. It
                # still writes the info JSON before attempting the media
                # download, so a leftover info.json with no video file is
                # the only signal we have that this was a size rejection
                # rather than a real download failure. Retrying is useless
                # here since the file won't get smaller.
                info_jsons = [p for p in all_files if p.name.endswith(".info.json")]
                if info_jsons:
                    logger.warning(
                        f"yt-dlp aborted: file exceeds --max-filesize "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    for info_json in info_jsons:
                        info_json.unlink(missing_ok=True)
                    return None, "file_too_large"

                error_msg = "No file was downloaded"
                last_error_msg = error_msg
                if attempt < max_retries - 1:
                    logger.warning(f"{error_msg} (attempt {attempt + 1}/{max_retries})")
                    continue
                logger.error(f"{error_msg} - all retries exhausted")
                return None, error_msg

            video_file = max(files, key=lambda p: p.stat().st_mtime)
            logger.info(f"Downloaded: {video_file.name} (attempt {attempt + 1})")
            return video_file, None

        except Exception as e:
            error_msg = str(e)
            last_error_msg = error_msg
            logger.error(
                f"Download error (attempt {attempt + 1}/{max_retries}): {error_msg}"
            )
            if attempt < max_retries - 1:
                continue

    return None, last_error_msg or "Download failed after all retry attempts"


async def cleanup_file(file_path: Path) -> None:
    """Delete a file asynchronously, logging errors instead of raising.

    :param file_path: Path to the file to delete.
    :type file_path: Path
    :return: None
    """
    try:
        await aiofiles.os.remove(file_path)
        logger.info(f"Cleaned up: {file_path.name}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


async def cleanup_info_json(video_path: Path) -> None:
    """Delete the yt-dlp info JSON file associated with a video.

    :param video_path: Path to the downloaded video file.
    :type video_path: Path
    :return: None
    """
    info_path = video_path.with_suffix(".info.json")
    try:
        if await aiofiles.os.path.exists(info_path):
            await aiofiles.os.remove(info_path)
            logger.debug(f"Cleaned up info JSON: {info_path.name}")
    except Exception as e:
        logger.warning(f"Could not clean up info JSON {info_path.name}: {e}")


async def can_bot_delete_messages(message: Message, bot: Bot) -> bool:
    """Check if the bot can delete messages in the given chat.

    Returns ``True`` only for group/supergroup chats where the bot is an
    administrator with the ``can_delete_messages`` privilege.

    :param message: Incoming message (used to derive chat context).
    :type message: Message
    :param bot: Bot instance for querying chat member info.
    :type bot: Bot
    :return: ``True`` if the bot can delete messages, ``False`` otherwise.
    :rtype: bool
    """
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    try:
        bot_member = await message.chat.get_member(bot.id)
        if isinstance(bot_member, ChatMemberAdministrator):
            return bool(bot_member.can_delete_messages)
        return False
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning(
            f"Could not check bot permissions in chat {message.chat.id}: {e}"
        )
        return False
