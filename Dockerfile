FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg pandoc libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/bot --shell /usr/sbin/nologin bot

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=bot:bot . .
RUN mkdir -p /app/data /app/downloads /app/logs /tmp/converter \
    && chown -R bot:bot /app/data /app/downloads /app/logs /tmp/converter

ENV BOT_DB_PATH=/app/data/bot_database.db \
    BOT_BACKUP_DIR=/app/data/backups \
    CONVERTER_OUTPUT_DIR=/tmp/converter

USER bot
CMD ["python", "app.py"]
