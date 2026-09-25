import os
import tempfile
from html import escape
import traceback
import zipfile
import shutil
import asyncio  # добавлен для асинхронного вызова ffmpeg
import subprocess
import time
from kbds.reply import main_menu_kb
from aiogram.exceptions import TelegramBadRequest

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None  # Безопасно, если не установлен

from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from moviepy.editor import VideoFileClip
from utils import FFMPEG_BINARY, compress_video_ffmpeg_async, convert_video_to_gif_ffmpeg_async, answer_editable
from utiles.performance import get_conversion_stats, get_popular_formats
from converter_service import file_converter
from utiles.progress_tracker import progress_tracker

from data_base.db import (
    log_action, is_admin, is_super_admin, has_admin_access,
    add_admin, remove_admin, get_user_logs,
    get_all_subscriptions, get_pending_payments, mark_payment_completed,
    get_all_payments,
    activate_premium_subscription, deactivate_subscription
)

admin_router = Router()
PAGE_SIZE = 20


async def _is_admin_user(message: types.Message) -> bool:
    if message.chat.type != "private" or message.from_user is None:
        return False
    return await has_admin_access(message.from_user.id)


# Admin handlers default to deny unless this filter confirms a privileged role.
admin_router.message.filter(_is_admin_user)

# ------------------------------
# 💉 FIX: Клавиатура обычного пользователя
# ------------------------------
# ------------------------------
# FSM состояния
# ------------------------------
class AdminStates(StatesGroup):
    main = State()
    add_admin_wait_id = State()
    remove_admin_wait_id = State()
    view_logs_page = State()
    view_payments_page = State()
    subscription_management = State()
    activate_premium_wait_id = State()
    deactivate_subscription_wait_id = State()

class FormatStates(StatesGroup):
    waiting_format = State()
    waiting_file = State()

# ------------------------------
# Главная клавиатура админа
# ------------------------------
async def admin_main_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    kb_builder = ReplyKeyboardBuilder()
    if await is_super_admin(user_id):
        # Для супер-админа - добавляем управление подписками
        kb_builder.row(
            KeyboardButton(text="📜 Просмотр логов"),
            KeyboardButton(text="💎 Управление подписками")
        )
        kb_builder.row(
            KeyboardButton(text="➕ Добавить админа"),
            KeyboardButton(text="➖ Удалить админа")
        )
        kb_builder.row(
            KeyboardButton(text="🧾 Все платежи"),
            KeyboardButton(text="🎞 Форматы"),
            KeyboardButton(text="🎫 Тикеты")
        )
        kb_builder.row(
            KeyboardButton(text="📊 Статистика производительности"),
            KeyboardButton(text="⬅️ Закрыть админку")
        )
    elif await is_admin(user_id):
        # Для обычного админа - просмотр подписок без управления
        kb_builder.row(
            KeyboardButton(text="📜 Просмотр логов"),
            KeyboardButton(text="🧾 Все платежи")
        )
        kb_builder.row(
            KeyboardButton(text="💎 Просмотр подписок"),
            KeyboardButton(text="🎞 Форматы"),
            KeyboardButton(text="🎫 Тикеты")
        )
        kb_builder.row(KeyboardButton(text="⬅️ Закрыть админку"))
    return kb_builder.as_markup(resize_keyboard=True)

# ------------------------------
# Клавиатура выбора формата
# ------------------------------
def formats_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="MP3"), KeyboardButton(text="MP4"), KeyboardButton(text="GIF")],
            [KeyboardButton(text="TXT"), KeyboardButton(text="PDF → PNG"), KeyboardButton(text="PDF → ZIP")],
            [KeyboardButton(text="PNG → JPG"), KeyboardButton(text="PNG → JPEG"), KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )

