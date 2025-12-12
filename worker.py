#!/usr/bin/env python
"""
Скрипт для запуска Celery worker.
Обрабатывает фоновые задачи по конвертации файлов.

Использование:
    python worker.py
    
Или в фоне (Linux/macOS):
    nohup python worker.py > worker.log 2>&1 &
    
Или в фоне (Windows PowerShell):
    Start-Process python -ArgumentList "worker.py" -NoNewWindow -Wait
"""
import os
import sys
from pathlib import Path

# Добавляем website в path
website_path = os.path.join(os.path.dirname(__file__), 'website')
sys.path.insert(0, website_path)

# Загружаем переменные из .env
from dotenv import load_dotenv
load_dotenv()

# Импортируем Celery app
from celery_config import celery_app

if __name__ == '__main__':
    # Запускаем worker
    celery_app.worker_main(
        argv=[
            'worker',
            '--loglevel=info',
            '--concurrency=2',
            '--max-tasks-per-child=100',
            '-Q', 'default',
        ]
    )
