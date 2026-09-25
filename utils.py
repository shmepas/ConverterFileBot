from aiogram import types
from kbds import reply
import asyncio
import subprocess
import os
from data_base.db import is_admin, is_super_admin
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from data_base import db as _db

try:
    import imageio_ffmpeg
    FFMPEG_BINARY = os.getenv("FFMPEG_BINARY") or imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_BINARY = os.getenv("FFMPEG_BINARY") or "ffmpeg"


def _get_gif_recommendations(input_path: str) -> dict:
    """
    Анализирует видео и возвращает рекомендации для создания оптимального GIF.

    Args:
        input_path: путь к исходному видео

    Returns:
        dict с рекомендациями: width, fps, quality
    """
    # Получаем размер файла для оценки
    file_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0

    # Размер файла в MB
    size_mb = file_size / (1024 * 1024)

    # Базовые рекомендации
    recommendations = {
        "width": 480,
        "fps": 12,
        "quality": "medium"
    }

    # Корректируем на основе размера файла
    if size_mb < 5:  # Маленькие файлы - высокое качество
        recommendations["width"] = 480
        recommendations["fps"] = 15
        recommendations["quality"] = "high"
    elif size_mb < 20:  # Средние файлы
        recommendations["width"] = 400
        recommendations["fps"] = 12
        recommendations["quality"] = "medium"
    else:  # Большие файлы - агрессивная оптимизация
        recommendations["width"] = 320
        recommendations["fps"] = 10
        recommendations["quality"] = "low"

    return recommendations


