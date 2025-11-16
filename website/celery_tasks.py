"""
Celery tasks для обработки загруженных файлов (конвертация и отправка результата).
"""
import os
import shutil
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import traceback
from pathlib import Path

from celery_config import celery_app

# SMTP-параметры из окружения
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_LOGIN = os.getenv("SMTP_LOGIN", "your-email@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "your-app-password")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), 'converted')

os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def _get_converter():
    """Динамический импорт converter модуля (избегаем циклических зависимостей)."""
    try:
        import converter
        return converter
    except ImportError:
        return None


@celery_app.task(bind=True, max_retries=2)
def process_large_file(self, filepath: str, format_type: str, email: str, notes: str = ""):
    """
    Фоновая задача для конвертации большого файла и отправки результата по email.
    
    :param filepath: Путь к загруженному файлу
    :param format_type: Формат для конвертации (MP3, MP4, GIF, TXT, PDF)
    :param email: Email пользователя
    :param notes: Дополнительные пожелания
    """
    try:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Файл не найден: {filepath}")

        filename = os.path.basename(filepath)
        name_without_ext = os.path.splitext(filename)[0]
        
        # Определяем расширение выходного файла
        output_ext = format_type.lower()
        output_filename = f"{name_without_ext}.{output_ext}"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        # Получаем converter модуль
        converter = _get_converter()
        if not converter:
            raise ImportError("Модуль converter не найден")

        # Конвертируем (может быть sync функция, не async)
        if format_type.upper() == "MP3":
            # Предполагаем, что в converter есть функция convert_to_mp3
            if hasattr(converter, 'convert_to_mp3'):
                converter.convert_to_mp3(filepath, output_path)
            else:
                raise NotImplementedError(f"Конвертация в {format_type} не поддерживается")

        elif format_type.upper() == "MP4":
            if hasattr(converter, 'convert_to_mp4'):
                converter.convert_to_mp4(filepath, output_path)
            else:
                raise NotImplementedError(f"Конвертация в {format_type} не поддерживается")

        elif format_type.upper() == "GIF":
            if hasattr(converter, 'convert_to_gif'):
                converter.convert_to_gif(filepath, output_path)
            else:
                raise NotImplementedError(f"Конвертация в {format_type} не поддерживается")

        elif format_type.upper() == "TXT":
            if hasattr(converter, 'convert_to_txt'):
                converter.convert_to_txt(filepath, output_path)
            else:
                raise NotImplementedError(f"Конвертация в {format_type} не поддерживается")

        elif format_type.upper() == "PDF":
            if hasattr(converter, 'convert_pdf_to_png'):
                converter.convert_pdf_to_png(filepath, output_path)
            else:
                raise NotImplementedError(f"Конвертация в {format_type} не поддерживается")

        else:
            raise ValueError(f"Неизвестный формат: {format_type}")

        # Проверяем, что файл создан
        if not os.path.exists(output_path):
            raise RuntimeError(f"Файл не был создан: {output_path}")

        # Отправляем email с результатом
        _send_result_email(email, filename, format_type, output_path, notes=notes)

        # Очищаем исходный файл (опционально)
        try:
            os.remove(filepath)
        except Exception:
            pass

        return {
            'status': 'success',
            'email': email,
            'filename': filename,
            'format': format_type,
            'output': output_filename,
        }

    except Exception as exc:
        print(f"Ошибка при обработке {filepath}: {traceback.format_exc()}")
        
        # Пытаемся отправить email об ошибке
        try:
            _send_error_email(email, filename, format_type, str(exc))
        except Exception:
            pass

        # Retry стратегия
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60)
        else:
            return {
                'status': 'error',
                'email': email,
                'error': str(exc),
            }


def _send_result_email(email: str, original_filename: str, format_type: str, output_path: str, notes: str = ""):
    """Отправляет результат конвертации на email."""
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_LOGIN
        msg['To'] = email
        msg['Subject'] = f"✓ Ваш файл готов! ({format_type})"

        body = f"""Привет!

Ваш файл успешно конвертирован!

📄 Исходный файл: {original_filename}
📊 Формат: {format_type}
💬 Пожелания: {notes or 'нет'}

Конвертированный файл прикреплен к этому письму.

Спасибо за использование нашего сервиса!
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # Прикрепляем файл
        if os.path.exists(output_path):
            with open(output_path, 'rb') as attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f'attachment; filename= {os.path.basename(output_path)}')
                msg.attach(part)

        # Отправляем
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_LOGIN, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"Email с результатом отправлен на {email}")

    except Exception as e:
        print(f"Ошибка при отправке email результата: {e}")
        raise


def _send_error_email(email: str, original_filename: str, format_type: str, error_msg: str):
    """Отправляет email об ошибке обработки."""
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_LOGIN
        msg['To'] = email
        msg['Subject'] = f"❌ Ошибка при конвертации файла ({format_type})"

        body = f"""Привет!

К сожалению, при обработке вашего файла произошла ошибка:

📄 Файл: {original_filename}
📊 Формат: {format_type}
⚠️ Ошибка: {error_msg}

Пожалуйста, попробуйте:
1. Загрузить другой файл
2. Проверить формат файла
3. Связаться с поддержкой

Спасибо за понимание!
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_LOGIN, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"Email об ошибке отправлен на {email}")

    except Exception as e:
        print(f"Ошибка при отправке email об ошибке: {e}")


__all__ = ['process_large_file']
