from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

PAGE_SIZE = 20

def pagination_keyboard(user_id: int, page: int, max_page: int):
    kb = InlineKeyboardMarkup(row_width=2)
    
    if page > 1:
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data=f"logpage:{user_id}:{page-1}"))
    if page < max_page:
        kb.add(InlineKeyboardButton("➡️ Вперёд", callback_data=f"logpage:{user_id}:{page+1}"))
    
    kb.add(InlineKeyboardButton("⬅️ Выйти из логов", callback_data="exit_logs"))
    return kb