# ------------------------------
# Асинхронная функция для конвертации аудио с помощью ffmpeg
# ------------------------------
async def convert_audio_ffmpeg_async(input_path: str, output_path: str):
    cmd = [
        FFMPEG_BINARY,
        "-y",
        "-i", input_path,
        "-vn",
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "192k",
        output_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {stderr.decode()}")

# ------------------------------
# Открытие админки
# ------------------------------
@admin_router.message(F.text == "Админка")
async def open_admin_panel(message: types.Message):
    user_id = message.from_user.id
    if await is_admin(user_id) or await is_super_admin(user_id):
        await log_action(user_id, "Открыл админ-панель")
        kb = await admin_main_kb(user_id)
        await answer_editable(message, "👋 Добро пожаловать в админ-панель!", reply_markup=kb)
    else:
        await log_action(user_id, "Попытался открыть админ-панель (нет доступа)")
        await answer_editable(message, "🚫 У вас нет доступа к админ-панели.")

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


@admin_router.message(F.text == "💎 Просмотр подписок")
async def view_subscriptions_readonly(message: types.Message, state: FSMContext):
    """Regular admins may view subscriptions but cannot manage them."""
    await state.clear()
    subscriptions = await get_all_subscriptions(limit=50)
    if not subscriptions:
        text = "📭 Подписок пока нет."
    else:
        rows = ["💎 Подписки (последние 50):", ""]
        for subscription in subscriptions:
            username = subscription.get("username")
            label = f"@{username}" if username else str(subscription["user_id"])
            status = "активна" if subscription.get("is_active") else "неактивна"
            rows.append(f"{label} — {subscription.get('plan_type', 'free')}, {status}")
        text = "\n".join(rows)
    await answer_editable(message, text, reply_markup=await admin_main_kb(message.from_user.id))

# ------------------------------
# Добавление админа
# ------------------------------
@admin_router.message(F.text == "➕ Добавить админа")
async def add_admin_start(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id):
        await log_action(message.from_user.id, "Попытался добавить админа (нет прав супер-админа)")
        await answer_editable(message, "❌ Только супер-админ может добавлять админов.")
        return
    await log_action(message.from_user.id, "Нажал кнопку '➕ Добавить админа'")
    await state.set_state(AdminStates.add_admin_wait_id)
    await answer_editable(message, "Введите ID пользователя для добавления в админы:")

@admin_router.message(F.text == "💎 Управление подписками")
async def subscription_management_start(message: types.Message, state: FSMContext):
    """Начало управления подписками для супер-админа."""
    if not await is_super_admin(message.from_user.id):
        await log_action(message.from_user.id, "Попытался управлять подписками (нет прав супер-админа)")
        await answer_editable(message, "❌ Только супер-админ может управлять подписками.")
        return

    await log_action(message.from_user.id, "Нажал кнопку '💎 Управление подписками'")
    await state.clear()
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Все подписки"), KeyboardButton(text="⏳ Ожидающие платежи")],
            [KeyboardButton(text="✅ Активировать подписку"), KeyboardButton(text="❌ Деактивировать подписку")],
            [KeyboardButton(text="⬅️ Назад в админку")],
        ],
        resize_keyboard=True
    )
    await answer_editable(message, "💎 Управление подписками. Выберите действие:", reply_markup=kb)

@admin_router.message(AdminStates.subscription_management)
async def subscription_management_handler(message: types.Message, state: FSMContext):
    """Обработчик действий в меню управления подписками."""
    if message.text == "👤 Активировать премиум":
        await log_action(message.from_user.id, "Выбрал активацию премиум подписки")
        await state.set_state(AdminStates.activate_premium_wait_id)
        await answer_editable(message, "Введите ID пользователя для активации премиум подписки:")

    elif message.text == "🚫 Деактивировать подписку":
        await log_action(message.from_user.id, "Выбрал деактивацию подписки")
        await state.set_state(AdminStates.deactivate_subscription_wait_id)
        await answer_editable(message, "Введите ID пользователя для деактивации подписки:")

    elif message.text == "📊 Просмотр статистики":
        await log_action(message.from_user.id, "Выбрал просмотр статистики подписок")
        await show_subscriptions_stats(message, state)

    elif message.text == "⬅️ Назад в главное меню":
        await log_action(message.from_user.id, "Вернулся в главное меню из управления подписками")
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

    else:
        await answer_editable(message, "❌ Неизвестная команда. Используйте кнопки ниже.")

@admin_router.message(AdminStates.activate_premium_wait_id, F.text)
async def activate_premium_confirm(message: types.Message, state: FSMContext):
    """Подтверждение активации премиум подписки."""
    try:
        user_id = int(message.text)
        await activate_premium_subscription(user_id, duration_months=1)
        await log_action(message.from_user.id, f"Активировал премиум подписку для пользователя {user_id}")
        await answer_editable(message, f"✅ Премиум подписка активирована для пользователя {user_id} на 1 месяц.")
    except ValueError:
        await log_action(message.from_user.id, f"Попытался активировать подписку с неверным ID: {message.text}")
        await answer_editable(message, "❌ Неверный ID, введите числовой ID.")
    except Exception as e:
        await log_action(message.from_user.id, f"Ошибка активации подписки для {message.text}: {e}")
        await answer_editable(message, f"❌ Ошибка активации подписки: {e}")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

