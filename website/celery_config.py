"""
Конфигурация Celery для фоновой обработки больших файлов.
"""
import os
from celery import Celery

# Инициализируем Celery
celery_app = Celery('file_converter')

# Используем Redis как broker и backend
celery_app.conf.update(
    broker_url=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    result_backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 час максимум на одну задачу
    task_soft_time_limit=3300,  # soft limit — 55 минут
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
)

__all__ = ['celery_app']
