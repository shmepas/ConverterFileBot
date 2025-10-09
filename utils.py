from aiogram import types
from kbds import reply
from kbds.admin_reply import admin_kb, super_admin_kb
from data_base.db import is_admin, is_super_admin
from aiogram.utils.keyboard import ReplyKeyboardBuilder

async def get_keyboard_by_role(user_id: int) -> types.ReplyKeyboardMarkup:
    """
    Возвращает клавиатуру в зависимости от роли:
    - обычный пользователь: базовая клавиатура
    - админ: базовая + кнопка "Админка"
    - супер-админ: базовая + кнопка "Админка"
    """
    kb = ReplyKeyboardBuilder()
    kb.attach(reply.start_kb3)  # базовая клавиатура для всех

    # для админов и супер-админов добавляем кнопку "Админка"
    if await is_admin(user_id) or await is_super_admin(user_id):
        kb.row(types.KeyboardButton(text="Админка"))

    return kb
