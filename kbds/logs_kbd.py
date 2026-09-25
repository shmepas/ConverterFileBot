from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

PAGE_SIZE = 20

def pagination_keyboard(user_id: int, page: int, max_page: int):
    kb = InlineKeyboardBuilder()

    if page > 1:
        kb.button(text="⬅️ Назад", callback_data=f"logpage:{user_id}:{page-1}")
    if page < max_page:
        kb.button(text="➡️ Вперёд", callback_data=f"logpage:{user_id}:{page+1}")

    kb.button(text="⬅️ Выйти из логов", callback_data="exit_logs")
    return kb.adjust(2).as_markup()
