"""
Утилиты для мониторинга производительности бота
"""
import time
import functools
import asyncio
from datetime import datetime
from data_base.db import log_action

def measure_time(func):
    """Декоратор для измерения времени выполнения функции (поддерживает sync и async)"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()

        # Проверяем, является ли функция асинхронной
        if asyncio.iscoroutinefunction(func):
            # Для async функций создаем корутину
            async def async_wrapper():
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                print(f"⏱️ {func.__name__} выполнился за {duration:.2f} секунд")
                return result
            return async_wrapper()
        else:
            # Для sync функций выполняем синхронно
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            print(f"⏱️ {func.__name__} выполнился за {duration:.2f} секунд")
            return result

    return wrapper

def log_performance(user_id: int, operation: str, duration: float, success: bool = True):
    """Логирование производительности операций"""
    try:
        status = "SUCCESS" if success else "FAILED"
        message = f"PERF: {operation} - {duration:.2f}s - {status}"
        # Асинхронно логируем
        asyncio.create_task(log_action(user_id, message))
    except Exception as e:
        print(f"❌ Ошибка логирования производительности: {e}")

# Глобальные счетчики для статистики
conversion_stats = {
    'formats_used': {},  # {'mp3': 15, 'mp4': 8, ...}
    'total_conversions': 0,
    'failed_conversions': 0,
    'average_time': 0.0
}



# Автоматический сброс статистики при импорте модуля


def update_conversion_stats(format_type: str, duration: float, success: bool = True):
    """Обновление статистики конвертаций"""
    global conversion_stats

    # Обновляем общую статистику
    conversion_stats['total_conversions'] += 1
    if not success:
        conversion_stats['failed_conversions'] += 1

    # Обновляем счетчик форматов только для успешных конвертаций
    if format_type not in conversion_stats['formats_used']:
        conversion_stats['formats_used'][format_type] = {'count': 0, 'total_time': 0, 'successful_count': 0}

    # Увеличиваем общий счетчик формата
    conversion_stats['formats_used'][format_type]['count'] += 1

    # Если конвертация успешная, обновляем счетчик успешных и время
    if success:
        conversion_stats['formats_used'][format_type]['successful_count'] += 1
        conversion_stats['formats_used'][format_type]['total_time'] += duration

    # Обновляем среднее время только для успешных конвертаций
    successful_conversions = conversion_stats['total_conversions'] - conversion_stats['failed_conversions']
    if successful_conversions > 0:
        total_time = sum(stats['total_time'] for stats in conversion_stats['formats_used'].values())
        conversion_stats['average_time'] = total_time / successful_conversions
    else:
        conversion_stats['average_time'] = 0.0

def reset_conversion_stats():
    """Сброс статистики конвертаций (для отладки или новой сессии)"""
    global conversion_stats
    conversion_stats = {
        'formats_used': {},
        'total_conversions': 0,
        'failed_conversions': 0,
        'average_time': 0.0
    }
    print("🔄 Статистика конвертаций сброшена")

def get_conversion_stats() -> dict:
    """Получение статистики конвертаций"""
    stats = conversion_stats.copy()
    # Добавляем информацию о успешных конвертациях
    stats['successful_conversions'] = stats['total_conversions'] - stats['failed_conversions']
    return stats

def get_popular_formats(limit: int = 5) -> list:
    """Получение самых популярных форматов (только успешные конвертации)"""
    formats = conversion_stats['formats_used']
    # Сортируем по количеству успешных конвертаций
    sorted_formats = sorted(formats.items(), key=lambda x: x[1]['successful_count'], reverse=True)
    return sorted_formats[:limit]

def log_conversion_attempt(user_id: int, format_type: str, attempt: int, method: str, success: bool, error: str = None, duration: float = 0):
    """Детальное логирование попыток конвертации"""
    try:
        import os
        import json
        from datetime import datetime

        # Создаем директорию для логов если её нет
        log_dir = "logs/conversion"
        os.makedirs(log_dir, exist_ok=True)

        # Формируем запись лога
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "format": format_type,
            "attempt": attempt,
            "method": method,
            "success": success,
            "error": error,
            "duration": duration
        }

        # Записываем в JSON файл (легче парсить)
        log_file = os.path.join(log_dir, f"conversion_{datetime.now().strftime('%Y%m%d')}.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        # Также пишем в текстовый файл для быстрого просмотра
        text_log_file = os.path.join(log_dir, f"conversion_{datetime.now().strftime('%Y%m%d')}.txt")
        status = "SUCCESS" if success else "FAILED"
        error_text = f" | Error: {error}" if error else ""
        log_line = f"{datetime.now().strftime('%H:%M:%S')} | User: {user_id} | Format: {format_type} | Attempt: {attempt} | Method: {method} | Status: {status} | Duration: {duration:.2f}s{error_text}\n"

        with open(text_log_file, "a", encoding="utf-8") as f:
            f.write(log_line)

    except Exception as e:
        print(f"❌ Ошибка при записи лога конвертации: {e}")

def get_conversion_logs(user_id: int = None, date: str = None, limit: int = 100) -> list:
    """Получение логов конвертаций для анализа"""
    try:
        import os
        import json
        from datetime import datetime, timedelta

        log_dir = "logs/conversion"
        if not os.path.exists(log_dir):
            return []

        logs = []

        # Определяем дату для чтения
        if date is None:
            date = datetime.now().strftime('%Y%m%d')
        elif date == "today":
            date = datetime.now().strftime('%Y%m%d')
        elif date == "yesterday":
            yesterday = datetime.now() - timedelta(days=1)
            date = yesterday.strftime('%Y%m%d')

        log_file = os.path.join(log_dir, f"conversion_{date}.log")

        if not os.path.exists(log_file):
            return []

        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    log_entry = json.loads(line.strip())

                    # Фильтруем по user_id если указан
                    if user_id and log_entry.get("user_id") != user_id:
                        continue

                    logs.append(log_entry)

                    # Ограничиваем количество записей
                    if len(logs) >= limit:
                        break

                except json.JSONDecodeError:
                    continue

        return logs[-limit:]  # Возвращаем последние записи

    except Exception as e:
        print(f"❌ Ошибка при чтении логов конвертации: {e}")
        return []

def get_conversion_analytics(days: int = 7) -> dict:
    """Получение аналитики конвертаций за период"""
    try:
        from datetime import datetime, timedelta

        analytics = {
            "total_attempts": 0,
            "successful_attempts": 0,
            "failed_attempts": 0,
            "success_rate": 0.0,
            "retry_attempts": 0,
            "fallback_usage": 0,
            "average_duration": 0.0,
            "formats_stats": {},
            "methods_stats": {},
            "errors_stats": {}
        }

        total_duration = 0.0

        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
            logs = get_conversion_logs(date=date, limit=1000)

            for log in logs:
                analytics["total_attempts"] += 1

                if log.get("success"):
                    analytics["successful_attempts"] += 1
                else:
                    analytics["failed_attempts"] += 1

                # Подсчитываем попытки с retry
                if log.get("attempt", 1) > 1:
                    analytics["retry_attempts"] += 1

                # Подсчитываем использование fallback
                if "fallback" in log.get("method", "").lower():
                    analytics["fallback_usage"] += 1

                # Статистика по форматам
                format_type = log.get("format", "unknown")
                if format_type not in analytics["formats_stats"]:
                    analytics["formats_stats"][format_type] = {"attempts": 0, "success": 0, "failures": 0}

                analytics["formats_stats"][format_type]["attempts"] += 1
                if log.get("success"):
                    analytics["formats_stats"][format_type]["success"] += 1
                else:
                    analytics["formats_stats"][format_type]["failures"] += 1

                # Статистика по методам
                method = log.get("method", "unknown")
                if method not in analytics["methods_stats"]:
                    analytics["methods_stats"][method] = {"attempts": 0, "success": 0}

                analytics["methods_stats"][method]["attempts"] += 1
                if log.get("success"):
                    analytics["methods_stats"][method]["success"] += 1

                # Статистика по ошибкам
                if log.get("error"):
                    error = log.get("error", "")[:50]  # Первые 50 символов ошибки
                    if error not in analytics["errors_stats"]:
                        analytics["errors_stats"][error] = 0
                    analytics["errors_stats"][error] += 1

                # Суммируем время
                duration = log.get("duration", 0)
                total_duration += duration

        # Вычисляем финальные метрики
        if analytics["total_attempts"] > 0:
            analytics["success_rate"] = (analytics["successful_attempts"] / analytics["total_attempts"]) * 100
            analytics["average_duration"] = total_duration / analytics["total_attempts"]

        return analytics

    except Exception as e:
        print(f"❌ Ошибка при расчете аналитики: {e}")
        return {}