from aiogram import Router, types, F
from data_base import db
from kbds.logs_kbd import pagination_keyboard, PAGE_SIZE
from aiogram.filters import BaseFilter
from html import escape

logs_router = Router()

class _AdminOnly(BaseFilter):
    async def __call__(self, event: types.CallbackQuery) -> bool:
        return event.message is not None and event.message.chat.type == "private" and await db.has_admin_access(event.from_user.id)

logs_router.callback_query.filter(_AdminOnly())

@logs_router.callback_query(F.data.startswith("showlog:"))
async def show_user_logs(cb: types.CallbackQuery):
    try:
        user_id = int(cb.data.split(":", 1)[1])
        await send_log_page(cb.message, user_id, page=1)
    except (ValueError, IndexError):
        await cb.answer("Некорректный запрос", show_alert=True)
        return
    await cb.answer()

@logs_router.callback_query(F.data.startswith("logpage:"))
async def paginate_logs(cb: types.CallbackQuery):
    try:
        _, user_id_str, page_str = cb.data.split(":")
        user_id, page = int(user_id_str), int(page_str)
        if user_id < 0 or page < 1:
            raise ValueError
    except (ValueError, AttributeError):
        await cb.answer("Некорректный запрос", show_alert=True)
        return
    await send_log_page(cb.message, user_id, page)
    await cb.answer()

@logs_router.callback_query(F.data == "exit_logs")
async def exit_logs(cb: types.CallbackQuery):
    await cb.message.delete_reply_markup()
    await cb.answer()

async def send_log_page(message: types.Message, user_id: int, page: int):
    logs = await db.get_user_logs(limit=1000)
    logs = [log for log in logs if log["user_id"] == user_id]

    if not logs:
        await message.edit_text("Действий пользователя нет.")
        return

    max_page = (len(logs) - 1) // PAGE_SIZE + 1
    page = max(1, min(page, max_page))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_logs = logs[start:end]

    text = f"Действия пользователя {user_id} — страница {page}/{max_page}:\n\n"
    for log in page_logs:
        text += f"{escape(str(log['timestamp']))}: {escape(str(log['action']))}\n"

    kb = pagination_keyboard(user_id, page, max_page)
    await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
