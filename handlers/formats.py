import os
import traceback
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile

from utils import answer_editable
from kbds.reply import format_choice_kb, main_menu_kb
from converter_service import file_converter

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
    """Обработка загруженного файла для конвертации."""
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

    import tempfile
    temp_dir = tempfile.gettempdir()
    file_name = getattr(file_obj, "file_name", f"tempfile_{file_obj.file_id}")
    file_path = os.path.join(temp_dir, file_name)

    try:
        # Скачиваем файл
        file_info = await message.bot.get_file(file_obj.file_id)
        await message.bot.download_file(file_info.file_path, destination=file_path)

        # Используем централизованный сервис конвертации
        output_path = file_converter.convert_file(file_path, fmt)

        # Отправляем результат пользователю
        await message.answer_document(FSInputFile(output_path), caption=f"✅ Файл конвертирован в {fmt}")

    except Exception:
        await message.answer(f"❌ Ошибка конвертации:\n<code>{traceback.format_exc()}</code>", parse_mode="HTML")

    finally:
        # Очищаем временные файлы
        file_converter.cleanup_files(file_path, locals().get('output_path'))
        await state.clear()

# --- Кнопка "Назад" ---
@formats_router.message(F.text == "⬅️ Назад в меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Возврат в главное меню.", reply_markup=main_menu_kb())