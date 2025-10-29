"""
Модуль для сбора и отображения статистики использования бота.

Собирает данные о скачиваниях и ошибках в Google Sheets,
предоставляет агрегированную статистику для админов.
"""

import asyncio
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
    """
    Класс для работы со статистикой в Google Sheets.

    Записывает события скачиваний и ошибок в реальном времени,
    предоставляет агрегированную статистику за период.
    Все операции выполняются неблокирующе и не влияют на основной функционал бота.
    """

    def __init__(self) -> None:
        """
        Инициализация клиента Google Sheets.

        Использует credentials из переменной окружения GOOGLE_CREDENTIALS_JSON.
        При ошибках инициализации логирует предупреждение, но не падает.
        """
        self.client: Optional[gspread.Client] = None
        self.spreadsheet: Optional[gspread.Spreadsheet] = None
        self.worksheet: Optional[gspread.Worksheet] = None
        self._initialized = False

        try:
            credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")

            if not credentials_json or not spreadsheet_id:
                logger.warning(
                    "Google Sheets credentials not configured. "
                    "Stats collection will be disabled."
                )
                logger.info(
                    f"GOOGLE_CREDENTIALS_JSON present: {bool(credentials_json)}, "
                    f"GOOGLE_SHEETS_SPREADSHEET_ID present: {bool(spreadsheet_id)}"
                )
                return

            logger.info("Initializing Google Sheets stats...")

            # Парсинг credentials из JSON строки
            credentials_dict = json.loads(credentials_json)
            logger.debug(f"Parsed credentials for project: {credentials_dict.get('project_id', 'unknown')}")

            credentials = Credentials.from_service_account_info(
                credentials_dict,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            logger.debug("Service account credentials created")

            # Инициализация клиента
            self.client = gspread.authorize(credentials)
            logger.debug("Google Sheets client authorized")

            self.spreadsheet = self.client.open_by_key(spreadsheet_id)
            logger.info(f"Connected to spreadsheet: {self.spreadsheet.title}")

            # Получаем первый лист (Events)
            self.worksheet = self.spreadsheet.sheet1
            logger.info(f"Using worksheet: {self.worksheet.title}")

            self._initialized = True
            logger.info("Google Sheets stats initialized successfully")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Google credentials JSON: {e}")
            logger.debug("Ensure GOOGLE_CREDENTIALS_JSON is valid JSON without newlines")
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
        self,
        user_id: int,
        chat_id: int,
        platform: str,
        url: str
    ) -> None:
        """
        Записывает успешное скачивание в статистику.

        :param user_id: ID пользователя Telegram
        :param chat_id: ID чата
        :param platform: Платформа (Instagram/TikTok)
        :param url: URL видео
        """
        if not self._initialized:
            logger.debug("Stats not initialized, skipping log_download_success")
            return

        try:
            now = datetime.utcnow()
            row = [
                now.isoformat() + "Z",  # Timestamp
                now.strftime("%Y-%m-%d"),  # Date
                str(user_id),
                str(chat_id),
                platform,
                url,
                "success",
                ""  # Empty error message
            ]

            logger.debug(f"Logging successful download: platform={platform}, user={user_id}, chat={chat_id}")
            await asyncio.to_thread(self._append_row, row)
            logger.info(f"Successfully logged {platform} download for user {user_id}")

        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error while logging success: {e}")
            logger.debug(f"Failed to log download for user {user_id}, platform {platform}")
        except Exception as e:
            logger.warning(f"Failed to log download success: {e}")
            logger.debug(f"Exception type: {type(e).__name__}, user: {user_id}, platform: {platform}")

    async def log_download_error(
        self,
        user_id: int,
        chat_id: int,
        platform: str,
        url: str,
        error_msg: str
    ) -> None:
        """
        Записывает ошибку скачивания в статистику.

        :param user_id: ID пользователя Telegram
        :param chat_id: ID чата
        :param platform: Платформа (Instagram/TikTok)
        :param url: URL видео
        :param error_msg: Сообщение об ошибке
        """
        if not self._initialized:
            logger.debug("Stats not initialized, skipping log_download_error")
            return

        try:
            now = datetime.utcnow()
            truncated_error = error_msg[:500] if error_msg else "Unknown error"
            row = [
                now.isoformat() + "Z",  # Timestamp
                now.strftime("%Y-%m-%d"),  # Date
                str(user_id),
                str(chat_id),
                platform,
                url,
                "error",
                truncated_error  # Ограничиваем длину сообщения об ошибке
            ]

            logger.debug(f"Logging download error: platform={platform}, user={user_id}, error={truncated_error[:50]}...")
            await asyncio.to_thread(self._append_row, row)
            logger.info(f"Successfully logged {platform} download error for user {user_id}")

        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error while logging error: {e}")
            logger.debug(f"Failed to log error for user {user_id}, platform {platform}")
        except Exception as e:
            logger.warning(f"Failed to log download error: {e}")
            logger.debug(f"Exception type: {type(e).__name__}, user: {user_id}, platform: {platform}")

    def _append_row(self, row: list) -> None:
        """
        Синхронная запись строки в Google Sheets.

        :param row: Список значений для записи
        """
        if self.worksheet:
            self.worksheet.append_row(row, value_input_option='RAW')

    async def get_stats(self, days: int = 30) -> Optional[dict]:
        """
        Получает статистику за указанное количество дней.

        :param days: Количество дней для анализа
        :return: Словарь со статистикой или None при ошибке
        """
        if not self._initialized:
            logger.warning("Google Sheets not initialized, cannot get stats")
            return None

        try:
            logger.info(f"Fetching stats for last {days} days...")

            # Получаем все данные
            all_records = await asyncio.to_thread(self.worksheet.get_all_records)
            logger.debug(f"Retrieved {len(all_records)} total records from Google Sheets")

            if not all_records:
                logger.info("No records found in Google Sheets")
                return {
                    'total': 0,
                    'success': 0,
                    'errors': 0,
                    'unique_chats': 0,
                    'error_types': {},
                    'daily_stats': []
                }

            # Фильтруем по дате
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")
            logger.debug(f"Filtering records from {cutoff_str} onwards")

            filtered_records = [
                r for r in all_records
                if r.get('Date', '') >= cutoff_str
            ]
            logger.info(f"Filtered to {len(filtered_records)} records within {days} days")

            # Подсчет статистики
            total = len(filtered_records)
            success = sum(1 for r in filtered_records if r.get('Status') == 'success')
            errors = sum(1 for r in filtered_records if r.get('Status') == 'error')

            # Уникальные чаты
            unique_chats = len(set(str(r.get('Chat ID', '')) for r in filtered_records))

            # Топ типов ошибок
            error_messages = [
                r.get('Error Message', 'Unknown error')
                for r in filtered_records
                if r.get('Status') == 'error' and r.get('Error Message')
            ]
            error_counter = Counter(error_messages)
            error_types = dict(error_counter.most_common(5))

            # Статистика по дням
            daily_data = {}
            for record in filtered_records:
                date = record.get('Date', '')
                if not date:
                    continue

                if date not in daily_data:
                    daily_data[date] = {'total': 0, 'success': 0, 'errors': 0}

                daily_data[date]['total'] += 1
                if record.get('Status') == 'success':
                    daily_data[date]['success'] += 1
                else:
                    daily_data[date]['errors'] += 1

            # Сортируем по дате (последние дни сверху)
            daily_stats = [
                {'date': date, **stats}
                for date, stats in sorted(
                    daily_data.items(),
                    key=lambda x: x[0],
                    reverse=True
                )
            ]

            logger.info(
                f"Stats calculated: total={total}, success={success}, errors={errors}, "
                f"unique_chats={unique_chats}, error_types={len(error_types)}"
            )
            logger.debug(f"Daily stats for {len(daily_stats)} days")

            return {
                'total': total,
                'success': success,
                'errors': errors,
                'unique_chats': unique_chats,
                'error_types': error_types,
                'daily_stats': daily_stats[:7]  # Последние 7 дней
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
        """
        Форматирует статистику для отображения в Telegram.

        :param stats: Словарь со статистикой
        :return: Отформатированное сообщение в HTML формате
        """
        total = stats['total']
        success = stats['success']
        errors = stats['errors']
        unique_chats = stats['unique_chats']

        if total == 0:
            return "📊 <b>Статистика</b>\n\nНет данных за указанный период."

        success_rate = (success / total * 100) if total > 0 else 0
        error_rate = (errors / total * 100) if total > 0 else 0

        message = f"""📊 <b>Статистика за последние 30 дней</b>

📥 Всего запросов: {total:,}
✅ Успешно: {success:,} ({success_rate:.1f}%)
❌ Ошибок: {errors:,} ({error_rate:.1f}%)

👥 Уникальных чатов: {unique_chats}"""

        # Добавляем топ ошибок, если есть
        if stats['error_types']:
            message += "\n\n🔝 <b>Типы ошибок:</b>"
            for error_msg, count in list(stats['error_types'].items())[:5]:
                # Обрезаем длинные сообщения
                short_msg = error_msg[:60] + "..." if len(error_msg) > 60 else error_msg
                message += f"\n• {short_msg}: {count}"

        # Добавляем статистику по дням
        if stats['daily_stats']:
            message += "\n\n📈 <b>По дням (последние 7):</b>"
            for day_stat in stats['daily_stats']:
                date_obj = datetime.strptime(day_stat['date'], "%Y-%m-%d")
                date_formatted = date_obj.strftime("%d.%m")
                message += (
                    f"\n{date_formatted}: {day_stat['total']} "
                    f"(✓{day_stat['success']} ✗{day_stat['errors']})"
                )

        return message
