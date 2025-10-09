from aiogram import Router, types
from data_base import db
from kbds.admin_reply import (
    admin_kb,
    super_admin_kb,
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

    # Скрываем кнопки пользователя
    await cb.message.edit_reply_markup(None)

    # Проверяем права
    if await db.is_super_admin(user_id):
        kb = super_admin_kb()  # полный функционал
        text = "Добро пожаловать, супер-админ!"
    else:
        kb = admin_kb()         # обычный админ
        text = "Добро пожаловать в админку!"

    # Отправляем сообщение с админскими кнопками
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


# ==========================
# Выход из админки
# ==========================
@admin_router.callback_query(lambda c: c.data in ["exit_admin", "exit_super_admin"])
async def exit_admin(cb: types.CallbackQuery):
    # Удаляем сообщение с админскими кнопками
    await cb.message.delete()
    # Восстанавливаем главное меню пользователя
    await cb.message.answer("Главное меню 👇", reply_markup=main_menu_kb())
    await cb.answer()


# ==========================
# Просмотр пользователей для логов
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
        return

    kb = users_keyboard(users)
    await cb.message.answer("Выберите пользователя для просмотра логов:", reply_markup=kb)
    await cb.answer()


# ==========================
# Показ логов конкретного пользователя (первая страница)
# ==========================
@admin_router.callback_query(lambda c: c.data.startswith("showlog:"))
async def show_user_logs(cb: types.CallbackQuery):
    user_id = int(cb.data.split(":")[1])
    await send_log_page(cb.message, user_id, page=1)


# ==========================
# Пагинация логов
# ==========================
@admin_router.callback_query(lambda c: c.data.startswith("logs_page_"))
async def paginate_logs(cb: types.CallbackQuery):
    page = int(cb.data.split("_")[-1])
    # user_id берём из текста сообщения: первая строка "Действия пользователя {user_id}"
    first_line = cb.message.text.splitlines()[0]
    user_id = int(first_line.split()[2])
    await send_log_page(cb.message, user_id, page)


# ==========================
# Выход из просмотра логов
# ==========================
@admin_router.callback_query(lambda c: c.data == "exit_logs")
async def exit_logs(cb: types.CallbackQuery):
    await cb.message.delete()
    await cb.answer("Вы вышли из просмотра логов.")


# ==========================
# Функция отправки страницы логов
# ==========================
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
