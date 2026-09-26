import os
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
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
import aiosqlite
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from utils import FFMPEG_BINARY, compress_video_ffmpeg_async, convert_video_to_gif_ffmpeg_async, answer_editable
from converter_service import file_converter
from utiles.progress_tracker import progress_tracker
from utiles.logging_config import latest_error
from utiles.conversion_runner import conversion_runner
from data_base import db
from data_base.backup import BACKUP_DIR

from data_base.db import (
    log_action, is_admin, is_super_admin, has_admin_access,
    add_admin, remove_admin, get_user_logs,
    get_all_subscriptions, get_pending_payments, mark_payment_completed,
    get_all_payments,
    activate_premium_subscription, deactivate_subscription,
    get_conversion_performance_stats, get_open_support_ticket_count,
)

admin_router = Router()
PAGE_SIZE = 20
logger = logging.getLogger(__name__)


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

# ------------------------------
# Главная клавиатура админа
# ------------------------------
async def admin_main_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    kb_builder = ReplyKeyboardBuilder()
    open_ticket_count = await get_open_support_ticket_count()
    tickets_label = f"🎫 Тикеты ({open_ticket_count})" if open_ticket_count else "🎫 Тикеты"
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
            KeyboardButton(text=tickets_label)
        )
        kb_builder.row(
            KeyboardButton(text="📊 Статистика производительности"),
            KeyboardButton(text="🩺 Состояние бота")
        )
        kb_builder.row(KeyboardButton(text="⬅️ Закрыть админку"))
    elif await is_admin(user_id):
        # Для обычного админа - просмотр подписок без управления
        kb_builder.row(
            KeyboardButton(text="📜 Просмотр логов"),
            KeyboardButton(text="🧾 Все платежи")
        )
        kb_builder.row(
            KeyboardButton(text="💎 Просмотр подписок"),
            KeyboardButton(text="🎞 Форматы"),
            KeyboardButton(text=tickets_label)
        )
        kb_builder.row(KeyboardButton(text="⬅️ Закрыть админку"))
    return kb_builder.as_markup(resize_keyboard=True)


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if amount < 1024 or unit == "ТБ":
            return f"{amount:.1f} {unit}"
        amount /= 1024


