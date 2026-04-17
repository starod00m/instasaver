# Локальное тестирование

Как вручную проверить бота на реальных сообщениях Telegram **до** деплоя
на сервер. Бот работает в webhook-режиме — long-polling из коробки не
поддерживается, поэтому нужен публичный HTTPS-endpoint до твоего
`localhost:8080`.

## Быстрая проверка без Telegram (curl-smoke)

Для проверки, что код собирается и HTTP-слой работает — хватит curl'ов.

```bash
# Запустить без WEBHOOK_URL — setWebhook пропустится (dev mode)
TELEGRAM_BOT_TOKEN=123:fake \
WEBHOOK_SECRET=test-secret \
uv run python -m bot
```

В другом терминале:

```bash
curl -i http://localhost:8080/health
# → 200 {"status":"ok"}

curl -i -X POST http://localhost:8080/webhook -d '{}'
# → 401 unauthorized

curl -i -X POST http://localhost:8080/webhook \
  -H "X-Telegram-Bot-Api-Secret-Token: test-secret" \
  -H "Content-Type: application/json" \
  -d '{"update_id":1}'
# → 200 ok
```

Этого достаточно для проверки webhook-слоя и secret check. Реальный
download через yt-dlp так не проверить — нужен валидный Telegram Update
с ссылкой.

## Полный E2E с реальным Telegram

### Предусловия

1. **Тестовый бот, не боевой.** [@BotFather](https://t.me/botfather) →
   `/newbot` → отдельный бот. Если его скомпрометируют, не пострадают
   чаты, где работает production-инстанс.
2. **Docker Desktop / OrbStack** запущен.
3. **`cloudflared`** установлен: `brew install cloudflared`.
4. **VPN до не-российского IP.** Из РФ trycloudflare маршрутизирует
   через Hong Kong edge — нестабильно. Через VPN (любой европейский
   exit) маршрут через Riga/Frankfurt — работает.

### Последовательность

**1. Подготовить `.env`.**

```bash
cp .env.example .env
# Заполни TELEGRAM_BOT_TOKEN (от тестового бота)
# Добавь секрет:
echo "WEBHOOK_SECRET=$(openssl rand -hex 32)" >> .env
chmod 600 .env
```

**2. Поднять cloudflared tunnel.**

```bash
# HTTP/2, НЕ QUIC — QUIC часто режут домашние роутеры и провайдеры
cloudflared tunnel --url http://localhost:8766 --protocol http2 \
  > /tmp/cf-tunnel.log 2>&1 &

# Получить публичный URL
until grep -q 'trycloudflare.com' /tmp/cf-tunnel.log; do sleep 1; done
TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cf-tunnel.log | head -1)
echo "$TUNNEL_URL"
```

**3. Прописать `WEBHOOK_URL` в `.env`.**

```bash
echo "WEBHOOK_URL=${TUNNEL_URL}/webhook" >> .env
```

Важно: подставляй строго ровно **один раз** — лишнюю строку `WEBHOOK_URL`
нужно убрать, иначе бот прочитает непредсказуемую из двух.

**4. Собрать и запустить контейнер.**

```bash
docker build -t instasaver:local .

# Используем порт 8766 (не 8080) — меньше шансов конфликта с другими
# локальными процессами (см. раздел "Грабли" ниже)
docker run -d --name instasaver-local \
  --env-file .env \
  -p 127.0.0.1:8766:8080 \
  instasaver:local

docker logs -f instasaver-local
# Должно появиться:
#   "HTTP server listening on 0.0.0.0:8080"
#   "Updating webhook: '' -> 'https://.../webhook'"
```

**5. Проверить регистрацию webhook в Telegram.**

```bash
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2-)
curl -sS "https://api.telegram.org/bot${TOKEN}/getWebhookInfo" | jq
# url = наш tunnel, last_error_message отсутствует или пустой
```

**6. Протестировать.**

Открой тестового бота в Telegram, отправь `/start`, потом ссылку на
публичный Instagram Reels. В логах контейнера увидишь весь пайплайн:
детект URL → yt-dlp → ffprobe → отправку → cleanup.

### Разборка тестовой среды

```bash
# Удалить webhook в Telegram (иначе Telegram ещё долго будет слать в никуда)
curl -X POST "https://api.telegram.org/bot${TOKEN}/deleteWebhook"

# Остановить контейнер
docker rm -f instasaver-local

# Остановить cloudflared
pkill -f 'cloudflared tunnel'

# Удалить .env с секретами
rm .env
```

## Грабли (проверено на живом тесте)

### `docker restart` не перечитывает `env_file`

`docker restart instasaver-local` берёт env только из того, что было
передано в `docker run`. Если меняешь `WEBHOOK_URL` (или любой другой env)
— нужно `docker rm -f && docker run` заново. Контейнер при старте всегда
вызывает `setWebhook`, так что новые значения применятся автоматически.

### Конфликт порта `localhost:8080`

Если на хосте уже что-то слушает 8080 (часто: dashboard'ы, dev-серверы,
другие docker-контейнеры), Docker **молча** стартует контейнер с mapping
`127.0.0.1:8080:8080`, но внешние запросы попадут к первому слушающему
процессу, а не к нашему контейнеру. Симптомы:

- cloudflared лог: `error="read tcp 127.0.0.1:xxxxx->127.0.0.1:8080:
  read: connection reset by peer"`
- Telegram: `last_error_message: Wrong response from the webhook: 404
  Not Found`
- `docker logs` контейнера: нет ни одной строки `aiohttp.access` от
  внешних запросов

Проверить: `lsof -iTCP:8080 -sTCP:LISTEN`. Если занято чужим — использовать
другой порт на хосте (`-p 127.0.0.1:8766:8080`). Порт внутри контейнера
(`8080`) менять не нужно.

### Два одновременных webhook'а на одного бота

Telegram позволяет только один webhook на бота. Если у тебя работает
production на st-dad, а ты регистрируешь локальный — production
перестанет получать сообщения. На время теста пользуйся **отдельным**
(тестовым) ботом.

## Что проверять в логах

Успешный жизненный цикл одного сообщения:

```
bot.handlers - INFO - Detected Instagram URL: https://...
bot.downloader - INFO - Starting yt-dlp (attempt 1/3, rate-limit=8M): ...
bot.downloader - INFO - yt-dlp finished (attempt 1/3), returncode=0
bot.downloader - INFO - Downloaded: <id>_<videoid>.mp4 (attempt 1)
bot.handlers - INFO - Video downloaded: <id>_<videoid>.mp4 (<size> KB)
bot.downloader - INFO - Video dimensions: WxH
bot.downloader - INFO - Cleaned up: <id>_<videoid>.mp4
aiogram.event - INFO - Update id=... is handled. Duration XXXX ms
bot.stats - INFO - Successfully logged Instagram download for user ...
```

Если в логах `Detected ... URL` нет — webhook не дошёл, смотри
`last_error_message` в `getWebhookInfo`.