@admin_router.message(AdminStates.deactivate_subscription_wait_id, F.text)
async def deactivate_subscription_confirm(message: types.Message, state: FSMContext):
    """Подтверждение деактивации подписки."""
    try:
        user_id = int(message.text)
        await deactivate_subscription(user_id)
        await log_action(message.from_user.id, f"Деактивировал подписку для пользователя {user_id}")
        await answer_editable(message, f"✅ Подписка деактивирована для пользователя {user_id}.")
    except ValueError:
        await log_action(message.from_user.id, f"Попытался деактивировать подписку с неверным ID: {message.text}")
        await answer_editable(message, "❌ Неверный ID, введите числовой ID.")
    except Exception as e:
        await log_action(message.from_user.id, f"Ошибка деактивации подписки для {message.text}: {e}")
        await answer_editable(message, f"❌ Ошибка деактивации подписки: {e}")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

async def show_subscriptions_stats(message: types.Message, state: FSMContext):
    """Показывает статистику подписок."""
    try:
        subscriptions = await get_all_subscriptions(limit=100)

        if not subscriptions:
            await answer_editable(message, "📊 Статистика подписок:\n\nПока нет данных о подписках.")
            return

        # Подсчитываем статистику
        total_users = len(subscriptions)
        premium_users = len([s for s in subscriptions if s.get('plan_type') == 'premium' and s.get('is_active')])
        free_users = total_users - premium_users

        stats_text = f"""📊 **Статистика подписок**

👥 **Всего пользователей:** {total_users}
💎 **Премиум подписчики:** {premium_users}
🔄 **Бесплатные пользователи:** {free_users}

📈 **Детализация:**
"""

        # Добавляем детализацию по пользователям
        for sub in subscriptions[:10]:  # Показываем только первых 10
            user_id = sub.get('user_id', 'N/A')
            username = sub.get('username', 'N/A')
            plan_type = sub.get('plan_type', 'free')
            is_active = sub.get('is_active', 0)
            status = "✅ Активна" if is_active else "❌ Неактивна"

            stats_text += f"• ID: {user_id} (@{username}) - {plan_type} ({status})\n"

        if len(subscriptions) > 10:
            stats_text += f"\n... и ещё {len(subscriptions) - 10} пользователей"

        await answer_editable(message, stats_text, parse_mode="Markdown")

    except Exception as e:
        await log_action(message.from_user.id, f"Ошибка получения статистики подписок: {e}")
        await answer_editable(message, f"❌ Ошибка получения статистики: {e}")

    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

@admin_router.message(AdminStates.add_admin_wait_id, F.text)
async def add_admin_confirm(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        success, result_message = await add_admin(user_id)
        await log_action(message.from_user.id, f"Попытка добавить пользователя {user_id} как админа: {result_message}")

        if success:
            await answer_editable(message, f"✅ {result_message}")
        else:
            await answer_editable(message, f"❌ {result_message}")

    except ValueError:
        await log_action(message.from_user.id, f"Попытался добавить админа с неверным ID: {message.text}")
        await answer_editable(message, "❌ Неверный ID, введите числовой ID.")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

# ------------------------------
# Удаление админа
# ------------------------------
@admin_router.message(F.text == "➖ Удалить админа")
async def remove_admin_start(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id):
        await log_action(message.from_user.id, "Попытался удалить админа (нет прав супер-админа)")
        await answer_editable(message, "❌ Только супер-админ может удалять админов.")
        return
    await log_action(message.from_user.id, "Нажал кнопку '➖ Удалить админа'")
    await state.set_state(AdminStates.remove_admin_wait_id)
    await answer_editable(message, "Введите ID пользователя для удаления из админов:")

@admin_router.message(AdminStates.remove_admin_wait_id, F.text)
async def remove_admin_confirm(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        success, result_message = await remove_admin(user_id)
        await log_action(message.from_user.id, f"Попытка удалить пользователя {user_id} из админов: {result_message}")

        if success:
            await answer_editable(message, f"✅ {result_message}")
        else:
            await answer_editable(message, f"❌ {result_message}")

    except ValueError:
        await log_action(message.from_user.id, f"Попытался удалить админа с неверным ID: {message.text}")
        await answer_editable(message, "❌ Неверный ID, введите числовой ID.")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

# ------------------------------
# Просмотр логов
# ------------------------------
@admin_router.message(F.text == "📜 Просмотр логов")
async def view_logs_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '📜 Просмотр логов'")
    await state.set_state(AdminStates.view_logs_page)
    await state.update_data(page=1)
    await send_logs_page(message, state)

async def send_logs_page(message: types.Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)
    logs = await get_user_logs(limit=500)
    if not logs:
        builder = ReplyKeyboardBuilder()
        builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))
        await answer_editable(message, "📭 Логов пока нет.", reply_markup=builder.as_markup(resize_keyboard=True))
        return

    total = len(logs)
    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_logs = logs[start:end]

    text = f"📜 Логи — страница {page}/{total_pages}\n\n"
    for log in page_logs:
        uid = log.get("user_id", "")
        ts = log.get("timestamp", "")
        action = log.get("action", "")
        text += f"👤 {uid} | 🕒 {ts}\n➡️ {action}\n\n"

    # Создаем клавиатуру с кнопками одинакового размера
    builder = ReplyKeyboardBuilder()
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(KeyboardButton(text="⬅️ Назад"))
    if page < total_pages:
        navigation_buttons.append(KeyboardButton(text="▶️ Далее"))

    # Добавляем навигационные кнопки в ряд
    if navigation_buttons:
        builder.row(*navigation_buttons)

    # Добавляем кнопку выхода
    builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))

    kb = builder.as_markup(resize_keyboard=True)
    await answer_editable(message, text.strip(), reply_markup=kb)
    await state.update_data(page=page)

