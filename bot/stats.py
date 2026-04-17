"""Google Sheets-based stats collection for the bot.

Collects download success/error events and provides aggregated stats for admin
queries. All operations are non-blocking and never fail the main bot flow.
"""

import asyncio
import base64
import json
import logging
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


class GoogleSheetsStats:
    """Google Sheets stats client.

    Writes download events in real time and returns aggregated stats for a
    given window. All operations are non-blocking and never affect the main
    bot flow.
    """

    def __init__(self) -> None:
        """Initialize the Google Sheets client.

        Reads credentials from ``GOOGLE_CREDENTIALS_JSON_BASE64`` and the sheet
        id from ``GOOGLE_SHEETS_SPREADSHEET_ID``. Initialization failures are
        logged as warnings and stats collection is disabled silently.

        :return: None
        """
        self.client: Optional[gspread.Client] = None
        self.spreadsheet: Optional[gspread.Spreadsheet] = None
        self.worksheet: Optional[gspread.Worksheet] = None
        self._initialized = False

        try:
            credentials_json_b64 = os.getenv("GOOGLE_CREDENTIALS_JSON_BASE64")
            spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")

            if not credentials_json_b64 or not spreadsheet_id:
                logger.warning(
                    "Google Sheets credentials not configured. "
                    "Stats collection will be disabled."
                )
                logger.info(
                    f"GOOGLE_CREDENTIALS_JSON_BASE64 present: {bool(credentials_json_b64)}, "
                    f"GOOGLE_SHEETS_SPREADSHEET_ID present: {bool(spreadsheet_id)}"
                )
                return

            logger.info("Initializing Google Sheets stats...")

            credentials_json = base64.b64decode(credentials_json_b64).decode("utf-8")
            credentials_dict = json.loads(credentials_json)
            logger.debug(
                f"Parsed credentials for project: {credentials_dict.get('project_id', 'unknown')}"
            )

            credentials = Credentials.from_service_account_info(
                credentials_dict,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
            logger.debug("Service account credentials created")

            self.client = gspread.authorize(credentials)
            logger.debug("Google Sheets client authorized")

            self.spreadsheet = self.client.open_by_key(spreadsheet_id)
            logger.info(f"Connected to spreadsheet: {self.spreadsheet.title}")

            self.worksheet = self.spreadsheet.sheet1
            logger.info(f"Using worksheet: {self.worksheet.title}")

            self._initialized = True
            logger.info("Google Sheets stats initialized successfully")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Google credentials JSON: {e}")
            logger.debug(
                "Ensure GOOGLE_CREDENTIALS_JSON_BASE64 is valid Base64-encoded JSON"
            )
        except gspread.exceptions.SpreadsheetNotFound:
            logger.error(
                f"Spreadsheet not found with ID: {spreadsheet_id}. "
                "Check GOOGLE_SHEETS_SPREADSHEET_ID or share the sheet with service account."
            )
        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error during initialization: {e}")
        except Exception as e:
            logger.warning(f"Failed to initialize Google Sheets stats: {e}")
            logger.debug(f"Exception details: {type(e).__name__}: {str(e)}")

    async def log_download_success(
        self, user_id: int, chat_id: int, platform: str, url: str
    ) -> None:
        """Log a successful download event.

        :param user_id: Telegram user id.
        :type user_id: int
        :param chat_id: Telegram chat id.
        :type chat_id: int
        :param platform: Platform label (e.g. ``Instagram``/``TikTok``).
        :type platform: str
        :param url: Video URL.
        :type url: str
        :return: None
        """
        if not self._initialized:
            logger.debug("Stats not initialized, skipping log_download_success")
            return

        try:
            now = datetime.utcnow()
            row = [
                now.isoformat() + "Z",
                now.strftime("%Y-%m-%d"),
                str(user_id),
                str(chat_id),
                platform,
                url,
                "success",
                "",
            ]

            logger.debug(
                f"Logging successful download: platform={platform}, user={user_id}, chat={chat_id}"
            )
            await asyncio.to_thread(self._append_row, row)
            logger.info(f"Successfully logged {platform} download for user {user_id}")

        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error while logging success: {e}")
            logger.debug(
                f"Failed to log download for user {user_id}, platform {platform}"
            )
        except Exception as e:
            logger.warning(f"Failed to log download success: {e}")
            logger.debug(
                f"Exception type: {type(e).__name__}, user: {user_id}, platform: {platform}"
            )

    async def log_download_error(
        self, user_id: int, chat_id: int, platform: str, url: str, error_msg: str
    ) -> None:
        """Log a failed download event.

        :param user_id: Telegram user id.
        :type user_id: int
        :param chat_id: Telegram chat id.
        :type chat_id: int
        :param platform: Platform label (e.g. ``Instagram``/``TikTok``).
        :type platform: str
        :param url: Video URL.
        :type url: str
        :param error_msg: Error message (truncated to 500 chars before writing).
        :type error_msg: str
        :return: None
        """
        if not self._initialized:
            logger.debug("Stats not initialized, skipping log_download_error")
            return

        try:
            now = datetime.utcnow()
            truncated_error = error_msg[:500] if error_msg else "Unknown error"
            row = [
                now.isoformat() + "Z",
                now.strftime("%Y-%m-%d"),
                str(user_id),
                str(chat_id),
                platform,
                url,
                "error",
                truncated_error,
            ]

            logger.debug(
                f"Logging download error: platform={platform}, user={user_id}, error={truncated_error[:50]}..."
            )
            await asyncio.to_thread(self._append_row, row)
            logger.info(
                f"Successfully logged {platform} download error for user {user_id}"
            )

        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error while logging error: {e}")
            logger.debug(f"Failed to log error for user {user_id}, platform {platform}")
        except Exception as e:
            logger.warning(f"Failed to log download error: {e}")
            logger.debug(
                f"Exception type: {type(e).__name__}, user: {user_id}, platform: {platform}"
            )

    def _append_row(self, row: list) -> None:
        """Append a row to the worksheet synchronously.

        :param row: List of values to append.
        :type row: list
        :return: None
        """
        if self.worksheet:
            self.worksheet.append_row(row, value_input_option="RAW")

    async def get_stats(self, days: int = 30) -> Optional[dict]:
        """Return aggregated stats for the last ``days`` days.

        :param days: Number of days to include in the aggregation window.
        :type days: int
        :return: Stats dict, or ``None`` if Google Sheets is not initialized
            or the API call fails.
        :rtype: Optional[dict]
        """
        if not self._initialized:
            logger.warning("Google Sheets not initialized, cannot get stats")
            return None

        try:
            logger.info(f"Fetching stats for last {days} days...")

            all_records = await asyncio.to_thread(self.worksheet.get_all_records)
            logger.debug(
                f"Retrieved {len(all_records)} total records from Google Sheets"
            )

            if not all_records:
                logger.info("No records found in Google Sheets")
                return {
                    "total": 0,
                    "success": 0,
                    "errors": 0,
                    "unique_chats": 0,
                    "error_types": {},
                    "daily_stats": [],
                }

            cutoff_date = datetime.utcnow() - timedelta(days=days)
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")
            logger.debug(f"Filtering records from {cutoff_str} onwards")

            filtered_records = [
                r for r in all_records if r.get("Date", "") >= cutoff_str
            ]
            logger.info(
                f"Filtered to {len(filtered_records)} records within {days} days"
            )

            total = len(filtered_records)
            success = sum(1 for r in filtered_records if r.get("Status") == "success")
            errors = sum(1 for r in filtered_records if r.get("Status") == "error")

            unique_chats = len(set(str(r.get("Chat ID", "")) for r in filtered_records))

            error_messages = [
                r.get("Error Message", "Unknown error")
                for r in filtered_records
                if r.get("Status") == "error" and r.get("Error Message")
            ]
            error_counter = Counter(error_messages)
            error_types = dict(error_counter.most_common(5))

            daily_data = {}
            for record in filtered_records:
                date = record.get("Date", "")
                if not date:
                    continue

                if date not in daily_data:
                    daily_data[date] = {"total": 0, "success": 0, "errors": 0}

                daily_data[date]["total"] += 1
                if record.get("Status") == "success":
                    daily_data[date]["success"] += 1
                else:
                    daily_data[date]["errors"] += 1

            daily_stats = [
                {"date": date, **stats}
                for date, stats in sorted(
                    daily_data.items(), key=lambda x: x[0], reverse=True
                )
            ]

            logger.info(
                f"Stats calculated: total={total}, success={success}, errors={errors}, "
                f"unique_chats={unique_chats}, error_types={len(error_types)}"
            )
            logger.debug(f"Daily stats for {len(daily_stats)} days")

            return {
                "total": total,
                "success": success,
                "errors": errors,
                "unique_chats": unique_chats,
                "error_types": error_types,
                "daily_stats": daily_stats[:7],
            }

        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error while getting stats: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get stats from Google Sheets: {e}")
            logger.debug(f"Exception type: {type(e).__name__}: {str(e)}")
            return None

    @staticmethod
    def format_stats_message(stats: dict) -> str:
        """Format aggregated stats as an HTML message for Telegram.

        :param stats: Stats dict as returned by :meth:`get_stats`.
        :type stats: dict
        :return: HTML-formatted message body.
        :rtype: str
        """
        total = stats["total"]
        success = stats["success"]
        errors = stats["errors"]
        unique_chats = stats["unique_chats"]

        if total == 0:
            return "📊 <b>Статистика</b>\n\nНет данных за указанный период."

        success_rate = (success / total * 100) if total > 0 else 0
        error_rate = (errors / total * 100) if total > 0 else 0

        message = f"""📊 <b>Статистика за последние 30 дней</b>

📥 Всего запросов: {total:,}
✅ Успешно: {success:,} ({success_rate:.1f}%)
❌ Ошибок: {errors:,} ({error_rate:.1f}%)

👥 Уникальных чатов: {unique_chats}"""

        if stats["error_types"]:
            message += "\n\n🔝 <b>Типы ошибок:</b>"
            for error_msg, count in list(stats["error_types"].items())[:5]:
                short_msg = error_msg[:60] + "..." if len(error_msg) > 60 else error_msg
                message += f"\n• {short_msg}: {count}"

        if stats["daily_stats"]:
            message += "\n\n📈 <b>По дням (последние 7):</b>"
            for day_stat in stats["daily_stats"]:
                date_obj = datetime.strptime(day_stat["date"], "%Y-%m-%d")
                date_formatted = date_obj.strftime("%d.%m")
                message += (
                    f"\n{date_formatted}: {day_stat['total']} "
                    f"(✓{day_stat['success']} ✗{day_stat['errors']})"
                )

        return message
