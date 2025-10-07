# Deployment Guide

## Timeweb Cloud Apps

Этот проект оптимизирован для деплоя на Timeweb Cloud Apps.

### Ограничения Timeweb Cloud Apps

Timeweb Cloud Apps не поддерживает следующие директивы в `docker-compose.yml`:
- `volumes` - монтирование томов
- `deploy` - ограничения ресурсов
- `security_opt` - настройки безопасности
- `read_only` - read-only файловая система
- `tmpfs` - временная файловая система в памяти
- `container_name` - имя контейнера (игнорируется)

### Деплой на Timeweb Cloud Apps

1. Подключите репозиторий к Timeweb Cloud Apps
2. Выберите тип деплоя: **Docker Compose**
3. Убедитесь, что в переменных окружения указан `TELEGRAM_BOT_TOKEN`
4. Файл `docker-compose.yml` в корне репозитория автоматически используется для деплоя

**Важно:** Используйте упрощенный `docker-compose.yml` для Timeweb Cloud Apps.

### Локальная разработка

Для локальной разработки используйте `docker-compose.local.yml` с полным набором best practices:

```bash
# Запуск локально с расширенными настройками
docker-compose -f docker-compose.local.yml up -d

# Просмотр логов
docker-compose -f docker-compose.local.yml logs -f

# Остановка
docker-compose -f docker-compose.local.yml down
```

### Различия между файлами

| Функция | docker-compose.yml (Timeweb) | docker-compose.local.yml |
|---------|------------------------------|--------------------------|
| Volumes | ❌ Не поддерживается | ✅ Включено |
| Resource limits | ❌ Не поддерживается | ✅ Включено (512MB/1CPU) |
| Security options | ❌ Не поддерживается | ✅ Включено |
| Read-only FS | ❌ Не поддерживается | ✅ Включено |
| Healthcheck | ❌ Не поддерживается | ✅ Включено |

### Переменные окружения

Создайте файл `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Укажите токен бота:
```
TELEGRAM_BOT_TOKEN=your_actual_token_here
```

### Проверка деплоя

После деплоя на Timeweb Cloud Apps:
1. Проверьте логи в панели управления
2. Отправьте `/start` боту в Telegram
3. Проверьте работу, отправив ссылку на Instagram Reels

### Альтернатива: деплой через Dockerfile

Если Docker Compose вызывает проблемы, можно использовать деплой через Dockerfile:

1. В Timeweb Cloud Apps выберите тип: **Dockerfile**
2. Переменные окружения укажите в интерфейсе панели управления
3. Dockerfile автоматически будет использован для сборки