@admin_router.message(AdminStates.view_logs_page)
async def logs_navigation(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()
    page = data.get("page", 1)
    if text == "⬅️ Назад":
        await state.update_data(page=max(1, page - 1))
        await send_logs_page(message, state)
    elif text == "▶️ Далее":
        await state.update_data(page=page + 1)
        await send_logs_page(message, state)
    elif text == "⬅️ Выйти в главное меню":
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)
    else:
        await answer_editable(message, "❌ Неизвестная команда. Используйте кнопки ниже.", reply_markup=ReplyKeyboardRemove())

# ------------------------------
# Просмотр платежей
# ------------------------------
@admin_router.message(F.text == "🧾 Все платежи")
async def view_payments_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '💳 Платежи'")
    await state.set_state(AdminStates.view_payments_page)
    await state.update_data(page=1)
    await send_payments_page(message, state)

async def send_payments_page(message: types.Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)
    payments = await get_all_payments(limit=500)
    if not payments:
        builder = ReplyKeyboardBuilder()
        builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))
        await answer_editable(message, "📭 История платежей пока пуста.", reply_markup=builder.as_markup(resize_keyboard=True))
        return

    total = len(payments)
    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_payments = payments[start:end]

    text = f"💳 История платежей — страница {page}/{total_pages}\n\n"
    for p in page_payments:
        text += f"👤 {p.get('user_id')} | 💰 {p.get('amount')} | 🕒 {p.get('date')}\n\n"

    # Создаем клавиатуру с кнопками одинакового размера
    builder = ReplyKeyboardBuilder()
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(KeyboardButton(text="⬅️ Назад"))
    if page < total_pages:
        navigation_buttons.append(KeyboardButton(text="▶️ Далее"))

    # Добавляем навигационные кнопки в ряд
    if navigation_buttons:
        builder.row(*navigation_buttons)

    # Добавляем кнопку выхода
    builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))

    kb = builder.as_markup(resize_keyboard=True)
    await answer_editable(message, text.strip(), reply_markup=kb)
    await state.update_data(page=page)

@admin_router.message(AdminStates.view_payments_page)
async def payments_navigation(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()
    page = data.get("page", 1)
    if text == "⬅️ Назад":
        await state.update_data(page=max(1, page - 1))
        await send_payments_page(message, state)
    elif text == "▶️ Далее":
        await state.update_data(page=page + 1)
        await send_payments_page(message, state)
    elif text == "⬅️ Выйти в главное меню":
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)
    else:
        await answer_editable(message, "❌ Неизвестная команда. Используйте кнопки ниже.", reply_markup=ReplyKeyboardRemove())

