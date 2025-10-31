from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

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
            [KeyboardButton(text="Меню"), KeyboardButton(text="О боте")],
            [KeyboardButton(text="Вариант оплаты"), KeyboardButton(text="Выбор формата")],
            [KeyboardButton(text="💳 Платежи")],
            [KeyboardButton(text="🎞 Форматы")],
        ],
        resize_keyboard=True
    )
    return kb

# ==============================
# Кнопки для работы с файлами
# ==============================
def file_menu_kb():
    kb = ReplyKeyboardBuilder()
    kb.add(
        KeyboardButton(text="Отправить файл"),
        KeyboardButton(text="Выбор формата")
    )
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

# ==============================
# Кнопка "Назад"
# ==============================
def back_kb():
    kb = ReplyKeyboardBuilder()
    kb.add(KeyboardButton(text="⬅️ Назад"))
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

# ==============================
# Инлайн-кнопки для выбора формата конвертации
# ==============================
def format_choice_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="PDF → PNG"), KeyboardButton(text="PDF → ZIP")],
            [KeyboardButton(text="MP3"), KeyboardButton(text="MP4"), KeyboardButton(text="GIF")],
            [KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )
    return kb