async def answer_editable(message: types.Message, text: str, reply_markup=None, parse_mode=None, disable_web_page_preview=None):
    """
    Try to edit the bot's last message to this user. If editing fails, send a new message instead of deleting.
    Stores the last bot message id per user in the DB.
    Use this instead of `message.answer` when you want to avoid chat clutter.
    """
    user_id = message.from_user.id
    chat_id = message.chat.id

    last = await _db.get_last_bot_message(user_id)
    bot = message.bot

    # Telegram only accepts inline keyboards when editing a message.
    can_edit_markup = reply_markup is None or isinstance(reply_markup, types.InlineKeyboardMarkup)
    if can_edit_markup and last and last.get('chat_id') == chat_id:
        try:
            await bot.edit_message_text(text=text, chat_id=chat_id, message_id=last.get('message_id'), reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
            return
        except Exception:
            # couldn't edit (maybe message deleted or not editable) - send new message instead of deleting
            pass

    # Send new message and save its id
    sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
    try:
        await _db.set_last_bot_message(user_id, chat_id, sent.message_id)
    except Exception:
        pass


async def send_editable_raw(bot, user_id: int, chat_id: int, text: str, reply_markup=None, parse_mode=None):
    """
    Variant that accepts bot and user/chat ids directly.
    """
    last = await _db.get_last_bot_message(user_id)
    can_edit_markup = reply_markup is None or isinstance(reply_markup, types.InlineKeyboardMarkup)
    if can_edit_markup and last and last.get('chat_id') == chat_id:
        try:
            await bot.edit_message_text(text=text, chat_id=chat_id, message_id=last.get('message_id'), reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except Exception:
            # couldn't edit - send new message instead of deleting
            pass

    sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    try:
        await _db.set_last_bot_message(user_id, chat_id, sent.message_id)
    except Exception:
        pass

async def get_keyboard_by_role(user_id: int) -> types.ReplyKeyboardMarkup:
    """
    Возвращает клавиатуру в зависимости от роли:
    - обычный пользователь: базовая клавиатура
    - админ: базовая + кнопка "Админка"
    - супер-админ: базовая + кнопка "Админка"
    """
    kb = ReplyKeyboardBuilder()
    kb.attach(reply.start_kb3)  # базовая клавиатура для всех

    # для админов и супер-админов добавляем кнопку "Админка"
    if await is_admin(user_id) or await is_super_admin(user_id):
        kb.row(types.KeyboardButton(text="Админка"))

    return kb


async def convert_audio_ffmpeg_async(input_path: str, output_path: str):
    """
    Асинхронно конвертирует аудио файл в нужный формат с помощью ffmpeg.
    """
    cmd = [
        FFMPEG_BINARY,
        "-y",  # перезаписывать без подтверждения
        "-i", input_path,
        output_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{stderr.decode()}")


async def compress_video_ffmpeg_async(input_path: str, output_path: str, *, crf: int = 30, max_width: int = 640, audio_bitrate: str = "64k", preset: str = "fast"):
    """
    Сжимает видео с помощью ffmpeg, возвращает после выполнения.

    - crf: значение качества (меньше — лучше качество / больше размер). Рекомендуется 18-40.
    - max_width: максимальная ширина кадра (сохраняется соотношение сторон).
    - audio_bitrate: битрейт аудио (например, '96k').
    - preset: ffmpeg preset (ultrafast, superfast, veryfast, faster, fast, medium, slow)
    """
    vf_expr = f"scale='min({max_width},iw)':-2"  # Adjusted for new width
    # Use movflags +faststart for web-friendly mp4 and sane defaults to limit size
    cmd = [
        FFMPEG_BINARY,
        "-y",
        "-i", input_path,
        "-vf", vf_expr,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-movflags", "+faststart",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        output_path,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg compress error:\n{stderr.decode()}")


async def convert_video_to_gif_ffmpeg_async(input_path: str, output_path: str, *, width: int = None, fps: int = None, quality: str = None):
    """
    Конвертирует видео в высококачественный GIF с использованием анализа исходного видео.

    Args:
        input_path: путь к исходному видео
        output_path: путь для сохранения GIF
        width: максимальная ширина (если None, определяется автоматически)
        fps: частота кадров (если None, определяется автоматически)
        quality: качество ('high', 'medium', 'low') - если None, определяется автоматически
    """
    # Получаем рекомендации на основе анализа видео
    recommendations = _get_gif_recommendations(input_path)

    # Используем рекомендации или переданные параметры
    final_width = width if width is not None else recommendations["width"]
    final_fps = fps if fps is not None else recommendations["fps"]
    final_quality = quality if quality is not None else recommendations["quality"]

    palette_path = output_path + '.palette.png'

    # Улучшенные параметры масштабирования
    scale_expr = f"scale='min({final_width},iw)':-2:flags=lanczos"

    # Настройки качества на основе параметра final_quality
    if final_quality == "high":
        palette_gen_opts = "stats_mode=full"
        palette_use_opts = "dither=sierra2_4a"
    elif final_quality == "medium":
        palette_gen_opts = "stats_mode=diff"
        palette_use_opts = "dither=bayer:bayer_scale=5"
    else:  # low
        palette_gen_opts = "stats_mode=single"
        palette_use_opts = "dither=none"

    # Первый проход: генерация оптимизированной палитры
    cmd_palette = [
        FFMPEG_BINARY, "-y", "-i", input_path,
        "-vf", f"fps={final_fps},{scale_expr},palettegen={palette_gen_opts}",
        palette_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd_palette,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        # Очищаем файл палитры если он был создан
        try:
            if os.path.exists(palette_path):
                os.remove(palette_path)
        except Exception:
            pass
        raise RuntimeError(f"FFmpeg palettegen error:\n{stderr.decode()}")

    # Второй проход: создание GIF с оптимизированной палитрой
    cmd_use = [
        FFMPEG_BINARY, "-y", "-i", input_path, "-i", palette_path,
        "-lavfi", f"fps={final_fps},{scale_expr} [x]; [x][1:v] paletteuse={palette_use_opts}",
        "-loop", "0",  # Бесконечное воспроизведение
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd_use,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()

    # Очищаем файл палитры
    try:
        if os.path.exists(palette_path):
            os.remove(palette_path)
    except Exception:
        pass

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg gif conversion error:\n{stderr.decode()}")

    # Проверяем размер файла и при необходимости дополнительно оптимизируем
    try:
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            # Если файл больше 10MB, дополнительно оптимизируем
            if file_size > 10 * 1024 * 1024:
                await _optimize_large_gif(output_path, final_width, final_fps)
    except Exception:
        pass


async def _optimize_large_gif(gif_path: str, max_width: int, fps: int):
    """
    Дополнительная оптимизация больших GIF файлов.
    """
    import tempfile

    # Создаем временный файл для оптимизированной версии
    with tempfile.NamedTemporaryFile(suffix='.gif', delete=False) as tmp_file:
        temp_gif = tmp_file.name

    try:
        # Применяем дополнительную оптимизацию
        optimize_cmd = [
            FFMPEG_BINARY, "-y", "-i", gif_path,
            "-vf", f"scale='min({max_width * 0.8},iw)':-2:flags=lanczos,fps={max(8, fps - 2)}",
            "-gifflags", "-offsetting",  # Удаляет избыточные кадры
            "-loop", "0",
            temp_gif
        ]

        process = await asyncio.create_subprocess_exec(
            *optimize_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()

        if process.returncode == 0:
            # Заменяем оригинальный файл оптимизированным
            import shutil
            shutil.move(temp_gif, gif_path)
        else:
            # Если оптимизация не удалась, удаляем временный файл
            try:
                os.remove(temp_gif)
            except Exception:
                pass

    except Exception:
        # В случае ошибки удаляем временный файл
        try:
            if os.path.exists(temp_gif):
                os.remove(temp_gif)
        except Exception:
            pass
async def _optimize_large_gif(gif_path: str, original_width: int, original_fps: int):
    """
    Дополнительная оптимизация больших GIF файлов.
    """
    import tempfile

    # Создаем временную оптимизированную версию
    temp_path = gif_path + '.opt'

    try:
        # Более агрессивная оптимизация для больших файлов
        optimize_cmd = [
            FFMPEG_BINARY, "-y", "-i", gif_path,
            "-vf", f"fps={max(original_fps-2, 6)},scale='min({max(original_width-60, 240)},iw)':-2:flags=lanczos",
            "-loop", "0",
            "-gifflags", "+transdiff",  # Улучшенное сжатие
            temp_path
        ]

        process = await asyncio.create_subprocess_exec(
            *optimize_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            # Проверяем, что оптимизированная версия меньше
            original_size = os.path.getsize(gif_path)
            optimized_size = os.path.getsize(temp_path)

            if optimized_size < original_size * 0.8:  # Если удалось уменьшить на 20%
                # Заменяем оригинал оптимизированной версией
                os.replace(temp_path, gif_path)
            else:
                # Удаляем временный файл если оптимизация не помогла
                os.remove(temp_path)

    except Exception:
        # В случае ошибки удаляем временный файл
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