# ------------------------------
# Работа с форматами
# ------------------------------
@admin_router.message(F.text == "🎞 Форматы")
async def choose_format(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '🎞 Форматы'")
    await state.set_state(FormatStates.waiting_format)
    await answer_editable(message, "Выберите формат для конвертации 👇", reply_markup=formats_kb())

@admin_router.message(FormatStates.waiting_format, F.text.in_({"MP3", "MP4", "GIF", "TXT", "PDF → PNG", "PDF → ZIP", "PNG → JPG", "PNG → JPEG"}))
async def format_selected(message: types.Message, state: FSMContext):
    await state.update_data(selected_format=message.text)
    await state.set_state(FormatStates.waiting_file)
    await answer_editable(
        message,
        f"📁 Отправьте файл для конвертации в {message.text} формат.\n\n"
        "Когда закончите — нажмите ⬅️ Назад.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]],
            resize_keyboard=True
        )
    )

@admin_router.message(FormatStates.waiting_file, F.content_type.in_({"document", "video", "audio"}))
async def convert_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fmt = data.get("selected_format")
    file_obj = message.document or message.video or message.audio

    if not file_obj:
        await message.answer("❌ Файл не найден в сообщении.")
        return

    if not fmt:
        await message.answer("⚠️ Сначала выберите формат.")
        return

    # Максимальный размер входящего файла для Telegram: 20 МБ
    MAX_INPUT_SIZE = 20 * 1024 * 1024

    # Проверяем размер входящего файла (супер-админам — без ограничения)
    file_size = getattr(file_obj, "file_size", 0)
    user_id = message.from_user.id
    if file_size > MAX_INPUT_SIZE:
        if await is_super_admin(user_id):
            await message.answer(f"⚠️ Ограничение входящего размера ({MAX_INPUT_SIZE // (1024*1024)} МБ) не применяется к супер-админу. Продолжаю загрузку и конвертацию.")
        else:
            await message.answer(f"🚫 Входящий файл слишком большой ({file_size // (1024*1024)} МБ). Максимум: {MAX_INPUT_SIZE // (1024*1024)} МБ.")
            await state.clear()
            return

    temp_dir = tempfile.gettempdir()

    # Проверяем наличие имени файла и создаем безопасное имя если нужно
    if not hasattr(file_obj, 'file_name') or file_obj.file_name is None:
        file_name = f"file_{user_id}_{int(time.time())}"
    else:
        file_name = file_obj.file_name

    # Дополнительные проверки на None
    if temp_dir is None:
        await message.answer("❌ Ошибка: temp_dir равен None")
        return
    if file_name is None:
        await message.answer("❌ Ошибка: file_name равен None")
        return
    if fmt is None:
        await message.answer("❌ Ошибка: fmt равен None")
        return

    work_dir = tempfile.mkdtemp(prefix=f"converter_admin_{user_id}_")
    file_name = os.path.basename(file_name)
    file_path = os.path.join(work_dir, file_name)
    out_file = os.path.join(work_dir, f"output.{fmt.lower()}")

    try:
        await message.answer("⏳ Скачиваю файл...")

        # Скачиваем файл через бота
        try:
            file_info = await message.bot.get_file(file_obj.file_id)
            await message.bot.download_file(file_info.file_path, destination=file_path)
        except TelegramBadRequest as e:
            # Telegram может отказать в выдаче файла, если он слишком большой для ботов
            msg = str(e)
            if "file is too big" in msg or "too big" in msg.lower():
                await message.answer("❌ Не могу скачать этот файл: Telegram отклонил запрос (файл слишком большой для бота).")
            else:
                await message.answer(f"❌ Ошибка при скачивании файла: {msg}")
            await state.clear()
            return
        except Exception as e:
            await message.answer("❌ Не удалось скачать файл. Попробуйте отправить его ещё раз.")
            await state.clear()
            return

        await message.answer("⏳ Конвертирую файл, это может занять время...")

        # Создаем прогресс-трекер для этой конвертации
        track_id = await progress_tracker.start_conversion_progress(user_id, message, fmt)

        # Создаем callback для обновления прогресса
        from utiles.progress_tracker import ConversionProgressCallback
        progress_callback = ConversionProgressCallback(user_id, progress_tracker)

        # Используем единый конвертер для всех форматов (с мониторингом производительности, retry логикой и прогресс-баром)
        try:
            out_file = await file_converter.convert_file_with_retry(file_path, fmt, user_id=user_id, progress_callback=progress_callback)
            await log_action(user_id, f"Конвертировал файл через converter_service: {fmt}")
        except Exception as e:
            await message.answer("❌ Основной конвертер не справился; пробую запасной способ.")
            # Fallback к старому методу только в крайнем случае
            try:
                await message.answer("⚠️ Пробую альтернативный метод конвертации...")

                if fmt == "MP3":
                    if file_path.lower().endswith((".mp3", ".wav", ".ogg")):
                        await convert_audio_ffmpeg_async(file_path, out_file)
                    else:
                        clip = VideoFileClip(file_path)
                        clip.audio.write_audiofile(out_file, verbose=False, logger=None)
                        clip.close()
                elif fmt == "MP4":
                    await compress_video_ffmpeg_async(file_path, out_file, crf=30, max_width=640, audio_bitrate="64k", preset="fast")
                elif fmt == "GIF":
                    await convert_video_to_gif_ffmpeg_async(file_path, out_file, width=480, fps=12)
                elif fmt == "PNG → JPG" or fmt == "PNG → JPEG":
                    target_ext = "jpg" if fmt == "PNG → JPG" else "jpeg"
                    if temp_dir is None or file_name is None:
                        raise ValueError(f"temp_dir={temp_dir}, file_name={file_name}")
                    out_file = os.path.join(work_dir, f"output.{target_ext}")
                    from PIL import Image
                    img = Image.open(file_path)
                    if img.mode in ("RGBA", "LA", "P"):
                        rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                        if img.mode == "RGBA":
                            rgb_img.paste(img, mask=img.split()[-1])
                        else:
                            rgb_img.paste(img)
                        rgb_img.save(out_file, "JPEG", quality=90)
                    else:
                        img.save(out_file, "JPEG", quality=90)
                elif fmt == "PDF → PNG":
                    if fitz is None:
                        raise ImportError("PyMuPDF не установлен")
                    doc = fitz.open(file_path)
                    if len(doc) > 0:
                        page = doc[0]
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                        out_file = os.path.join(work_dir, "output.png")
                        pix.save(out_file)
                    else:
                        raise Exception("PDF файл пуст")
                elif fmt == "PDF → ZIP":
                    if fitz is None:
                        raise ImportError("PyMuPDF не установлен")
                    doc = fitz.open(file_path)
                    out_file = os.path.join(work_dir, "output.zip")
                    with zipfile.ZipFile(out_file, "w") as zipf:
                        for i, page in enumerate(doc):
                            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                            img_name = f"page_{i + 1}.png"
                            img_path = os.path.join(work_dir, img_name)
                            pix.save(img_path)
                            zipf.write(img_path, img_name)
                            os.remove(img_path)
                elif fmt == "TXT":
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    out_file = os.path.join(work_dir, "output.txt")
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(content)
                else:
                    raise ValueError(f"Неподдерживаемый формат: {fmt}")

                await log_action(user_id, f"Конвертировал файл через fallback метод: {fmt}")

            except Exception as fallback_error:
                await message.answer("❌ Не удалось конвертировать файл. Проверьте формат файла.")
                return

        await message.answer_document(types.FSInputFile(out_file), caption=f"✅ Файл конвертирован в {fmt}")

    except Exception:
        await message.answer("❌ Не удалось обработать файл. Проверьте формат файла и попробуйте снова.")

    finally:
        for path in (file_path, out_file):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        shutil.rmtree(work_dir, ignore_errors=True)

