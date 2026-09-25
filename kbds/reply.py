from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from data_base.db import is_admin


# ==============================
# Удаление клавиатуры
# ==============================
del_kb = ReplyKeyboardRemove()

# ==============================
# Главное меню
# ==============================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📘 Меню"), KeyboardButton(text="ℹ️ О боте")],
            [KeyboardButton(text="💳 Вариант оплаты"), KeyboardButton(text="💳 Платежи")],
            [KeyboardButton(text="🎞 Форматы"), KeyboardButton(text="📎 Отправить файл")],
            [KeyboardButton(text="🎫 Создать тикет"), KeyboardButton(text="📨 Мои тикеты")]
        ],
        resize_keyboard=True
    )
    return kb

# ==============================
# Меню отправки файла
# ==============================
def file_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📎 Отправить файл")],
            [KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )

# ==============================
# Выбор формата конвертации
# ==============================
def format_choice_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="MP3"), KeyboardButton(text="MP4"), KeyboardButton(text="GIF")],
            [KeyboardButton(text="TXT"), KeyboardButton(text="PDF → PNG"), KeyboardButton(text="PDF → ZIP")],
            [KeyboardButton(text="PNG → JPG"), KeyboardButton(text="PNG → JPEG"), KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )


async def get_main_menu_kb(user_id: int):
    if await is_admin(user_id):
        from kbds.admin_reply import admin_kb
        return admin_kb()
    else:
        return main_menu_kb()
