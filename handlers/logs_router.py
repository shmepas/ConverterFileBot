from aiogram import Router, types
from data_base import db
from kbds.logs_kbd import pagination_keyboard, PAGE_SIZE

logs_router = Router()

# Старт просмотра логов конкретного пользователя
@logs_router.callback_query(lambda c: c.data.startswith("showlog:"))
async def show_user_logs(cb: types.CallbackQuery):
    user_id = int(cb.data.split(":")[1])
    await send_log_page(cb.message, user_id, page=1)

# Обработка пагинации
@logs_router.callback_query(lambda c: c.data.startswith("logpage:"))
async def paginate_logs(cb: types.CallbackQuery):
    _, user_id_str, page_str = cb.data.split(":")
    user_id = int(user_id_str)
    page = int(page_str)
    await send_log_page(cb.message, user_id, page)

# Функция отправки страницы логов
async def send_log_page(message: types.Message, user_id: int, page: int):
    logs = await db.get_user_logs(limit=1000)  # Получаем все логи (можно оптимизировать)
    logs = [log for log in logs if log["user_id"] == user_id]
    
    if not logs:
        await message.edit_text("Действий пользователя нет.")
        return

    # Пагинация
    max_page = (len(logs) - 1) // PAGE_SIZE + 1
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_logs = logs[start:end]

    text = f"Действия пользователя {user_id} — страница {page}/{max_page}:\n\n"
    for log in page_logs:
        text += f"{log['timestamp']}: {log['action']}\n"

    kb = pagination_keyboard(user_id, page, max_page)
    await message.edit_text(text, reply_markup=kb)
