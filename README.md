# InstaSaver Bot

Telegram-бот для автоматического скачивания видео из Instagram Reels и TikTok.

## Описание

InstaSaver - это простой Telegram-бот, который автоматически скачивает видео из Instagram Reels и TikTok. Бот работает как в личных чатах, так и в группах и каналах. При обнаружении ссылки бот скачивает видео с помощью yt-dlp и отправляет его ответом (reply) на исходное сообщение.

## Возможности

- 🎥 Автоматическое скачивание Instagram Reels и TikTok видео
- 💬 Работа в личных чатах, группах и каналах
- 📤 Отправка видео в ответ на исходное сообщение
- 🧹 Автоматическая очистка временных файлов
- ⚡ Асинхронная обработка для работы с несколькими запросами
- 🔒 Работает только с публичным контентом Instagram и TikTok
- 🌐 Поддержка прокси для TikTok (опционально)
- 📊 Сбор статистики использования в Google Sheets (опционально)

## Поддерживаемые URL

**Instagram:**
- `instagram.com/reel/...`
- `instagram.com/p/...`
- `instagram.com/tv/...`

**TikTok:**
- `tiktok.com/@username/video/...`
- `vm.tiktok.com/...`
- `vt.tiktok.com/...`

## Требования

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) - для управления зависимостями
- ffmpeg (нужен yt-dlp для merge высокого качества и ffprobe — в Docker-образе уже установлен)
- yt-dlp (устанавливается автоматически через uv sync)

## Установка

### 1. Клонируйте репозиторий

```bash
git clone <your-repo-url>
cd instasaver
```

### 2. Установите uv (если еще не установлен)

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 3. Установите зависимости

```bash
uv sync
```

### 4. Настройте бота

Создайте файл `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Откройте `.env` и укажите необходимые параметры:

```env
# Обязательный параметр
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Опциональный параметр (требуется для скачивания TikTok, если есть блокировка)
PROXY_URL=socks5://username:password@host:port
```

#### Как получить токен бота?

1. Откройте Telegram и найдите [@BotFather](https://t.me/botfather)
2. Отправьте команду `/newbot`
3. Следуйте инструкциям и придумайте имя для бота
4. Скопируйте полученный токен и вставьте его в `.env`

#### Настройка прокси (опционально)

Прокси требуется для скачивания видео из TikTok, если сервис заблокирован в вашем регионе.

**Поддерживаемые форматы:**
- `http://username:password@host:port`
- `https://username:password@host:port`
- `socks5://username:password@host:port`

**Пример:**
```env
PROXY_URL=socks5://proxy_user:your_password@your_proxy_host:port
```

Если `PROXY_URL` не указан, бот будет пытаться скачать видео напрямую. Для Instagram прокси не требуется.

#### Настройка статистики Google Sheets (опционально)

Бот может автоматически собирать статистику использования в Google Sheets. Для этого необходимо:

1. **Создать Google Service Account:**
   - Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
   - Создайте новый проект или выберите существующий
   - Включите Google Sheets API
   - Создайте Service Account и скачайте JSON-файл с credentials

2. **Подготовить credentials для использования:**

   **Локальная разработка:**
   ```bash
   # macOS
   base64 -i credentials.json | pbcopy

   # Linux
   cat credentials.json | base64 -w 0
   ```

   **CI/CD (например, для переменных окружения):**
   ```bash
   # Кодирование JSON в Base64 одной строкой
   echo -n '{"type":"service_account","project_id":"..."}' | base64 -w 0
   ```

3. **Добавить в `.env`:**
   ```env
   # Base64-кодированный JSON с credentials
   GOOGLE_CREDENTIALS_JSON_BASE64=eyJ0eXBlIjoic2VydmljZV9hY2NvdW50...

   # ID таблицы из URL (между /d/ и /edit)
   GOOGLE_SHEETS_SPREADSHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms

   # Telegram ID администратора (для просмотра статистики)
   ADMIN_USER_ID=123456789
   ```

4. **Дать доступ Service Account к таблице:**
   - Откройте вашу Google Sheets таблицу
   - Нажмите "Share" / "Поделиться"
   - Добавьте email Service Account (из JSON файла, поле `client_email`)
   - Дайте права "Editor" / "Редактор"

Команда `/stats` покажет статистику за последние 7 дней (доступна только администратору).

## Запуск

Бот работает только в режиме **webhook** — long-polling (`getUpdates`) не
поддерживается в production-коде. Это значит, Telegram должен иметь
возможность сделать `POST` на публичный HTTPS URL бота.

### Режим 1: Production (Docker + Cloudflare Tunnel)

Процедура деплоя на собственный сервер описана в runbook'е
`docker-bot-deploy`. Вкратце: GitHub Actions собирает и пушит образ в GHCR,
на сервере лежит `compose.yml` с `image: ghcr.io/<user>/instasaver:latest`,
порт `127.0.0.1:8080` публикуется наружу через Cloudflare Tunnel.

Обязательные env: `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `WEBHOOK_URL`.
Бот при старте идемпотентно вызывает `setWebhook`, сверяя текущий URL c
`WEBHOOK_URL` через `getWebhookInfo` — повторных вызовов при рестартах
контейнера не происходит.

### Режим 2: Локальный запуск для отладки

Полная процедура с граблями и разборкой — в
[`docs/local-testing.md`](docs/local-testing.md). Вкратце:

```bash
# 1. Поднять локальный туннель через VPN (чтобы edge был в Европе, а не в Азии)
cloudflared tunnel --url http://localhost:8766 --protocol http2

