from aiogram import Router, types, F
from kbds import reply
from utils import answer_editable
from data_base import db
from data_base.db import get_user_payments
from aiogram.fsm.context import FSMContext
from handlers.user_privatka import MenuStates, build_dynamic_keyboard
from aiogram.filters import Command

user_reply_router = Router()

# === Главное меню ===
@user_reply_router.message(F.text.in_(["Меню", "/menu", "📘 Меню"]))
async def show_menu(message: types.Message, state: FSMContext):
    await db.log_action(message.from_user.id, "Открыл меню")
    kb = await build_dynamic_keyboard(message.from_user.id)
    await state.set_state(MenuStates.main)
    await answer_editable(message, "📋 Главное меню:", reply_markup=kb)

# === Отправить файл ===
@user_reply_router.message(F.text == "Отправить файл")
async def send_file_prompt(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.waiting_file)
    await message.answer("📂 Пришлите файл для конвертации.")

# === Выбор формата ===
@user_reply_router.message(F.text == "🎞 Форматы")
async def choose_format(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.waiting_format)
    await db.log_action(message.from_user.id, "Открыл выбор формата")
    await answer_editable(
        message,
        "🎞 Выберите нужный формат конвертации:",
        reply_markup=reply.format_choice_kb()
    )

# === Вариант оплаты ===
@user_reply_router.message(F.text.in_(["Вариант оплаты", "💳 Вариант оплаты"]))
async def payment_option(message: types.Message, state: FSMContext):
    await db.log_action(message.from_user.id, "Просмотрел варианты оплаты")
    await state.set_state(MenuStates.payment)
    await answer_editable(
        message,
        "💳 Доступные варианты оплаты:\n\n"
        "1️⃣ По карте Visa / MasterCard\n"
        "2️⃣ Криптовалюта\n"
        "3️⃣ СБП\n\n"
        "После оплаты отправьте скриншот для подтверждения."
    )

# === О боте ===
@user_reply_router.message(F.text.in_(["О боте", "ℹ️ О боте"]))
async def about_bot(message: types.Message, state: FSMContext):
    await db.log_action(message.from_user.id, "Просмотрел информацию о боте")
    await state.set_state(MenuStates.about)
    await answer_editable(
        message,
        """🤖 **Этот бот помогает быстро конвертировать файлы различных форматов.**

🎯 **Возможности:**
• Конвертация аудио (MP3, WAV, OGG)
• Конвертация видео (MP4, MOV → MP3, GIF)
• Обработка изображений (PNG ↔ JPG/JPEG)
• Работа с документами (PDF → PNG, PDF → ZIP, TXT)

💡 **Как использовать:**
1. Отправьте файл боту
2. Выберите нужный формат
3. Получите конвертированный файл ✨

🔒 Максимальный размер файла: 20 МБ"""
    )

# === Конвертация форматов через ReplyKeyboard ===
@user_reply_router.message(F.text.in_(["MP4 ➜ MP3", "MP4 ➜ MOV", "MP4 ➜ GIF"]))
async def format_selected(message: types.Message, state: FSMContext):
    format_map = {
        "MP4 ➜ MP3": "mp3",
        "MP4 ➜ MOV": "mov",
        "MP4 ➜ GIF": "gif",
    }
    chosen = format_map.get(message.text)
    await db.log_action(message.from_user.id, f"Выбрал формат: {chosen}")
    await state.set_state(MenuStates.waiting_file)
    await answer_editable(message, f"✅ Формат {chosen.upper()} выбран.\nТеперь отправь видео для конвертации!")

# === Кнопка Назад (скрывает inline и возвращает ReplyKeyboard) ===
@user_reply_router.message(F.text == "⬅️ Назад")
async def go_back(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    kb = await build_dynamic_keyboard(user_id)
    await state.set_state(MenuStates.main)
    await answer_editable(message, "🔙 Возврат в главное меню", reply_markup=kb)



# === Кнопка Платежи ===
@user_reply_router.message(F.text.in_(["💳Платежи", "💳 Платежи", "Платежи"]))
async def show_payments_handler(message: types.Message):
    user_id = message.from_user.id
    try:
        payments = await db.get_user_payments(user_id)  # асинхронная функция из db.py
    except Exception as e:
        await message.answer("⚠️ Ошибка при получении истории платежей.")
        # лог в консоль
        print("get_user_payments error:", e)
        return

    if not payments:
        await message.answer("📭 У вас пока нет записей о платежах.")
        return

    # Форматируем ответ читабельно
    lines = []
    for p in payments:
        # p — dict: amount, type, description, date (в db мы возвращали dict)
        date = p.get("date", "")
        amount = p.get("amount", 0)
        ptype = p.get("type", "")
        desc = p.get("description", "")
        lines.append(f"{date} — {ptype} {amount} ₽ — {desc}")

    # Разбиваем на сообщения по длине, если много записей
    text = "💳 История платежей:\n\n" + "\n".join(lines)
    await message.answer(text)
