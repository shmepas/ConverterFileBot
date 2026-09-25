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
    """Обработка загруженного файла для конвертации."""
    user_id = message.from_user.id
    data = await state.get_data()
    fmt = data.get("selected_format")
    file_obj = message.document or message.video or message.audio

    if not fmt:
        await message.answer("⚠️ Сначала выберите формат.")
        await state.clear()
        return

    # Проверяем тариф и размер до списания дневной квоты.
    limits = await db.check_user_limits(user_id)
    file_size = getattr(file_obj, "file_size", 0) or 0
    if file_size > limits['max_file_size']:
        await message.answer(
            f"🚫 Файл слишком большой: {file_size // (1024*1024)} МБ. "
            f"Лимит тарифа: {limits['max_file_size'] // (1024*1024)} МБ."
        )
        await state.clear()
        return

    charged = False
    if not limits['is_premium']:
        charged = await db.increment_conversion_count(user_id)
        if not charged:
            limit_message = f"""🚫 **Дневной лимит исчерпан!**

📊 **Ваш статус:**
• Использовано сегодня: {limits['current_count']}/{limits['daily_limit']} конвертаций
• Осталось: 0 конвертаций

💎 **Хотите больше возможностей?**

✅ **Премиум подписка даёт:**
• 🚀 Безлимитные конвертации
• 📁 Файлы до 100 МБ (вместо 20 МБ)
• 🎯 Все форматы без ограничений
• ⚡ Приоритетная обработка
• 🔧 Расширенные функции

💰 **Стоимость:** от 299₽/месяц

Для покупки подписки нажмите "💰 Вариант оплаты" в меню"""

            await message.answer(limit_message, parse_mode="Markdown")
            await state.clear()
            return

    work_dir = tempfile.mkdtemp(prefix=f"converter_user_{user_id}_")
    file_path = os.path.join(work_dir, "input")
    output_path = None

    try:
        # Скачиваем файл
        file_info = await message.bot.get_file(file_obj.file_id)
        original_name = getattr(file_obj, "file_name", None) or file_info.file_path or "upload.bin"
        extension = os.path.splitext(original_name)[1][:16]
        file_path = os.path.join(work_dir, f"input{extension}")
        await message.bot.download_file(file_info.file_path, destination=file_path)

        # Используем централизованный сервис конвертации
        output_path = await asyncio.to_thread(file_converter.convert_file, file_path, fmt, user_id)

        # Отправляем результат пользователю
        await message.answer_document(FSInputFile(output_path), caption=f"✅ Файл конвертирован в {fmt}")

        # Для бесплатных пользователей показываем информацию об оставшихся конвертациях
        if not limits['is_premium']:
            final_count = await db.get_daily_conversion_count(user_id)
            remaining = max(0, 5 - final_count)
            await message.answer(f"📊 Конвертация выполнена!\n\nОсталось конвертаций на сегодня: {remaining}/5")

    except Exception as e:
        if charged:
            await db.decrement_conversion_count(user_id)
        await message.answer("❌ Не удалось обработать файл. Проверьте формат файла и попробуйте снова.")

    finally:
        # Очищаем временные файлы
        file_converter.cleanup_files(file_path, output_path, work_dir)
        await state.clear()

# --- Кнопка "Назад" ---
@formats_router.message(F.text == "⬅️ Назад в меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Возврат в главное меню.", reply_markup=main_menu_kb())
