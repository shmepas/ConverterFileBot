import os
from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from data_base.db import (
    log_action, is_admin, is_super_admin,
    add_admin, remove_admin, get_user_logs, get_user_payments
)

admin_router = Router()
PAGE_SIZE = 20

# ------------------------------
# FSM состояния для админки
# ------------------------------
class AdminStates(StatesGroup):
    main = State()
    add_admin_wait_id = State()
    remove_admin_wait_id = State()
    view_logs_page = State()
    view_payments_page = State()

# ------------------------------
# Главная клавиатура админа
# ------------------------------
async def admin_main_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    kb_builder = ReplyKeyboardBuilder()
    if await is_super_admin(user_id):
        kb_builder.row(
            KeyboardButton(text="📜 Просмотр логов"),
            KeyboardButton(text="➕ Добавить админа"),
            KeyboardButton(text="➖ Удалить админа"),
            KeyboardButton(text="💳 Платежи")
        )
    elif await is_admin(user_id):
        kb_builder.row(
            KeyboardButton(text="📜 Просмотр логов"),
            KeyboardButton(text="💳 Платежи")
        )
    kb_builder.row(KeyboardButton(text="⬅️ Закрыть админку"))
    return kb_builder.as_markup(resize_keyboard=True)

# ------------------------------
# Открытие админки
# ------------------------------
@admin_router.message(F.text == "Админка")
async def open_admin_panel(message: types.Message):
    user_id = message.from_user.id
    if await is_admin(user_id) or await is_super_admin(user_id):
        kb = await admin_main_kb(user_id)
        await message.answer("👋 Добро пожаловать в админ-панель!", reply_markup=kb)
    else:
        await message.answer("🚫 У вас нет доступа к админ-панели.")

# ------------------------------
# Моя роль
# ------------------------------
@admin_router.message(F.text == "Моя роль")
async def show_my_role(message: types.Message):
    user_id = message.from_user.id
    if await is_super_admin(user_id):
        role = "🌟 Супер-админ"
    elif await is_admin(user_id):
        role = "🛠 Админ"
    else:
        role = "👤 Пользователь"
    await message.answer(f"Ваша роль: {role}")

# ------------------------------
# Добавление админа
# ------------------------------
@admin_router.message(F.text == "➕ Добавить админа")
async def add_admin_start(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id):
        await message.answer("❌ Только супер-админ может добавлять админов.")
        return
    await state.set_state(AdminStates.add_admin_wait_id)
    await message.answer("Введите ID пользователя для добавления в админы:")

@admin_router.message(F.text, AdminStates.add_admin_wait_id)
async def add_admin_confirm(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await add_admin(user_id)
        await message.answer(f"✅ Пользователь {user_id} добавлен как админ.")
        await log_action(message.from_user.id, f"Добавил админа {user_id}")
    except ValueError:
        await message.answer("❌ Неверный ID, введите числовой ID.")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)

# ------------------------------
# Удаление админа
# ------------------------------
@admin_router.message(F.text == "➖ Удалить админа")
async def remove_admin_start(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id):
        await message.answer("❌ Только супер-админ может удалять админов.")
        return
    await state.set_state(AdminStates.remove_admin_wait_id)
    await message.answer("Введите ID пользователя для удаления из админов:")

@admin_router.message(F.text, AdminStates.remove_admin_wait_id)
async def remove_admin_confirm(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await remove_admin(user_id)
        await message.answer(f"✅ Пользователь {user_id} удален из админов.")
        await log_action(message.from_user.id, f"Удалил админа {user_id}")
    except ValueError:
        await message.answer("❌ Неверный ID, введите числовой ID.")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)

# ------------------------------
# Просмотр логов
# ------------------------------
@admin_router.message(F.text == "📜 Просмотр логов")
async def view_logs_start(message: types.Message, state: FSMContext):
    await state.set_state(AdminStates.view_logs_page)
    await state.update_data(page=1)
    await send_logs_page(message, state)

async def send_logs_page(message: types.Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)

    logs = await get_user_logs(limit=500)
    if not logs:
        await message.answer("📭 Логов пока нет.")
        return

    total = len(logs)
    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_logs = logs[start:end]

    text_lines = []
    for log in page_logs:
        uid = log.get("user_id", "")
        ts = log.get("timestamp", "")
        action = log.get("action", "")
        text_lines.append(f"👤 {uid} | 🕒 {ts}\n➡️ {action}")

    text = f"📜 Логи — страница {page}/{total_pages}\n\n" + "\n\n".join(text_lines)

    builder = ReplyKeyboardBuilder()
    if page > 1:
        builder.add(KeyboardButton(text="⬅️ Назад"))
    if page < total_pages:
        builder.add(KeyboardButton(text="▶️ Далее"))
    builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))

    kb = builder.as_markup(resize_keyboard=True)
    await message.answer(text, reply_markup=kb)
    await state.update_data(page=page)

@admin_router.message(AdminStates.view_logs_page)
async def logs_navigation(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()
    page = data.get("page", 1)

    if text == "⬅️ Назад":
        page = max(1, page - 1)
        await state.update_data(page=page)
        await send_logs_page(message, state)
    elif text == "▶️ Далее":
        page += 1
        await state.update_data(page=page)
        await send_logs_page(message, state)
    elif text == "⬅️ Выйти в главное меню":
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)
    else:
        await message.answer("❌ Неизвестная команда. Используйте кнопки ниже.")

# ------------------------------
# Просмотр платежей
# ------------------------------
@admin_router.message(F.text == "💳 Платежи")
async def view_payments_start(message: types.Message, state: FSMContext):
    await state.set_state(AdminStates.view_payments_page)
    await state.update_data(page=1)
    await send_payments_page(message, state)

async def send_payments_page(message: types.Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)

    payments = await get_user_payments(limit=500)
    if not payments:
        await message.answer("📭 История платежей пока пуста.")
        return

    total = len(payments)
    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_payments = payments[start:end]

    text_lines = []
    for p in page_payments:
        uid = p.get("user_id", "")
        amount = p.get("amount", "")
        ts = p.get("timestamp", "")
        text_lines.append(f"👤 {uid} | 💰 {amount} | 🕒 {ts}")

    text = f"💳 История платежей — страница {page}/{total_pages}\n\n" + "\n\n".join(text_lines)

    builder = ReplyKeyboardBuilder()
    if page > 1:
        builder.add(KeyboardButton(text="⬅️ Назад"))
    if page < total_pages:
        builder.add(KeyboardButton(text="▶️ Далее"))
    builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))

    kb = builder.as_markup(resize_keyboard=True)
    await message.answer(text, reply_markup=kb)
    await state.update_data(page=page)

@admin_router.message(AdminStates.view_payments_page)
async def payments_navigation(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()
    page = data.get("page", 1)

    if text == "⬅️ Назад":
        page = max(1, page - 1)
        await state.update_data(page=page)
        await send_payments_page(message, state)
    elif text == "▶️ Далее":
        page += 1
        await state.update_data(page=page)
        await send_payments_page(message, state)
    elif text == "⬅️ Выйти в главное меню":
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)
    else:
        await message.answer("❌ Неизвестная команда. Используйте кнопки ниже.")

# ------------------------------
# Закрытие админки
# ------------------------------
@admin_router.message(F.text == "⬅️ Закрыть админку")
async def close_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Админка закрыта ✅", reply_markup=ReplyKeyboardRemove())
    await log_action(message.from_user.id, "Закрыл админку")
