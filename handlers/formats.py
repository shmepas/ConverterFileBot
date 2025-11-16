import os
import tempfile
from tkinter import Image
import traceback
import shutil
import zipfile

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None  # Безопасно, если не установлен

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
from moviepy.editor import VideoFileClip
from PyPDF2 import PdfReader

from utils import convert_audio_ffmpeg_async, compress_video_ffmpeg_async, convert_video_to_gif_ffmpeg_async, answer_editable
from kbds.reply import format_choice_kb, main_menu_kb

formats_router = Router()

# --- FSM состояния ---
class FormatStates(StatesGroup):
    waiting_format = State()
    waiting_file = State()

# --- Клавиатура выбора формата ---
def formats_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="MP3"), KeyboardButton(text="MP4"), KeyboardButton(text="GIF")],
            [KeyboardButton(text="TXT"), KeyboardButton(text="PDF → PNG")],
            [KeyboardButton(text="PDF → ZIP"), KeyboardButton(text="PNG → JPG")],
            [KeyboardButton(text="PNG → JPEG"), KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )

# --- Кнопка запуска ---
@formats_router.message(F.text == "🎞 Форматы")
async def choose_format(message: types.Message, state: FSMContext):
    await state.set_state(FormatStates.waiting_format)
    await answer_editable(message, "🎞 Выберите формат для конвертации 👇", reply_markup=formats_kb())

# --- Пользователь выбрал формат ---
@formats_router.message(FormatStates.waiting_format)
async def format_selected(message: types.Message, state: FSMContext):
    fmt = message.text
    valid_formats = {"PDF → PNG", "PDF → ZIP", "MP3", "MP4", "GIF", "TXT", "PNG → JPG", "PNG → JPEG"}
    if fmt not in valid_formats:
        await answer_editable(message, "⚠️ Пожалуйста, выберите формат с клавиатуры.")
        return

    await state.update_data(selected_format=fmt)
    await state.set_state(FormatStates.waiting_file)
    await answer_editable(
        message,
        f"📁 Отправьте файл для конвертации в формат {fmt}\n"
        f"Максимальный размер: 20 МБ",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад в меню")]],
            resize_keyboard=True
        )
    )

# --- Обработка загруженного файла ---
@formats_router.message(FormatStates.waiting_file, F.content_type.in_({"document", "video", "audio"}))
async def convert_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fmt = data.get("selected_format")
    file_obj = message.document or message.video or message.audio

    if not fmt:
        await message.answer("⚠️ Сначала выберите формат.")
        await state.clear()
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

        out_file = os.path.join(temp_dir, f"{os.path.splitext(file_name)[0]}.{fmt.split('→')[-1].strip().lower()}")

        if fmt == "MP3":
            if file_path.lower().endswith((".mp3", ".wav", ".ogg")):
                await convert_audio_ffmpeg_async(file_path, out_file)
            else:
                clip = VideoFileClip(file_path)
                clip.audio.write_audiofile(out_file, verbose=False, logger=None)
                clip.close()

        elif fmt == "MP4":
            try:
                # Prefer ffmpeg compression to control output size
                await compress_video_ffmpeg_async(file_path, out_file, crf=30, max_width=640, audio_bitrate="64k", preset="fast")
            except Exception:
                # Fallback to moviepy with constrained bitrate
                clip = VideoFileClip(file_path)
                clip.write_videofile(out_file, codec="libx264", bitrate="600k", fps=24, audio=True, verbose=False, logger=None)
                clip.close()

        elif fmt == "GIF":
            try:
                await convert_video_to_gif_ffmpeg_async(file_path, out_file, width=480, fps=12)
            except Exception:
                clip = VideoFileClip(file_path)
                clip.write_gif(out_file, fps=12)
                clip.close()

        elif fmt == "PDF → PNG":
            if fitz is None:
                await message.answer("❌ Для конвертации PDF установите PyMuPDF: `pip install PyMuPDF`")
                return

            doc = fitz.open(file_path)
            out_dir = os.path.join(temp_dir, "pdf_images")
            os.makedirs(out_dir, exist_ok=True)
            image_paths = []

            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_path = os.path.join(out_dir, f"page_{i + 1}.png")
                pix.save(img_path)
                image_paths.append(img_path)

            if len(image_paths) == 1:
                await message.answer_document(FSInputFile(image_paths[0]), caption="✅ Конвертация завершена!")
            else:
                zip_path = os.path.join(temp_dir, "pdf_pages.zip")
                with zipfile.ZipFile(zip_path, "w") as zipf:
                    for img in image_paths:
                        zipf.write(img, os.path.basename(img))
                await message.answer_document(FSInputFile(zip_path), caption="✅ Все страницы в ZIP-архиве!")

        elif fmt == "PDF → ZIP":
            if fitz is None:
                await message.answer("❌ Для работы с PDF установите PyMuPDF: `pip install PyMuPDF`")
                return

            doc = fitz.open(file_path)
            zip_path = os.path.join(temp_dir, "pdf_images.zip")
            with zipfile.ZipFile(zip_path, "w") as zipf:
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_name = f"page_{i + 1}.png"
                    img_path = os.path.join(temp_dir, img_name)
                    pix.save(img_path)
                    zipf.write(img_path, img_name)
                    os.remove(img_path)

            await message.answer_document(FSInputFile(zip_path), caption="✅ PDF сконвертирован в ZIP!")

        elif fmt == "PNG → JPG" or fmt == "PNG → JPEG":
            target_ext = "jpg" if fmt == "PNG → JPG" else "jpeg"
            out_file = os.path.join(temp_dir, f"{os.path.splitext(file_name)[0]}.{target_ext}")
            try:
                img = Image.open(file_path)
                # Convert RGBA to RGB if needed
                if img.mode in ("RGBA", "LA", "P"):
                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "RGBA":
                        rgb_img.paste(img, mask=img.split()[-1])
                    else:
                        rgb_img.paste(img)
                    rgb_img.save(out_file, "JPEG", quality=90)
                else:
                    img.save(out_file, "JPEG", quality=90)
            except Exception as e:
                await message.answer(f"❌ Ошибка при конвертации PNG: {str(e)}")
                return

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

            elif ext in [".txt", ".md", ".log"]:
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

# --- Кнопка "Назад" ---
@formats_router.message(F.text == "⬅️ Назад в меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Возврат в главное меню.", reply_markup=main_menu_kb())