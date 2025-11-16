# Фоновая обработка больших файлов с Celery

Система использует Celery + Redis для асинхронной обработки загруженных файлов и отправки результатов по email.

## Установка зависимостей

### Windows

```powershell
# Активируйте виртуальное окружение (если нужно)
& C:/Project/.venv/Scripts/Activate.ps1

# Установите зависимости
pip install -r website/web_requirements.txt
```

### macOS / Linux

```bash
# Активируйте виртуальное окружение
source .venv/bin/activate

# Установите зависимости
pip install -r website/web_requirements.txt

# Убедитесь, что Redis установлен
brew install redis  # macOS
# или
sudo apt-get install redis-server  # Ubuntu/Debian
```

## Настройка Redis

Redis используется как message broker и result backend для Celery.

### macOS / Linux (локально)

```bash
# Запустите Redis сервер
redis-server

# Или в фоне
nohup redis-server > /tmp/redis.log 2>&1 &
```

### Windows

Используйте WSL2 или Docker:

```powershell
# С Docker
docker run -d -p 6379:6379 redis:7-alpine

# Или используйте Windows port Redis:
# https://github.com/microsoftarchive/redis/releases
```

### Переменные окружения

Создайте или обновите `.env` файл с параметрами Redis:

```env
# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# SMTP для email
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_LOGIN=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

## Запуск компонентов

### 1. Запустите Flask приложение (веб-форма)

```powershell
cd website
python web.py
```

Приложение будет доступно на `http://localhost:8080/upload_large`

### 2. Запустите Celery Worker (в отдельном терминале)

```powershell
# Из корня проекта
python worker.py
```

Или более гибко:

```powershell
cd website
celery -A celery_tasks worker --loglevel=info
```

### 3. (Опционально) Запустите Flower для мониторинга

```powershell
pip install flower
flower -A celery_tasks --port=5555
```

Откройте `http://localhost:5555` для просмотра статуса задач.

## Архитектура

```
┌─────────────────────┐
│  Web Form Upload    │  (/upload_large)
│  (upload_large.html)│
└──────────┬──────────┘
           │ POST file
           ↓
      ┌────────────┐
      │  Flask App │
      │  (web.py)  │
      └──────┬─────┘
             │ Celery delay()
             ↓
      ┌──────────────────┐
      │  Redis Message   │
      │    Broker        │ (CELERY_BROKER_URL)
      └──────┬───────────┘
             │
             ↓
      ┌──────────────────┐
      │ Celery Worker 1  │
      │ Celery Worker 2  │──→ process_large_file()
      │ Celery Worker N  │    ├─ Конвертация
      └──────┬───────────┘    └─ Email результат
             │
             ↓
      ┌──────────────────┐
      │  Redis Result    │
      │   Backend        │ (CELERY_RESULT_BACKEND)
      └──────────────────┘
```

## Типичный поток

1. **Пользователь загружает файл** на `/upload_large`
2. **Flask сохраняет файл** в `website/uploads/`
3. **Celery задача создается** → файл в очередь
4. **Пользователю отправляется email** с подтверждением + task_id
5. **Celery Worker обрабатывает** файл:
   - Скачивает из `uploads/`
   - Конвертирует в нужный формат
   - Сохраняет в `website/converted/`
6. **Email с результатом** отправляется пользователю с вложением
7. **Файл удаляется** или архивируется (по настройке)

## Примеры использования

### Python (직接 запуск задачи)

```python
from website.celery_tasks import process_large_file

# Синхронно (для debug)
result = process_large_file('/path/to/file.mp4', 'MP4', 'user@example.com')

# Асинхронно через Celery
task = process_large_file.delay('/path/to/file.mp4', 'MP4', 'user@example.com')
print(f"Task ID: {task.id}")
print(f"Status: {task.status}")
```

### Мониторинг задач

```python
from celery.result import AsyncResult
from website.celery_config import celery_app

# Получить статус задачи по ID
task_id = "abc123def456"
task = AsyncResult(task_id, app=celery_app)
print(f"Status: {task.status}")  # PENDING, STARTED, SUCCESS, FAILURE
print(f"Result: {task.result}")
```

## Troubleshooting

### Redis недоступен

```
Error: Failed to establish a new connection
```

**Решение:** Убедитесь, что Redis запущен:

```bash
redis-cli ping  # должен вернуть PONG
```

### Celery worker не обрабатывает задачи

```
Task received. ETA: [unknown] but FAILED
```

**Решение:** 
- Проверьте, что worker запущен в отдельном терминале
- Проверьте логи worker'а
- Убедитесь, что `converter` модуль в `website/` существует

### Email не отправляется

**Решение:**
- Проверьте `SMTP_LOGIN` и `SMTP_PASSWORD` в `.env`
- Для Gmail используйте App Password (не обычный пароль)
- Проверьте, что email не заблокирован firewall'ом

### Файлы накапливаются в `uploads/`

**Решение:** Добавьте cleanup task (cron job):

```python
@celery_app.task
def cleanup_old_files():
    """Удаляет файлы старше 24 часов."""
    import time
    cutoff_time = time.time() - 24 * 3600
    for f in Path(UPLOAD_FOLDER).glob('*'):
        if f.stat().st_mtime < cutoff_time:
            f.unlink()
```

## Production Development

Для production используйте:
- **Supervisor** или **systemd** для управления worker процессами
- **nginx** для reverse proxy
- **Redis Sentinel** для high availability
- **Docker Compose** для оркестрации всех сервисов

Пример `docker-compose.yml`:

```yaml
version: '3.8'
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  web:
    build: .
    ports:
      - "8080:8080"
    environment:
      CELERY_BROKER_URL: redis://redis:6379/0
    depends_on:
      - redis
  
  worker:
    build: .
    command: celery -A website.celery_tasks worker --loglevel=info
    environment:
      CELERY_BROKER_URL: redis://redis:6379/0
    depends_on:
      - redis
```

Запуск:

```bash
docker-compose up
```

## Дополнительные ресурсы

- [Celery Documentation](https://docs.celeryproject.org/)
- [Redis Documentation](https://redis.io/documentation)
- [Flask + Celery Tutorial](https://blog.celeryproject.org/using-celery-with-flask/)
