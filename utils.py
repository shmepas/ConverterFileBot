from aiogram import types
from kbds import reply
import asyncio
import subprocess
from kbds.admin_reply import admin_kb, super_admin_kb
from data_base.db import is_admin, is_super_admin
from aiogram.utils.keyboard import ReplyKeyboardBuilder

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
