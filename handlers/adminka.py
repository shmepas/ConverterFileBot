from aiogram import F, Router, types
from aiogram.filters import Command
from kbds import admin_reply
from data_base.db import is_admin, is_super_admin, add_admin, remove_admin, log_action, get_logs

admin_router = Router()

# 🔱 ID суперадмина
SUPER_ADMIN_ID = 6462907077  # замени на свой Telegram ID


# === Вход в админ-панель ===
@admin_router.message(Command("admin"))
async def open_admin_panel(message: types.Message):
    user_id = message.from_user.id

    if user_id == SUPER_ADMIN_ID or await is_super_admin(user_id):
        await message.answer(
            "🔱 Панель супер-админа:",
            reply_markup=admin_reply.super_admin_kb
        )
    elif await is_admin(user_id):
        await message.answer(
            "🛠 Панель администратора:",
            reply_markup=admin_reply.admin_kb
        )
    else:
        await message.answer("⛔ У тебя нет прав доступа.")


# === Добавить админа ===
@admin_router.message(F.text.startswith("➕ Добавить админа"))
async def add_admin_cmd(message: types.Message):
    user_id = message.from_user.id
    if user_id != SUPER_ADMIN_ID and not await is_super_admin(user_id):
        await message.answer("🚫 Только супер-админ может добавлять админов.")
        return

    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("❗ Пример: ➕ Добавить админа 123456789")
        return

    try:
        new_admin_id = int(parts[-1])
        await add_admin(new_admin_id)
        await log_action(user_id, f"Добавил админа {new_admin_id}")
        await message.answer(f"✅ Пользователь {new_admin_id} теперь админ!")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")


# === Удалить админа ===
@admin_router.message(F.text.startswith("➖ Удалить админа"))
async def remove_admin_cmd(message: types.Message):
    user_id = message.from_user.id
    if user_id != SUPER_ADMIN_ID and not await is_super_admin(user_id):
        await message.answer("🚫 Только супер-админ может удалять админов.")
        return

    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("❗ Пример: ➖ Удалить админа 123456789")
        return

    try:
        admin_id = int(parts[-1])
        await remove_admin(admin_id)
        await log_action(user_id, f"Удалил админа {admin_id}")
        await message.answer(f"✅ Админ {admin_id} удалён.")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")


# === Просмотр логов ===
@admin_router.message(F.text == "📜 Просмотр логов")
async def show_logs(message: types.Message):
    user_id = message.from_user.id
    if user_id != SUPER_ADMIN_ID and not await is_admin(user_id):
        await message.answer("🚫 Нет доступа.")
        return

    logs = await get_logs()
    if not logs:
        await message.answer("📭 Логи пока пусты.")
        return

    text = "\n\n".join(
        [f"👤 {log['user_id']} — {log['action']} ({log['timestamp']})" for log in logs]
    )
    await message.answer(f"📋 Последние действия:\n\n{text}")


# === Назад ===
@admin_router.message(F.text == "⬅️ Назад")
async def back_to_admin_panel(message: types.Message):
    user_id = message.from_user.id
    if user_id == SUPER_ADMIN_ID or await is_super_admin(user_id):
        kb = admin_reply.super_admin_kb
    else:
        kb = admin_reply.admin_kb

    await message.answer("🔙 Возврат в админ-панель.", reply_markup=kb)
