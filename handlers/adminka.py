from aiogram import Router, types
from data_base import db
from kbds.admin_reply import (
    admin_kb,
    super_admin_kb,
    users_for_admin_kb,
    users_keyboard,
    logs_pagination_kb,
    PAGE_SIZE,
    main_menu_kb
)

admin_router = Router()

# ==========================
# Вход в админку
# ==========================
@admin_router.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    await cb.message.edit_reply_markup(None)

    if await db.is_super_admin(user_id):
        kb = super_admin_kb()
        text = "Добро пожаловать, супер-админ!"
    elif await db.is_admin(user_id):
        kb = admin_kb()
        text = "Добро пожаловать в админку!"
    else:
        await cb.answer("У вас нет доступа к админке.", show_alert=True)
        return

    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()

# ==========================
# Добавление админа (с динамическим списком пользователей)
# ==========================
@admin_router.callback_query(lambda c: c.data == "add_admin")
async def add_admin_panel(cb: types.CallbackQuery):
    if not await db.is_super_admin(cb.from_user.id):
        await cb.answer("У вас нет доступа.", show_alert=True)
        return

    await cb.message.edit_reply_markup(None)

    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute(
            "SELECT user_id, username, first_name, last_name, is_admin FROM users ORDER BY user_id"
        ) as cursor:
            users = await cursor.fetchall()
            users = [
                {
                    "id": u["user_id"],
                    "username": u["username"],
                    "first_name": u["first_name"],
                    "last_name": u["last_name"],
                    "is_admin": bool(u["is_admin"])
                }
                for u in users
            ]

    kb = users_for_admin_kb(users)
    await cb.message.answer(
        "Введите Telegram ID нового админа:\n\nСписок пользователей:",
        reply_markup=kb
    )
    await cb.answer()

# ==========================
# Переключение прав админа (➕ / ➖)
# ==========================
@admin_router.callback_query(lambda c: c.data.startswith("toggle_admin:"))
async def toggle_admin_callback(cb: types.CallbackQuery):
    user_id = int(cb.data.split(":")[1])

    if await db.is_admin(user_id):
        await db.remove_admin(user_id)
        status_text = "сняты права администратора"
    else:
        await db.add_admin(user_id)
        status_text = "даны права администратора"

    await cb.answer(f"Пользователю {user_id} {status_text}", show_alert=True)

    # Обновляем список пользователей
    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute(
            "SELECT user_id, username, first_name, last_name, is_admin FROM users ORDER BY user_id"
        ) as cursor:
            users = await cursor.fetchall()
            users = [
                {
                    "id": u["user_id"],
                    "username": u["username"],
                    "first_name": u["first_name"],
                    "last_name": u["last_name"],
                    "is_admin": bool(u["is_admin"])
                }
                for u in users
            ]

    kb = users_for_admin_kb(users)
    await cb.message.edit_text(
        "Введите Telegram ID нового админа:\n\nСписок пользователей:",
        reply_markup=kb
    )

# ==========================
# Кнопка "Отмена" возвращает супер-админ меню
# ==========================
@admin_router.callback_query(lambda c: c.data == "cancel_add_admin")
async def cancel_add_admin(cb: types.CallbackQuery):
    if not await db.is_super_admin(cb.from_user.id):
        await cb.answer("У вас нет доступа.", show_alert=True)
        return

    await cb.message.delete()
    kb = super_admin_kb()
    await cb.message.answer("Супер-админ: главное меню 👇", reply_markup=kb)
    await cb.answer()

# ==========================
# Закрытие админки
# ==========================
@admin_router.callback_query(lambda c: c.data == "close_admin")
async def close_admin(cb: types.CallbackQuery):
    await cb.message.delete()
    await cb.message.answer("Главное меню 👇", reply_markup=main_menu_kb())
    await cb.answer()

# ==========================
# Просмотр логов, пагинация и exit_logs
# ==========================
@admin_router.callback_query(lambda c: c.data in ["view_logs", "view_all_logs"])
async def view_users_logs(cb: types.CallbackQuery):
    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute("SELECT user_id, username FROM users ORDER BY user_id") as cursor:
            users = await cursor.fetchall()
            users = [dict(u) for u in users]

    if not users:
        await cb.message.answer("Пользователей нет.")
        await cb.answer()
        return

    kb = users_keyboard(users)
    await cb.message.answer("Выберите пользователя для просмотра логов:", reply_markup=kb)
    await cb.answer()

@admin_router.callback_query(lambda c: c.data.startswith("showlog:"))
async def show_user_logs(cb: types.CallbackQuery):
    user_id = int(cb.data.split(":")[1])
    await send_log_page(cb.message, user_id, page=1)
    await cb.answer()

@admin_router.callback_query(lambda c: c.data.startswith("logs_page_"))
async def paginate_logs(cb: types.CallbackQuery):
    try:
        page = int(cb.data.split("_")[-1])
        first_line = cb.message.text.splitlines()[0]
        user_id = int(first_line.split()[2])
        await send_log_page(cb.message, user_id, page)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)

@admin_router.callback_query(lambda c: c.data == "exit_logs")
async def exit_logs(cb: types.CallbackQuery):
    await cb.message.delete()
    await cb.answer("Вы вышли из просмотра логов.")

async def send_log_page(message: types.Message, user_id: int, page: int):
    logs = await db.get_user_logs(limit=1000)
    logs = [log for log in logs if log["user_id"] == user_id]

    if not logs:
        await message.edit_text("Действий пользователя нет.")
        return

    total_pages = (len(logs) - 1) // PAGE_SIZE + 1
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_logs = logs[start:end]

    text = f"Действия пользователя {user_id} — страница {page}/{total_pages}:\n\n"
    for log in page_logs:
        text += f"{log['timestamp']}: {log['action']}\n"

    kb = logs_pagination_kb(page, total_pages)
    await message.edit_text(text, reply_markup=kb)
