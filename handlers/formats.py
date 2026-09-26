import os
import tempfile
import asyncio
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile

from utils import answer_editable
from kbds.reply import format_choice_kb, main_menu_kb
from converter_service import file_converter
from data_base import db

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
    user_id = message.from_user.id
    print(f"[FORMATS DEBUG] 🎞 FORMATS BUTTON CLICKED in formats.py for user {user_id}")
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
    """Download, validate, and convert through the shared isolated worker."""
    from utiles.conversion_workflow import ConversionRejected, process_conversion
    from utiles.file_validator import file_validator
    from handlers.user_privatka import MenuStates, build_dynamic_keyboard

    user_id = message.from_user.id
    data = await state.get_data()
    target_format = data.get("selected_format")
    file_obj = message.document or message.video or message.audio
    if not file_obj or not target_format:
        await message.answer("⚠️ Сначала выберите формат и отправьте файл.")
        await state.clear()
        return

    limits = await db.check_user_limits(user_id)
    file_size = getattr(file_obj, "file_size", 0) or 0
    if file_size > limits["max_file_size"]:
        await message.answer(
            f"🚫 Файл превышает лимит тарифа ({limits['max_file_size'] // (1024 * 1024)} МБ)."
        )
        await state.clear()
        return

    work_dir = tempfile.mkdtemp(prefix=f"converter_user_{user_id}_")
    source_name = getattr(file_obj, "file_name", None) or f"upload_{file_obj.file_unique_id}.bin"
    safe_name = file_validator.get_safe_filename(source_name, user_id)
    file_path = os.path.join(work_dir, safe_name)
    try:
        file_info = await message.bot.get_file(file_obj.file_id)
        if file_info.file_size and file_info.file_size > limits["max_file_size"]:
            raise ValueError("Файл превышает лимит тарифа")
        await message.bot.download_file(file_info.file_path, destination=file_path, timeout=300)
        source_type = file_validator.detect_file_type(file_path)
        if target_format not in file_validator.TARGET_FORMATS_BY_INPUT_TYPE.get(source_type, []):
            raise ValueError("Содержимое файла не подходит для выбранного формата")
        canonical_path = os.path.splitext(file_path)[0] + file_validator.canonical_extension(file_path, source_type)
        if canonical_path != file_path:
            os.replace(file_path, canonical_path)
            file_path = canonical_path
        await process_conversion(
            message, state, user_id, file_path, source_name, source_type, target_format
        )
    except ConversionRejected as exc:
        await message.answer(str(exc))
    except Exception:
        await message.answer("❌ Не удалось скачать или проверить файл. Проверьте формат и размер.")
    finally:
        file_converter.cleanup_files(file_path, work_dir)
        await state.set_state(MenuStates.main)
        await message.answer("↩️ Возврат в главное меню.", reply_markup=await build_dynamic_keyboard(user_id))

# --- Кнопка "Назад" ---
@formats_router.message(F.text == "⬅️ Назад в меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Возврат в главное меню.", reply_markup=main_menu_kb())
