from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Клавиатура для обычного админа
admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📜 Просмотр логов")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)

# Клавиатура для супер-админа
super_admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📜 Просмотр логов"), KeyboardButton(text="➕ Добавить админа")],
        [KeyboardButton(text="➖ Удалить админа"), KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)
