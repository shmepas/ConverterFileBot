from aiogram import types
from kbds import reply
import asyncio
import subprocess
import os
import requests
from kbds.admin_reply import admin_kb, super_admin_kb
from data_base.db import is_admin, is_super_admin
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from data_base import db as _db


async def answer_editable(message: types.Message, text: str, reply_markup=None, parse_mode=None, disable_web_page_preview=None):
    """
    Try to edit the bot's last message to this user. If editing fails, delete previous and send a new message.
    Stores the last bot message id per user in the DB.
    Use this instead of `message.answer` when you want to avoid chat clutter.
    """
    user_id = message.from_user.id
    chat_id = message.chat.id

    last = await _db.get_last_bot_message(user_id)
    bot = message.bot

    # Try to edit existing bot message
    if last and last.get('chat_id') == chat_id:
        try:
            await bot.edit_message_text(text=text, chat_id=chat_id, message_id=last.get('message_id'), reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
            return
        except Exception:
            # couldn't edit (maybe message deleted or not editable) - try to delete old message
            try:
                await bot.delete_message(chat_id=chat_id, message_id=last.get('message_id'))
            except Exception:
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
    if last and last.get('chat_id') == chat_id:
        try:
            await bot.edit_message_text(text=text, chat_id=chat_id, message_id=last.get('message_id'), reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except Exception:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=last.get('message_id'))
            except Exception:
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
        "ffmpeg",
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
        "ffmpeg",
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


async def convert_video_to_gif_ffmpeg_async(input_path: str, output_path: str, *, width: int = 360, fps: int = 10, quality: str = "high"):
    """
    Конвертирует видео в высококачественный GIF с использованием оптимизированных параметров.
    
    Args:
        input_path: путь к исходному видео
        output_path: путь для сохранения GIF
        width: максимальная ширина (по умолчанию 360 для лучшей производительности)
        fps: частота кадров (по умолчанию 10 для плавности)
        quality: качество ('high', 'medium', 'low') - влияет на цветовую палитру
    """
    import subprocess
    
    # Получаем информацию о видео для оптимизации
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json", 
        "-show_format", "-show_streams", input_path
    ]

    try:
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        if probe_result.returncode == 0:
            import json
            video_info = json.loads(probe_result.stdout)
            # Находим видео поток
            video_stream = None
            for stream in video_info.get('streams', []):
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                    break
            
            if video_stream:
                # Адаптивные настройки на основе исходного видео
                orig_width = int(video_stream.get('width', 640))
                orig_height = int(video_stream.get('height', 480))
                orig_fps = eval(video_stream.get('r_frame_rate', '25/1'))
                
                # Оптимизируем размер для лучшего качества
                if orig_width <= 480:
                    width = min(width, orig_width)
                elif orig_width <= 720:
                    width = min(width, 480)
                else:
                    width = min(width, 540)
                
                # Оптимизируем FPS для плавности
                if orig_fps >= 30:
                    fps = min(fps, 12)  # Ограничиваем для производительности
                elif orig_fps >= 24:
                    fps = min(fps, 10)
                else:
                    fps = min(fps, int(orig_fps))
    except Exception:
        # Если не удалось получить информацию, используем настройки по умолчанию
        pass

    palette_path = output_path + '.palette.png'
    
    # Улучшенные параметры масштабирования
    scale_expr = f"scale='min({width},iw)':-2:flags=lanczos"
    
    # Настройки качества на основе параметра quality
    if quality == "high":
        palette_gen_opts = "stats_mode=full"
        palette_use_opts = "dither=sierra2_4a"
    elif quality == "medium":
        palette_gen_opts = "stats_mode=diff"
        palette_use_opts = "dither=bayer:bayer_scale=5"
    else:  # low
        palette_gen_opts = "stats_mode=single"
        palette_use_opts = "dither=none"
    
    # Первый проход: генерация оптимизированной палитры
    cmd_palette = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"fps={fps},{scale_expr},palettegen={palette_gen_opts}", 
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
        "ffmpeg", "-y", "-i", input_path, "-i", palette_path,
        "-lavfi", f"fps={fps},{scale_expr} [x]; [x][1:v] paletteuse={palette_use_opts}",
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
                await _optimize_large_gif(output_path, width, fps)
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
            "ffmpeg", "-y", "-i", gif_path,
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
            "ffmpeg", "-y", "-i", gif_path,
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


async def upload_file_fallback(file_path: str) -> str:
    """
    Попытка загрузить файл на несколько публичных сервисов и вернуть ссылку.
    Использует последовательность провайдеров: transfer.sh, file.io, 0x0.st.
    В случае неудачи бросает исключение.
    """
    def _upload():
        filename = os.path.basename(file_path)
        # transfer.sh (PUT)
        try:
            with open(file_path, 'rb') as f:
                url = f"https://transfer.sh/{filename}"
                resp = requests.put(url, data=f, timeout=120)
                if resp.status_code == 200:
                    return resp.text.strip()
        except Exception:
            pass

        # file.io (POST)
        try:
            with open(file_path, 'rb') as f:
                resp = requests.post('https://file.io', files={'file': f}, timeout=120)
                if resp.status_code == 200:
                    j = resp.json()
                    # file.io returns {'success': True, 'link': '...'} or similar
                    for key in ('link', 'url'):
                        if key in j:
                            return j[key]
                    if 'success' in j and j.get('success') and 'key' in j:
                        return j.get('key')
        except Exception:
            pass

        # 0x0.st (POST)
        try:
            with open(file_path, 'rb') as f:
                resp = requests.post('https://0x0.st', files={'file': f}, timeout=120)
                if resp.status_code == 200:
                    return resp.text.strip()
        except Exception:
            pass

        raise RuntimeError('All upload providers failed')

    return await asyncio.to_thread(_upload)
