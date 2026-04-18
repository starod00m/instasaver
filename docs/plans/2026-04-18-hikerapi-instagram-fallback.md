# ТЗ: интеграция HikerAPI как основного источника Instagram Reels

**Статус:** In progress
**Дата:** 2026-04-18
**Автор контекста:** предыдущая сессия разбора блокировки Instagram по IP
**Цель:** заставить бот скачивать Instagram Reels на инфраструктуре, где публичный IP помечен Instagram как rate-limited/login-required

## Проблема

Публичный IP хоста (через VPN) помечен Instagram. Любой запрос к IG через `yt-dlp` возвращает:

```
ERROR: [Instagram] <id>: Requested content is not available, rate-limit reached
or login required. Use --cookies-from-browser or --cookies for authentication.
```

TikTok с этого же IP через `yt-dlp + proxy` работает. Проблема именно с Instagram.

## Решение

Использовать [HikerAPI](https://hikerapi.com) — коммерческий API поверх IG (команда `instagrapi`). Принимает URL reel'а, возвращает прямой mp4-URL с CDN Meta. Под капотом — пул residential proxy + прогретых dummy-аккаунтов.

- Free tier: 100 запросов
- Pricing: $0.0006–$0.02 за запрос (pay-as-you-go, без минимума)
- Ожидаемая стоимость для 20–50 reels/день: ~$1–5/мес

## Что нужно сделать

### 1. Новый модуль `bot/hikerapi_client.py`

Асинхронный клиент для HikerAPI поверх `aiohttp` (уже в зависимостях).

**Требования:**
- Класс `HikerAPIClient` с методом `async def get_reel_media_url(self, instagram_url: str) -> tuple[Optional[str], Optional[str]]`
- Возвращает `(mp4_url, error_msg)` — на успехе `error_msg is None`; на ошибке `mp4_url is None`.
- Никогда не райзит — все ошибки логируются и возвращаются как значения (симметрично с `download_video`).
- Использует API-ключ из `Config.hikerapi_key` (см. §3).
- Timeout запроса — 30 секунд (`aiohttp.ClientTimeout(total=30)`).
- HTTP-клиент (`aiohttp.ClientSession`) создаётся один на процесс (через lifespan бота), **не** на каждый запрос. Принять session через `__init__`.
- Логи: DEBUG с polled URL, INFO на успехе с ответом без sensitive data, ERROR с телом ошибки HikerAPI.

**API endpoint (уточнить по docs HikerAPI):**
- POST `https://api.hikerapi.com/v2/media/by_url` или эквивалент `/media/info/by_url` — актуальный endpoint посмотреть в [их Swagger/docs](https://hikerapi.com/docs)
- Заголовок авторизации: `x-access-key: <HIKERAPI_KEY>` (проверить в docs)
- Body: `{"url": "<instagram_url>"}`
- Response: поле с video URL. В reel обычно `video_url` или `video_versions[0].url`. Проверить по реальному ответу на тестовом reel — **не гадать по памяти**, сделать один реальный запрос и парсить то, что отдают сейчас.

**Обязательно на старте работы:**
1. Сходить на hikerapi.com, прочитать актуальную доку API.
2. Сделать один curl к sandbox/live на бесплатном запросе, зафиксировать реальную форму ответа.
3. На основе этого реализовать парсинг `response_json → mp4_url`.

### 2. Новая функция скачивания по прямому URL в `bot/downloader.py`

Добавить функцию `async def download_direct_url(direct_url: str, temp_dir: Path) -> tuple[Optional[Path], Optional[str]]` рядом с `download_video`.

- Скачивает mp4 напрямую через `aiohttp.ClientSession.get(direct_url, stream=True)` → чанками в файл.
- Имя файла: `{uuid4()[:8]}_hikerapi.mp4`.
- На любой ошибке (таймаут, non-200, broken stream) — логирование + возврат `(None, error_msg)`.
- **Не** использует yt-dlp. Это прямая загрузка по CDN-ссылке Meta.
- Timeout: 60 секунд на весь download.

### 3. Конфиг `bot/config.py`

- Добавить `self.hikerapi_key: Optional[str] = _get_optional(name="HIKERAPI_KEY", default="") or None`
- **Не** делать обязательным на старте бота — если ключа нет, Instagram-загрузка будет падать с понятной ошибкой в handler'е, но бот должен запускаться (для TikTok через yt-dlp).

### 4. Логика выбора в `bot/handlers.py`

В `handle_message` (сейчас строки 186–220):

- Для **Instagram** URL:
  - Если `config.hikerapi_key is not None`:
    1. Вызвать `HikerAPIClient.get_reel_media_url(video_url)` → получить `direct_url`.
    2. Если успех → `download_direct_url(direct_url, temp_dir)` → дальше общий путь (отправка, cleanup).
    3. Если HikerAPI вернул ошибку → логировать WARN, **не** делать fallback на yt-dlp (он точно не сработает), отдать пользователю сообщение вида «Instagram временно недоступен, попробуйте позже».
  - Если `config.hikerapi_key is None`:
    - Логировать ERROR один раз на запуск (warn at startup если не задан), отдать пользователю осмысленное сообщение про конфигурацию.
- Для **TikTok** — текущий путь через `download_video(use_proxy=True)` остаётся без изменений.

Переменная `use_proxy` и весь yt-dlp-путь **сохраняются для TikTok**. Не выпиливать.

### 5. Dependency injection

`HikerAPIClient` и `aiohttp.ClientSession` создать в `bot/__main__.py` рядом с другими singleton'ами (там уже создаётся `Bot`, `GoogleSheetsStats`). Зарегистрировать в диспетчере под ключом `hikerapi_client` — аналогично `config` и `stats_tracker`. Принимать в `handle_message` через DI.

**Важно про aiohttp.ClientSession:** создавать внутри startup-хука aiogram (после запуска event loop), закрывать в shutdown-хуке. Создание на импорте модуля сломает Python 3.13 warning'ами.

### 6. Обновления зависимостей

- `aiohttp` уже есть (`>=3.12.15`).
- Никаких новых зависимостей не нужно. **Не добавлять** `hikerapi` SDK — их Python-клиент избыточен, чистый aiohttp проще.

### 7. Тестирование (ручное)

После реализации:
1. Локально запустить бот с `HIKERAPI_KEY=<ключ из free tier>` в `.env`.
2. Отправить боту 3 тестовых URL:
   - Публичный reel: `https://www.instagram.com/reel/C8_xxx/` (любой живой на момент теста)
   - `/p/` пост с видео
   - TikTok (проверить, что старый путь не сломан)
3. Проверить логи: один INFO-лог на каждый успешный HikerAPI-запрос, прямая загрузка mp4 без yt-dlp для IG.
4. Проверить стату в Google Sheets — запись должна попасть как и раньше.

### 8. Документация

- В `README.md` добавить секцию «Instagram через HikerAPI» с описанием: почему не yt-dlp, где брать ключ, сколько стоит, как задать в env.
- Пометить в `docs/plans/2026-04-18-hikerapi-instagram-fallback.md` статус: To do → In progress → Done.

## Что НЕ делать

- **Не** выпиливать yt-dlp для TikTok — он там работает.
- **Не** делать graceful fallback IG → yt-dlp → HikerAPI. В текущих условиях это лишний код, yt-dlp для IG заведомо не работает.
- **Не** кешировать mp4-ссылки HikerAPI — они одноразовые / с коротким TTL у CDN Meta.
- **Не** хранить HikerAPI-ключ в коде, только через env.
- **Не** логировать сам ключ даже на DEBUG.

## Edge cases и обработка ошибок

- **HikerAPI 429 (rate limit)** — логировать WARN, пользователю: «Сервис временно перегружен, попробуйте через минуту».
- **HikerAPI 402 (payment required / free tier исчерпан)** — логировать ERROR (это блокер), пользователю: «Временные проблемы с сервисом, сообщите администратору». Администратор получит alert через лог → journalctl на сервере.
- **HikerAPI 404** (пост удалён / приватный) — пользователю: «Пост недоступен или приватный».
- **Таймаут HikerAPI** — пользователю: «Не удалось получить видео, попробуйте позже».
- **Прямой mp4 URL даёт 403** (cookie expired на стороне HikerAPI) — retry один раз, дальше — ошибка.

## Приёмка

- [ ] Бот скачивает публичный IG reel end-to-end в личном чате.
- [ ] Бот скачивает публичный IG пост `/p/` с видео.
- [ ] TikTok-путь не сломан, скачивает как раньше.
- [ ] Без `HIKERAPI_KEY` в env: бот запускается, TikTok работает, на IG-ссылку отдаёт понятное сообщение (не 500).
- [ ] Логи не содержат API-ключ ни на одном уровне.
- [ ] Статистика в Google Sheets пишется как раньше.
- [ ] `ruff check` и `ruff format --check` проходят.
- [ ] PR с changelog в description.

## Контекст: связанные артефакты

- `wiki/concepts/bot-code-requirements.md` — 9 обязательных пунктов кода бота (webhook, env-config, graceful shutdown, etc.), соблюдать.
- `wiki/entities/instasaver.md` — entity бота.
- `wiki/log.md` — лог исследования 2026-04-17 / 2026-04-18, почему вообще нужен HikerAPI.

## Деплой — отдельный шаг, после merge

Деплой выполняется в следующей сессии по runbook `wiki/runbooks/docker-bot-deploy.md`:
1. Бамп тега до `v0.2.0`.
2. GHA собирает образ.
3. На st-dad: `docker compose pull && docker compose up -d`.
4. Добавить `HIKERAPI_KEY` в `/srv/bots/instasaver/.env` + бэкап в Keychain на Mac (см. Шаг 5.5 runbook'а).

Этот шаг — **не** часть этого ТЗ. Задача текущего агента — только код + PR. Деплой вручную после ревью.
