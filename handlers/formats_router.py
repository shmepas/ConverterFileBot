import os
import tempfile
import traceback
import shutil
import zipfile
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from moviepy.editor import VideoFileClip
from aiogram.fsm.state import StatesGroup, State
from utils import convert_audio_ffmpeg_async  # функция конвертации аудио
from handlers.adminka import FormatStates  # <-- Импорт состояний из проекта
from kbds.reply import format_choice_kb
from kbds.reply import main_menu_kb

# Для PDF конвертации
from PyPDF2 import PdfReader

formats_router = Router()


@formats_router.message(F.text == "🎞 Форматы")
async def start_format(message: types.Message, state: FSMContext):
    await message.answer("Выберите формат:", reply_markup=format_choice_kb)
    await state.set_state(FormatStates.waiting_format)


@formats_router.message(FormatStates.waiting_format)
async def format_selected(message: types.Message, state: FSMContext):
    selected_format = message.text.upper()
    allowed_formats = {"MP3", "MP4", "GIF", "TXT"}
    if selected_format not in allowed_formats:
        await message.answer("❌ Неподдерживаемый формат. Выберите из списка.")
        return
    await state.update_data(selected_format=selected_format)
    await message.answer("Отправьте файл для конвертации (макс. 20 МБ):")
    await state.set_state(FormatStates.waiting_file)


@formats_router.message(FormatStates.waiting_file, F.content_type.in_({"document", "video", "audio"}))
async def convert_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fmt = data.get("selected_format")
    file_obj = message.document or message.video or message.audio

    if not fmt:
        await message.answer("⚠️ Сначала выберите формат.")
        return

    # Проверка размера файла
    file_size = getattr(file_obj, "file_size", 0)
    if file_size > 20 * 1024 * 1024:
        await message.answer("🚫 Файл слишком большой. Максимальный размер — 20 МБ.")
        return

    temp_dir = tempfile.gettempdir()
    file_name = getattr(file_obj, "file_name", f"tempfile_{file_obj.file_id}")
    file_path = os.path.join(temp_dir, file_name)

    try:
        # Скачиваем файл
        file_info = await message.bot.get_file(file_obj.file_id)
        await message.bot.download_file(file_info.file_path, destination=file_path)

        out_file = os.path.join(temp_dir, f"{os.path.splitext(file_name)[0]}.{fmt.lower()}")

        if fmt == "MP3":
            if file_path.lower().endswith((".mp3", ".wav", ".ogg")):
                await convert_audio_ffmpeg_async(file_path, out_file)
            else:
                clip = VideoFileClip(file_path)
                clip.audio.write_audiofile(out_file)
                clip.close()

        elif fmt == "MP4":
            clip = VideoFileClip(file_path)
            clip.write_videofile(out_file, codec="libx264")
            clip.close()

        elif fmt == "GIF":
            clip = VideoFileClip(file_path)
            clip.write_gif(out_file)
            clip.close()

        elif fmt == "TXT":
            ext = os.path.splitext(file_name)[1].lower()

            if ext == ".pdf":
                reader = PdfReader(file_path)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() or ""
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(text)

            elif ext == ".zip":
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    extracted_files = zip_ref.namelist()
                    text = ""
                    for ef in extracted_files:
                        if ef.lower().endswith(('.txt', '.md', '.log')):
                            with zip_ref.open(ef) as file:
                                content = file.read().decode('utf-8', errors='ignore')
                                text += f"\n\n--- {ef} ---\n\n" + content
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(text)

            else:
                if ext in [".txt", ".md", ".log"]:
                    shutil.copy(file_path, out_file)
                else:
                    await message.answer("❌ Неподдерживаемый текстовый формат для конвертации.")
                    return

        else:
            await message.answer("❌ Неподдерживаемый формат.")
            return

        await message.answer_document(FSInputFile(out_file), caption=f"✅ Файл конвертирован в {fmt}")

    except Exception:
        await message.answer(f"❌ Ошибка конвертации:\n<code>{traceback.format_exc()}</code>", parse_mode="HTML")

    finally:
        for path in (file_path, out_file):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        await state.clear()


@formats_router.message(F.text == "⬅️ Назад")
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_kb)