async def _build_bot_health_report() -> str:
    db_path = Path(db.DB_PATH)
    db_status = "недоступна"
    users_count = open_tickets = waiting_tickets = fsm_sessions = schema_version = 0
    conversion_stats = {"successful_conversions": 0, "failed_conversions": 0}
    try:
        async with aiosqlite.connect(db.DB_PATH, timeout=5) as connection:
            async with connection.execute("PRAGMA quick_check") as cursor:
                result = await cursor.fetchone()
            db_status = "OK" if result and result[0] == "ok" else "ошибка целостности"
            async with connection.execute("SELECT COUNT(*) FROM users") as cursor:
                users_count = (await cursor.fetchone())[0]
            async with connection.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE status = 'open'"
            ) as cursor:
                open_tickets = (await cursor.fetchone())[0]
            async with connection.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE status = 'waiting_user'"
            ) as cursor:
                waiting_tickets = (await cursor.fetchone())[0]
            async with connection.execute(
                "SELECT COUNT(*) FROM fsm_sessions WHERE state IS NOT NULL"
            ) as cursor:
                fsm_sessions = (await cursor.fetchone())[0]
            async with connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ) as cursor:
                schema_version = (await cursor.fetchone())[0]
            since = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            async with connection.execute(
                "SELECT SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) "
                "FROM conversion_history WHERE created_at >= ?",
                (since,),
            ) as cursor:
                counts = await cursor.fetchone()
            conversion_stats = {
                "successful_conversions": counts[0] or 0,
                "failed_conversions": counts[1] or 0,
            }
    except Exception:
        logger.exception("Admin health report could not query the database")

    try:
        free_space = shutil.disk_usage(Path(__file__).resolve().parents[1]).free
        disk_text = _format_bytes(free_space)
    except OSError:
        disk_text = "не удалось проверить"

    backups = sorted(
        BACKUP_DIR.glob("bot_database_*.sqlite3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if BACKUP_DIR.is_dir() else []
    if backups:
        backup_time = datetime.fromtimestamp(backups[0].stat().st_mtime).strftime("%d.%m %H:%M")
        backup_text = f"{backup_time} · {_format_bytes(backups[0].stat().st_size)}"
    else:
        backup_text = "нет резервной копии"

    last_error = latest_error()
    last_error_text = (
        f"{last_error[0].strftime('%d.%m %H:%M:%S')} · {escape(last_error[1])}"
        if last_error else "ошибок с момента запуска не зарегистрировано"
    )
    db_size = _format_bytes(db_path.stat().st_size) if db_path.is_file() else "файл отсутствует"
    return (
        "🩺 <b>Состояние бота</b>\n\n"
        f"База: <b>{db_status}</b> · {db_size}\n"
        f"Схема БД: версия {schema_version} · пользователей: {users_count}\n"
        f"Тикеты: ожидают админа — {open_tickets}, ожидают пользователя — {waiting_tickets}\n"
        f"Конвертации за 24 ч: успешно — {conversion_stats['successful_conversions']}, "
        f"ошибки — {conversion_stats['failed_conversions']}\n"
        f"Очередь конвертации: выполняется — {conversion_runner.running_count}, "
        f"в очереди — {conversion_runner.queued_count}\n"
        f"Активные диалоги: {fsm_sessions} · свободно на диске: {disk_text}\n"
        f"Последний бэкап: {backup_text}\n"
        f"Последняя ошибка: {last_error_text}"
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

@admin_router.message(F.text == "🩺 Состояние бота")
async def bot_health_handler(message: types.Message, state: FSMContext):
    """Show a compact operational snapshot to the super-admin."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Статус бота доступен только супер-админу.")
        return
    await log_action(user_id, "Просматривает состояние бота")
    try:
        report = await _build_bot_health_report()
    except Exception:
        logger.exception("Failed to build admin health report")
        report = "Не удалось получить часть диагностических данных. Подробности записаны в журнал."
    await answer_editable(
        message, report, parse_mode="HTML", reply_markup=await admin_main_kb(user_id)
    )


@admin_router.message(F.text == "📊 Статистика производительности")
async def performance_stats_handler(message: types.Message, state: FSMContext):
    """Показывает статистику производительности бота."""
    user_id = message.from_user.id
    if not await is_super_admin(user_id):
        await answer_editable(message, "❌ Только супер-админ может просматривать статистику производительности.")
        return

    await log_action(user_id, "Просматривает статистику производительности")

    try:
        # История хранится в SQLite и доступна из отдельного процесса конвертации.
        stats = await get_conversion_performance_stats(days=90)
        popular_formats = sorted(
            stats["formats_used"].items(),
            key=lambda item: (item[1]["successful_count"], item[1]["count"]),
            reverse=True,
        )[:5]

        # Формируем текст статистики
        stats_text = f"""📊 **Статистика производительности за последние 90 дней**

🔢 **Общая статистика:**
• Всего конвертаций: {stats['total_conversions']}
• Неудачных конвертаций: {stats['failed_conversions']}
• Среднее время: {stats['average_time']:.2f} сек
• Процент успеха: {((stats['total_conversions'] - stats['failed_conversions']) / max(stats['total_conversions'], 1) * 100):.1f}%

🏆 **Топ-5 популярных форматов:**"""

        if popular_formats:
            for i, (format_name, data) in enumerate(popular_formats, 1):
                successful_count = data['successful_count']
                avg_time = data['total_time'] / successful_count if successful_count else 0
                stats_text += f"\n{i}. **{format_name}**: {data['count']} раз (ср. {avg_time:.1f}с)"
        else:
            stats_text += "\n📭 Пока нет данных о конвертациях за последние 90 дней"

        # Добавляем детализацию по форматам
        if stats['formats_used']:
            stats_text += "\n\n📋 **Детализация по форматам:**"
            for format_name, data in sorted(
                stats['formats_used'].items(),
                key=lambda item: (item[1]['successful_count'], item[1]['count']),
                reverse=True,
            ):
                avg_time = data['total_time'] / data['successful_count'] if data['successful_count'] else 0
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

