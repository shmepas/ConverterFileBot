# Развёртывание ConverterFileBot

GitHub хранит исходный код и запускает автоматическое обновление. Сам бот постоянно работает на Linux-сервере через Docker Compose; сервер нужен, потому что GitHub Actions ограничивает длительность одной задачи.

## Первый запуск сервера

1. Подготовьте VPS с Ubuntu или другим Linux, Docker Engine и Docker Compose v2. Для преобразования видео лучше выбрать сервер как минимум с 2 ГБ оперативной памяти.
2. Подключитесь к серверу по SSH и клонируйте репозиторий в постоянный каталог, например `/opt/converterfilebot`:

   ```sh
   git clone https://github.com/shmepas/ConverterFileBot.git /opt/converterfilebot
   cd /opt/converterfilebot
   cp .env.example .env
   chmod 600 .env
   ```

3. Если нужно перенести существующих пользователей, подписки, тикеты и историю, создайте согласованную копию SQLite на Windows из каталога проекта:

   ```powershell
   python -c "import sqlite3; source=sqlite3.connect(r'data_base/bot_database.db'); target=sqlite3.connect(r'bot_database-migration.sqlite3'); source.backup(target); target.close(); source.close()"
   scp .\bot_database-migration.sqlite3 <пользователь>@<сервер>:/tmp/bot_database-migration.sqlite3
   ```

   На сервере до первого запуска контейнера импортируйте её в постоянный том:

   ```sh
   docker volume create converterfilebot-data
   docker run --rm \
     -v converterfilebot-data:/data \
     -v /tmp/bot_database-migration.sqlite3:/source.db:ro \
     alpine:3.22 sh -c 'cp /source.db /data/bot_database.db && chown -R 10001:10001 /data'
   rm /tmp/bot_database-migration.sqlite3
   ```

   Если переносить старую базу не нужно, пропустите этот шаг. Временную копию базы не отправляйте в GitHub.
4. Откройте `.env` и укажите `TOKEN` и `SUPER_ADMIN_ID`. Файл `.env` остаётся только на сервере и не добавляется в Git. Если прежний Telegram-токен ещё активен, сначала замените его через BotFather.
5. Запустите бота:

   ```sh
   docker compose up -d --build
   docker compose logs -f bot
   ```

SQLite-база и резервные копии сохраняются в Docker volume и переживают пересоздание контейнера. Загруженные временные файлы, журналы и резервные копии разделены по томам. Входящие порты серверу не требуются: бот подключается к Telegram через long polling.

## Автоматический деплой из GitHub

Сначала настройте сервер так, чтобы SSH-пользователь мог читать клон репозитория и запускать `docker compose`.

В репозитории откройте **Settings → Secrets and variables → Actions** и добавьте repository variables:

- `DEPLOY_HOST` — IP-адрес или DNS-имя сервера;
- `DEPLOY_USER` — SSH-пользователь;
- `DEPLOY_PATH` — путь к клону, например `/opt/converterfilebot`.

Добавьте repository secrets:

- `DEPLOY_SSH_KEY` — приватная часть отдельного SSH-ключа деплоя; публичную часть добавьте в `~/.ssh/authorized_keys` пользователя на сервере;
- `DEPLOY_KNOWN_HOSTS` — проверенная запись ключа SSH-сервера из `ssh-keyscan -H <адрес-сервера>`.

Сверьте отпечаток ключа сервера с данными провайдера перед сохранением `DEPLOY_KNOWN_HOSTS`. После настройки каждый push в `main` обновляет код и перезапускает контейнер. Запуск также можно сделать вручную на вкладке **Actions → Deploy ConverterFileBot → Run workflow**.

Если `DEPLOY_HOST` не задан, GitHub пропускает деплой. Токен Telegram и `.env` не нужно добавлять в GitHub Actions для этой схемы: они хранятся в `.env` на сервере.
