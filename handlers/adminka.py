from aiogram import Router, types
from aiogram.filters import Command
from data_base.db import is_admin, is_super_admin, log_action, get_user_logs, add_admin, remove_admin
from kbds.admin_reply import admin_kb, super_admin_kb

admin_router = Router()

# ==============================
# Вход в админ-панель
# ==============================
@admin_router.message(Command("admin"))
async def admin_panel(message: types.Message):
    user_id = message.from_user.id

    if await is_super_admin(user_id):
        await message.answer(
            "Привет, супер-админ! Выберите действие 👇",
            reply_markup=super_admin_kb
        )
        await log_action(user_id, "Открыл панель супер-админа")
    elif await is_admin(user_id):
        await message.answer(
            "Привет, админ! Выберите действие 👇",
            reply_markup=admin_kb
        )
        await log_action(user_id, "Открыл панель админа")
    else:
        await message.answer("У вас нет прав для доступа к админ-панели")
        await log_action(user_id, "Попытка доступа к админ-панели без прав")

# ==============================
# Обработчик кнопок админа и супер-админа
# ==============================
@admin_router.message(lambda message: message.text in ["📜 Просмотр логов", "➕ Добавить админа", "➖ Удалить админа", "⬅️ Назад"])
async def admin_actions(message: types.Message):
    user_id = message.from_user.id
    text = message.text

    # Просмотр логов
    if text == "📜 Просмотр логов":
        logs = await get_user_logs(limit=10)
        if logs:
            log_text = "\n".join([f"{log['timestamp']} — User {log['user_id']}: {log['action']}" for log in logs])
        else:
            log_text = "Логи пусты"
        await message.answer(log_text)
        await log_action(user_id, "Просмотрел логи")

    # Добавить админа (только супер-админ)
    elif text == "➕ Добавить админа":
        if await is_super_admin(user_id):
            await message.answer("Введите ID пользователя для добавления админом:")
            await log_action(user_id, "Начал добавление нового админа")
        else:
            await message.answer("Только супер-админ может добавлять админов")
            await log_action(user_id, "Попытка добавить админа без прав")

    # Удалить админа (только супер-админ)
    elif text == "➖ Удалить админа":
        if await is_super_admin(user_id):
            await message.answer("Введите ID пользователя для удаления из админов:")
            await log_action(user_id, "Начал удаление админа")
        else:
            await message.answer("Только супер-админ может удалять админов")
            await log_action(user_id, "Попытка удалить админа без прав")

    # Назад
    elif text == "⬅️ Назад":
        await message.answer("Вы вернулись в главное меню 👇")
        await log_action(user_id, "Нажал Назад в админ-панели")
