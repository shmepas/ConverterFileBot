import os
import tempfile
import traceback
import zipfile

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None  # Безопасно, если не установлен

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from moviepy.editor import VideoFileClip

formats_router = Router()

# --- FSM состояния ---
class FormatStates(StatesGroup):
    waiting_format = State()
    waiting_file = State()

# --- Клавиатура выбора формата ---
def formats_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="PDF → PNG"), KeyboardButton(text="PDF → ZIP")],
            [KeyboardButton(text="MP3"), KeyboardButton(text="MP4"), KeyboardButton(text="GIF")],
            [KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )

# --- Кнопка запуска ---
@formats_router.message(F.text == "🎞 Форматы")
async def choose_format(message: types.Message, state: FSMContext):
    await state.set_state(FormatStates.waiting_format)
    await message.answer("🎞 Выберите формат для конвертации 👇", reply_markup=formats_kb())

# --- Пользователь выбрал формат ---
@formats_router.message(FormatStates.waiting_format, F.text)
async def format_selected(message: types.Message, state: FSMContext):
    fmt = message.text
    valid_formats = {"PDF → PNG", "PDF → ZIP", "MP3", "MP4", "GIF"}
    if fmt not in valid_formats:
        await message.answer("⚠️ Пожалуйста, выберите формат с клавиатуры.")
        return

    await state.update_data(selected_format=fmt)
    await state.set_state(FormatStates.waiting_file)
    await message.answer(
        f"📁 Отправьте файл для конвертации в формат {fmt}\n\n"
        "Когда закончите — нажмите ⬅️ Назад в меню.",
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
    file = message.document or message.video or message.audio

    temp_dir = tempfile.gettempdir()
    input_path = os.path.join(temp_dir, file.file_name)
    await file.download(destination_file=input_path)

    try:
        if fmt == "PDF → PNG":
            if fitz is None:
                await message.answer("❌ Для конвертации PDF установите PyMuPDF (модуль `PyMuPDF`).")
                return

            doc = fitz.open(input_path)
            out_dir = os.path.join(temp_dir, "pdf_images")
            os.makedirs(out_dir, exist_ok=True)
            image_paths = []

            for i, page in enumerate(doc):
                pix = page.get_pixmap()
                img_path = os.path.join(out_dir, f"page_{i + 1}.png")
                pix.save(img_path)
                image_paths.append(img_path)

            if len(image_paths) == 1:
                await message.answer_document(types.FSInputFile(image_paths[0]), caption="✅ Конвертация завершена!")
            else:
                zip_path = os.path.join(temp_dir, "pdf_pages.zip")
                with zipfile.ZipFile(zip_path, "w") as zipf:
                    for img in image_paths:
                        zipf.write(img, os.path.basename(img))
                await message.answer_document(types.FSInputFile(zip_path), caption="✅ Все страницы в ZIP-архиве!")

        elif fmt == "PDF → ZIP":
            if fitz is None:
                await message.answer("❌ Для работы с PDF установите PyMuPDF (`pip install PyMuPDF`).")
                return

            doc = fitz.open(input_path)
            zip_path = os.path.join(temp_dir, "pdf_images.zip")
            with zipfile.ZipFile(zip_path, "w") as zipf:
                for i, page in enumerate(doc):
                    pix = page.get_pixmap()
                    img_name = f"page_{i + 1}.png"
                    img_path = os.path.join(temp_dir, img_name)
                    pix.save(img_path)
                    zipf.write(img_path, img_name)

            await message.answer_document(types.FSInputFile(zip_path), caption="✅ PDF сконвертирован в ZIP!")

        elif fmt == "MP3":
            clip = VideoFileClip(input_path)
            out_path = os.path.join(temp_dir, "output.mp3")
            clip.audio.write_audiofile(out_path)
            clip.close()
            await message.answer_document(types.FSInputFile(out_path), caption="✅ Аудио сохранено как MP3")

        elif fmt == "MP4":
            clip = VideoFileClip(input_path)
            out_path = os.path.join(temp_dir, "output.mp4")
            clip.write_videofile(out_path, codec="libx264")
            clip.close()
            await message.answer_document(types.FSInputFile(out_path), caption="✅ Видео готово (MP4)")

        elif fmt == "GIF":
            clip = VideoFileClip(input_path)
            out_path = os.path.join(temp_dir, "output.gif")
            clip.write_gif(out_path)
            clip.close()
            await message.answer_document(types.FSInputFile(out_path), caption="✅ GIF готов!")

    except Exception as e:
        await message.answer(f"❌ Ошибка:\n<code>{traceback.format_exc()}</code>", parse_mode="HTML")
    finally:
        for path in (input_path,):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

# --- Кнопка "Назад" ---
@formats_router.message(F.text == "⬅️ Назад в меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    from kbds.reply import main_menu_kb  # подключение твоей клавы
    await message.answer("↩️ Возврат в главное меню.", reply_markup=main_menu_kb())