@admin_router.message(F.text == "⬅️ Назад", FormatStates.waiting_file)
@admin_router.message(F.text == "⬅️ Назад", FormatStates.waiting_format)
async def back_from_formats(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id

    # 🔍 Проверяем, админ ли пользователь
    if await is_admin(user_id) or await is_super_admin(user_id):
        kb = await admin_main_kb(user_id)
        text = "↩️ Возврат в главное меню админки."
    else:
        # 💡 Обычному пользователю — его клавиатура
        kb = main_menu_kb()
        text = "↩️ Возврат в главное меню."

    await message.answer(text, reply_markup=kb)

# ------------------------------
# 💉 FIX: Закрытие админки (исправлено)
# ------------------------------
@admin_router.message(F.text == "⬅️ Закрыть админку")
async def close_admin(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '⬅️ Закрыть админку'")
    await state.clear()
    await answer_editable(message, "Админка закрыта ✅", reply_markup=main_menu_kb())

# ==============================
# SUBSCRIPTION MANAGEMENT
# ==============================

@admin_router.message(F.text == "💎 Управление подписками")
async def subscription_management_menu(message: types.Message, state: FSMContext):
    """Меню управления подписками для супер-админа."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Только супер-админ может управлять подписками.")
        return

    await log_action(user_id, "Открыл меню управления подписками")

    kb_builder = ReplyKeyboardBuilder()
    kb_builder.row(
        KeyboardButton(text="📊 Все подписки"),
        KeyboardButton(text="⏳ Ожидающие платежи")
    )
    kb_builder.row(
        KeyboardButton(text="✅ Активировать подписку"),
        KeyboardButton(text="❌ Деактивировать подписку")
    )
    kb_builder.row(
        KeyboardButton(text="⬅️ Назад в админку")
    )

    kb = kb_builder.as_markup(resize_keyboard=True)
    await answer_editable(message, "💎 **Управление подписками**\n\nВыберите действие:", parse_mode="Markdown", reply_markup=kb)

@admin_router.message(F.text == "📊 Все подписки")
async def view_all_subscriptions(message: types.Message, state: FSMContext):
    """Просмотр всех подписок."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Только супер-админ может просматривать подписки.")
        return

    await log_action(user_id, "Просматривает все подписки")

    try:
        subscriptions = await get_all_subscriptions(50)
        if not subscriptions:
            await answer_editable(message, "📭 Подписок пока нет.")
            return

        text = "📊 <b>Все подписки:</b>\n\n"
        for sub in subscriptions:
            username = sub.get('username') or 'Неизвестно'
            first_name = sub.get('first_name') or ''
            plan_type = sub.get('plan_type') or 'free'
            is_active = sub.get('is_active', 0)
            status = "✅ Активна" if is_active else "❌ Неактивна"

            text += f"👤 {escape(str(first_name))} (@{escape(str(username))})\n"
            text += f"📋 Тариф: {escape(str(plan_type))}\n"
            text += f"🔄 Статус: {status}\n\n"

        # Возвращаемся в меню управления подписками
        kb_builder = ReplyKeyboardBuilder()
        kb_builder.row(
            KeyboardButton(text="💎 Управление подписками"),
            KeyboardButton(text="⬅️ Назад в админку")
        )
        kb = kb_builder.as_markup(resize_keyboard=True)

        await answer_editable(message, text, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        await answer_editable(message, f"❌ Ошибка при получении подписок: {escape(str(e))}")

@admin_router.message(F.text == "⏳ Ожидающие платежи")
async def view_pending_payments(message: types.Message, state: FSMContext):
    """Просмотр ожидающих платежей."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Только супер-админ может просматривать платежи.")
        return

    await log_action(user_id, "Просматривает ожидающие платежи")

    try:
        payments = await get_pending_payments(20)
        if not payments:
            await answer_editable(message, "📭 Ожидающих платежей нет.")
            return

        text = "⏳ <b>Ожидающие платежи:</b>\n\n"
        for payment in payments:
            payment_id = payment.get('id')
            username = payment.get('username') or 'Неизвестно'
            first_name = payment.get('first_name') or ''
            amount = payment.get('amount', 0)
            plan_duration = payment.get('plan_duration') or ''
            created_at = payment.get('created_at') or ''

            text += f"🆔 ID: {escape(str(payment_id))}\n"
            text += f"👤 {escape(str(first_name))} (@{escape(str(username))})\n"
            text += f"💰 Сумма: {escape(str(amount))} ₽\n"
            text += f"📅 Период: {escape(str(plan_duration))}\n"
            text += f"🕒 Создан: {escape(str(created_at))}\n\n"

        # Возвращаемся в меню управления подписками
        kb_builder = ReplyKeyboardBuilder()
        kb_builder.row(
            KeyboardButton(text="💎 Управление подписками"),
            KeyboardButton(text="⬅️ Назад в админку")
        )
        kb = kb_builder.as_markup(resize_keyboard=True)

        await answer_editable(message, text, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        await answer_editable(message, f"❌ Ошибка при получении платежей: {escape(str(e))}")

@admin_router.message(F.text == "✅ Активировать подписку")
async def activate_subscription_start(message: types.Message, state: FSMContext):
    """Начало активации подписки."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Только супер-админ может активировать подписки.")
        return

    await state.set_state(AdminStates.activate_premium_wait_id)
    await answer_editable(message, "Введите ID пользователя для активации премиум подписки:")

@admin_router.message(F.text == "❌ Деактивировать подписку")
async def deactivate_subscription_start(message: types.Message, state: FSMContext):
    """Начало деактивации подписки."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Только супер-админ может деактивировать подписки.")
        return

    await state.set_state(AdminStates.deactivate_subscription_wait_id)
    await answer_editable(message, "Введите ID пользователя для деактивации подписки:")

async def activate_subscription_confirm(message: types.Message, state: FSMContext):
    """Подтверждение активации подписки."""
    user_id = message.from_user.id
    try:
        target_user_id = int(message.text)

        # Запрашиваем длительность подписки
        await answer_editable(message,
            f"Пользователь: {target_user_id}\n\n"
            "Выберите длительность подписки:\n"
            "1 - 1 месяц\n"
            "3 - 3 месяца\n"
            "12 - 1 год\n\n"
            "Введите номер (1, 3 или 12):")

        # Сохраняем ID пользователя для следующего шага
        await state.update_data(target_user_id=target_user_id, action='activate')

    except ValueError:
        await answer_editable(message, "❌ Неверный ID, введите числовой ID.")
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(user_id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

async def deactivate_subscription_confirm(message: types.Message, state: FSMContext):
    """Подтверждение деактивации подписки."""
    user_id = message.from_user.id
    try:
        target_user_id = int(message.text)

        # Сразу деактивируем подписку
        await deactivate_subscription(target_user_id)
        await log_action(user_id, f"Деактивировал подписку пользователя {target_user_id}")
        await answer_editable(message, f"✅ Подписка пользователя {target_user_id} деактивирована.")

    except ValueError:
        await answer_editable(message, "❌ Неверный ID, введите числовой ID.")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(user_id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

# Обработчик для ввода длительности подписки
async def subscription_duration_handler(message: types.Message, state: FSMContext):
    """Обработка выбора длительности подписки."""
    user_id = message.from_user.id
    data = await state.get_data()

    if data.get('action') == 'activate' and 'target_user_id' in data:
        target_user_id = data['target_user_id']
        duration = int(message.text)

        # Активируем подписку
        await activate_premium_subscription(target_user_id, duration, f"Активировано админом {user_id}")
        await log_action(user_id, f"Активировал премиум подписку для пользователя {target_user_id} на {duration} месяцев")

        await answer_editable(message, f"✅ Премиум подписка активирована для пользователя {target_user_id} на {duration} месяцев.")

        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(user_id)
        await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

@admin_router.message(F.text == "📊 Статистика производительности")
async def performance_stats_handler(message: types.Message, state: FSMContext):
    """Показывает статистику производительности бота."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Только супер-админ может просматривать статистику производительности.")
        return

    await log_action(user_id, "Просматривает статистику производительности")

    try:
        # Получаем статистику
        stats = get_conversion_stats()
        popular_formats = get_popular_formats(limit=5)

        # Формируем текст статистики
        stats_text = f"""📊 **Статистика производительности**

🔢 **Общая статистика:**
• Всего конвертаций: {stats['total_conversions']}
• Неудачных конвертаций: {stats['failed_conversions']}
• Среднее время: {stats['average_time']:.2f} сек
• Процент успеха: {((stats['total_conversions'] - stats['failed_conversions']) / max(stats['total_conversions'], 1) * 100):.1f}%

🏆 **Топ-5 популярных форматов:**"""

        if popular_formats:
            for i, (format_name, data) in enumerate(popular_formats, 1):
                avg_time = data['total_time'] / data['count'] if data['count'] > 0 else 0
                stats_text += f"\n{i}. **{format_name}**: {data['count']} раз (ср. {avg_time:.1f}с)"
        else:
            stats_text += "\n📭 Пока нет данных о конвертациях"

        # Добавляем детализацию по форматам
        if stats['formats_used']:
            stats_text += "\n\n📋 **Детализация по форматам:**"
            for format_name, data in sorted(stats['formats_used'].items(), key=lambda x: x[1]['count'], reverse=True):
                avg_time = data['total_time'] / data['count'] if data['count'] > 0 else 0
                stats_text += f"\n• {format_name}: {data['count']} конвертаций (ср. {avg_time:.1f}с)"

        # Возвращаемся в главное меню админки
        kb_builder = ReplyKeyboardBuilder()
        kb_builder.row(
            KeyboardButton(text="📊 Статистика производительности"),
            KeyboardButton(text="⬅️ Назад в админку")
        )
        kb = kb_builder.as_markup(resize_keyboard=True)

        await answer_editable(message, stats_text, parse_mode="Markdown", reply_markup=kb)

    except Exception as e:
        await answer_editable(message, f"❌ Ошибка при получении статистики: {str(e)}")

@admin_router.message(F.text == "⬅️ Назад в админку")
async def back_to_admin_menu(message: types.Message, state: FSMContext):
    """Возврат в главное меню админки."""
    await state.set_state(AdminStates.main)
    kb = await admin_main_kb(message.from_user.id)
    await answer_editable(message, "Возврат в главное меню админки 👇", reply_markup=kb)

