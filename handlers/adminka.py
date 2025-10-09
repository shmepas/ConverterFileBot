from aiogram import Router, types
from data_base import db
from kbds.admin_reply import admin_kb, super_admin_kb, users_keyboard, logs_pagination_kb, PAGE_SIZE

admin_router = Router()

# === Вход в админку с проверкой прав ===
@admin_router.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel(cb: types.CallbackQuery):
    user_id = cb.from_user.id

    if await db.is_super_admin(user_id):
        kb = super_admin_kb()
        await cb.message.answer("Добро пожаловать, супер-админ!", reply_markup=kb)
    else:
        kb = admin_kb()
        await cb.message.answer("Добро пожаловать в админку!", reply_markup=kb)

    # Скрываем главное меню
    await cb.message.edit_reply_markup(None)
    await cb.answer()


# === Выход из админки ===
@admin_router.callback_query(lambda c: c.data == "exit_admin" or c.data == "exit_super_admin")
async def exit_admin(cb: types.CallbackQuery):
    await cb.message.delete()
    from kbds.admin_reply import main_menu_kb
    await cb.message.answer("Главное меню 👇", reply_markup=main_menu_kb())
    await cb.answer()


# === Просмотр пользователей для логов ===
@admin_router.callback_query(lambda c: c.data == "view_logs" or c.data == "view_all_logs")
async def view_users_logs(cb: types.CallbackQuery):
    # Получаем список пользователей
    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute("SELECT user_id, username FROM users ORDER BY user_id") as cursor:
            users = await cursor.fetchall()
            users = [dict(u) for u in users]

    if not users:
        await cb.message.answer("Пользователей нет.")
        return

    kb = users_keyboard(users)
    await cb.message.answer("Выберите пользователя для просмотра логов:", reply_markup=kb)
    await cb.answer()


# === Показ логов конкретного пользователя (первая страница) ===
@admin_router.callback_query(lambda c: c.data.startswith("showlog:"))
async def show_user_logs(cb: types.CallbackQuery):
    user_id = int(cb.data.split(":")[1])
    await send_log_page(cb.message, user_id, page=1)


# === Пагинация логов ===
@admin_router.callback_query(lambda c: c.data.startswith("logs_page_"))
async def paginate_logs(cb: types.CallbackQuery):
    page = int(cb.data.split("_")[-1])
    # user_id берём из текста сообщения: предполагаем, что первая строка вида "Действия пользователя {user_id}"
    first_line = cb.message.text.splitlines()[0]
    user_id = int(first_line.split()[2])
    await send_log_page(cb.message, user_id, page)


# === Выход из просмотра логов ===
@admin_router.callback_query(lambda c: c.data == "exit_logs")
async def exit_logs(cb: types.CallbackQuery):
    await cb.message.delete()
    await cb.answer("Вы вышли из просмотра логов.")


# === Функция отправки страницы логов ===
async def send_log_page(message: types.Message, user_id: int, page: int):
    logs = await db.get_user_logs(limit=1000)  # берём все логи
    logs = [log for log in logs if log["user_id"] == user_id]

    if not logs:
        await message.edit_text("Действий пользователя нет.")
        return

    # Пагинация
    total_pages = (len(logs) - 1) // PAGE_SIZE + 1
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_logs = logs[start:end]

    text = f"Действия пользователя {user_id} — страница {page}/{total_pages}:\n\n"
    for log in page_logs:
        text += f"{log['timestamp']}: {log['action']}\n"

    kb = logs_pagination_kb(page, total_pages)
    await message.edit_text(text, reply_markup=kb)
