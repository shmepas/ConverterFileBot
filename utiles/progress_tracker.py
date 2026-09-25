"""
Модуль для отслеживания прогресса операций конвертации
"""
import asyncio
import time
from typing import Callable, Optional
from aiogram.types import Message

class ProgressTracker:
    """Класс для отслеживания прогресса операций"""

    def __init__(self):
        self.active_tracks = {}  # {user_id: track_info}

    async def start_conversion_progress(self, user_id: int, message: Message, format_type: str) -> str:
        """Начинает отслеживание прогресса конвертации"""
        track_info = {
            'user_id': user_id,
            'message': message,
            'format': format_type,
            'start_time': time.time(),
            'stage': 'initializing',
            'progress': 0,
            'update_count': 0
        }

        self.active_tracks[user_id] = track_info

        # Отправляем начальное сообщение
        try:
            progress_text = self._format_progress_message(track_info)
            await message.edit_text(progress_text)
        except Exception as e:
            # Игнорируем ошибки редактирования сообщения (например, если сообщение было удалено)
            print(f"⚠️ Не удалось отправить прогресс-сообщение: {str(e)}")
            # Удаляем из активных треков, так как сообщение недоступно
            if user_id in self.active_tracks:
                del self.active_tracks[user_id]
            return None

        return user_id

    async def update_progress(self, user_id: int, stage: str, progress: int, message_text: str = None):
        """Обновляет прогресс операции"""
        if user_id not in self.active_tracks:
            return

        track_info = self.active_tracks[user_id]
        track_info['stage'] = stage
        track_info['progress'] = min(progress, 100)
        track_info['update_count'] += 1

        # Ограничиваем частоту обновлений (не чаще чем раз в 2 секунды)
        current_time = time.time()
        if current_time - track_info.get('last_update', 0) < 2:
            return

        track_info['last_update'] = current_time

        try:
            progress_text = self._format_progress_message(track_info)
            await track_info['message'].edit_text(progress_text)
        except Exception as e:
            # Игнорируем ошибки обновления сообщения (например, если сообщение было удалено)
            print(f"⚠️ Не удалось обновить прогресс-сообщение: {str(e)}")
            # Удаляем из активных треков, так как сообщение недоступно
            if user_id in self.active_tracks:
                del self.active_tracks[user_id]

    async def complete_progress(self, user_id: int, success: bool = True, result_message: str = None):
        """Завершает отслеживание прогресса"""
        if user_id not in self.active_tracks:
            return

        track_info = self.active_tracks[user_id]
        duration = time.time() - track_info['start_time']

        # Формируем финальное сообщение
        if success:
            final_text = f"""✅ **Конвертация завершена!**

🎯 Формат: {track_info['format']}
⏱️ Время: {duration:.1f} секунд
📊 Обновлений прогресса: {track_info['update_count']}

{result_message or 'Файл успешно конвертирован!'}"""
        else:
            final_text = f"""❌ **Конвертация не удалась**

🎯 Формат: {track_info['format']}
⏱️ Время: {duration:.1f} секунд
📊 Обновлений прогресса: {track_info['update_count']}

{result_message or 'Произошла ошибка при конвертации.'}"""

        try:
            await track_info['message'].edit_text(final_text)
        except Exception as e:
            # Игнорируем ошибки редактирования сообщения (например, если сообщение было удалено)
            print(f"⚠️ Не удалось обновить финальное сообщение: {str(e)}")

        # Удаляем из активных треков
        del self.active_tracks[user_id]

    def _format_progress_message(self, track_info: dict) -> str:
        """Формирует текст сообщения с прогрессом"""
        duration = time.time() - track_info['start_time']
        progress = track_info['progress']

        # Создаем визуальный прогресс-бар
        bar_length = 20
        filled_length = int(bar_length * progress / 100)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)

        # Формируем описание стадии
        stage_descriptions = {
            'initializing': '🔧 Инициализация...',
            'validating': '🔍 Валидация файла...',
            'converting': '⚙️ Конвертация...',
            'optimizing': '📊 Оптимизация...',
            'uploading': '📤 Подготовка к отправке...',
            'retrying': '🔄 Повторная попытка...',
            'fallback': '🔧 Использование альтернативного метода...',
            'cleanup': '🧹 Очистка...'
        }

        stage_text = stage_descriptions.get(track_info['stage'], f'⚙️ {track_info["stage"]}...')

        return f"""⏳ **Конвертация в {track_info['format']}**

{stage_text}
[{bar}] {progress}%

⏱️ Время: {duration:.1f}с
📊 Обновлений: {track_info['update_count']}

*Не закрывайте чат, процесс может занять время...*"""

class ConversionProgressCallback:
    """Callback для отслеживания прогресса конвертации"""

    def __init__(self, user_id: int, tracker: ProgressTracker):
        self.user_id = user_id
        self.tracker = tracker
        self.current_stage = 'initializing'
        self.progress = 0

    async def set_stage(self, stage: str, progress: int = None):
        """Устанавливает текущую стадию и прогресс"""
        self.current_stage = stage
        if progress is not None:
            self.progress = progress

        await self.tracker.update_progress(self.user_id, self.current_stage, self.progress)

    async def set_progress(self, progress: int):
        """Устанавливает прогресс (0-100)"""
        self.progress = min(max(progress, 0), 100)
        await self.tracker.update_progress(self.user_id, self.current_stage, self.progress)

    async def complete(self, success: bool = True, message: str = None):
        """Завершает отслеживание прогресса"""
        await self.tracker.complete_progress(self.user_id, success, message)

# Глобальный экземпляр трекера
progress_tracker = ProgressTracker()

# Декоратор для автоматического отслеживания прогресса
def track_conversion_progress(user_id: int, format_type: str):
    """Декоратор для автоматического отслеживания прогресса функций конвертации"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Создаем callback для отслеживания прогресса
            callback = ConversionProgressCallback(user_id, progress_tracker)

            try:
                # Начинаем отслеживание
                await callback.set_stage('initializing', 0)

                # Заменяем callback в kwargs если есть
                if 'progress_callback' in kwargs:
                    kwargs['progress_callback'] = callback
                else:
                    # Добавляем как дополнительный аргумент
                    kwargs['progress_callback'] = callback

                # Выполняем функцию
                result = await func(*args, **kwargs)

                # Завершаем успешно
                await callback.complete(True, "Файл успешно конвертирован!")
                return result

            except Exception as e:
                # Завершаем с ошибкой
                await callback.complete(False, f"Ошибка: {str(e)[:100]}")
                raise e

        return wrapper
    return decorator

# Утилиты для создания прогресс-сообщений
def create_progress_message(format_type: str, stage: str = "initializing", progress: int = 0) -> str:
    """Создает текст сообщения прогресса"""
    tracker = ProgressTracker()
    temp_track = {
        'format': format_type,
        'stage': stage,
        'progress': progress,
        'start_time': time.time(),
        'update_count': 0
    }
    return tracker._format_progress_message(temp_track)

# Функции для работы с прогрессом в асинхронном коде
async def update_conversion_progress(user_id: int, stage: str, progress: int, message: Message):
    """Обновляет прогресс конвертации для сообщения"""
    await progress_tracker.update_progress(user_id, stage, progress)

async def complete_conversion_progress(user_id: int, success: bool, message: Message, result_text: str = None):
    """Завершает прогресс конвертации"""
    await progress_tracker.complete_progress(user_id, success, result_text)