# 2. Прописать полученный HTTPS URL в .env как WEBHOOK_URL, добавить WEBHOOK_SECRET
# 3. Запустить через Docker на порту 8766 (избежать конфликтов):
docker build -t instasaver:local .
docker run -d --name instasaver-local --env-file .env -p 127.0.0.1:8766:8080 instasaver:local
```

Если нужно поднять только HTTP-сервер без обращения к Telegram (например,
проверить `/health` локально) — оставьте `WEBHOOK_URL` пустым, бот не
будет трогать webhook-конфигурацию.

### Режим 3: Docker Compose локально

```bash
docker compose build
docker compose up
# контейнер слушает на 127.0.0.1:8080
curl http://127.0.0.1:8080/health
```

## Использование

### Вариант 1: Личный чат с ботом

Просто отправьте ссылку на Instagram Reels или TikTok боту в личные сообщения.

### Вариант 2: Группа или канал

#### 1. Добавьте бота в группу или канал

- Откройте вашу группу/канал
- Нажмите на название → "Добавить участников"
- Найдите вашего бота и добавьте его
- **Важно:** Убедитесь, что бот имеет права на чтение сообщений (Privacy Mode должен быть отключен в настройках бота через @BotFather)

#### 2. Отключите Privacy Mode (если необходимо)

Если бот не видит сообщения в группе:
1. Откройте [@BotFather](https://t.me/botfather)
2. Отправьте `/mybots`
3. Выберите вашего бота
4. Bot Settings → Group Privacy → **Turn off**

#### 3. Отправьте ссылку на видео

Просто отправьте сообщение со ссылкой на Instagram Reels или TikTok в группу, где находится бот.

### Примеры ссылок

```
https://www.instagram.com/reel/ABC123xyz/
https://www.tiktok.com/@username/video/1234567890
https://vm.tiktok.com/ZMFkL1234/
```

### Как это работает

Бот автоматически:
- Обнаружит ссылку в сообщении
- Отправит статус "⏳ Скачиваю видео..."
- Скачает видео (с использованием прокси для TikTok, если настроен)
- Отправит видео ответом на ваше сообщение
- Удалит временные файлы

## Команды

- `/start` - Приветствие и краткое описание
- `/help` - Подробная инструкция по использованию
- `/stats` - Статистика использования за 7 дней (только для администратора)

## Структура проекта

```
instasaver/
├── .github/workflows/docker.yml   # CI: build + push образа в GHCR
├── .env                           # Конфигурация (не коммитится)
├── .env.example                   # Шаблон конфигурации
├── .dockerignore
├── .gitignore
├── Dockerfile                     # multi-stage образ, non-root UID 10001
├── docker-compose.yml             # Локальный dev-compose (без hardening)
├── pyproject.toml                 # uv / зависимости
├── uv.lock
├── README.md
└── bot/                           # Пакет бота, запуск: python -m bot
    ├── __init__.py
    ├── __main__.py                # Entrypoint: aiohttp + aiogram, graceful shutdown
    ├── config.py                  # Чтение env vars
    ├── webhook.py                 # POST /webhook с проверкой secret token
    ├── health.py                  # GET /health
    ├── handlers.py                # aiogram router (/start, /help, /stats, URLs)
    ├── downloader.py              # yt-dlp, ffprobe, cleanup, permission checks
    └── stats.py                   # Google Sheets статистика
```

Временные файлы при скачивании живут в `/tmp/instasaver/` (в контейнере —
tmpfs, не volume).

## Технологии

- **[aiogram 3.x](https://github.com/aiogram/aiogram)** - асинхронная библиотека для Telegram Bot API
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** - мощный инструмент для скачивания видео
- **[python-dotenv](https://github.com/theskumar/python-dotenv)** - управление переменными окружения
- **[aiofiles](https://github.com/Tinche/aiofiles)** - асинхронная работа с файлами

## Ограничения

- Работает только с **публичным** контентом Instagram и TikTok
- Telegram Bot API ограничивает размер файла до 50 МБ (бот не обрабатывает файлы большего размера специально)
- Скачивает только видео (не фото)
- Для TikTok может потребоваться прокси в зависимости от региона

## Troubleshooting

### Бот не отвечает в группе

- Проверьте, что Privacy Mode отключен (см. раздел "Использование")
- Убедитесь, что бот добавлен в группу как администратор (для каналов)

### Ошибка "yt-dlp: command not found"

Убедитесь, что все зависимости установлены:
```bash
uv sync
```

### Ошибка скачивания видео из Instagram

- Проверьте, что контент является публичным
- Убедитесь, что ссылка ведет на Reels/видео, а не на фото
- Проверьте подключение к интернету

### Ошибка скачивания видео из TikTok

- Если TikTok заблокирован в вашем регионе, настройте прокси в `.env`:
  ```env
  PROXY_URL=socks5://username:password@host:port
  ```
- Проверьте, что прокси работает и доступен
- Проверьте логи бота - должна быть строка "Using proxy for download"
- Убедитесь, что контент является публичным
