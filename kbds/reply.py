from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# ==============================
# Удаление клавиатуры
# ==============================
del_kb = ReplyKeyboardRemove()

# ==============================
# Стартовая клавиатура (вариант 1)
# ==============================
start_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Меню"),
            KeyboardButton(text="О боте")
        ],
        [
            KeyboardButton(text="Вариант оплаты"),
            KeyboardButton(text="Выбор формата")
        ]
    ],
    resize_keyboard=True,
    input_field_placeholder='Что вас интересует?'
)

# ==============================
# Стартовая клавиатура (вариант 2) с ReplyKeyboardBuilder
# ==============================
start_kb2 = ReplyKeyboardBuilder()
start_kb2.add(
    KeyboardButton(text="Меню"),
    KeyboardButton(text="О боте"),
    KeyboardButton(text="Вариант оплаты"),
    KeyboardButton(text="Выбор формата")
)
start_kb2.adjust(2, 2)  # 2 колонки, 2 ряда

# ==============================
# Стартовая клавиатура (вариант 3) с дополнительной кнопкой "Платежи"
# ==============================
start_kb3 = ReplyKeyboardBuilder()
start_kb3.add(
    KeyboardButton(text="Меню"),
    KeyboardButton(text="О боте"),
    KeyboardButton(text="Вариант оплаты"),
    KeyboardButton(text="Выбор формата")
)
start_kb3.row(KeyboardButton(text="Платежи"))  # отдельная строка
start_kb3.adjust(2)  # выравнивание основных кнопок по 2

# ==============================
# Кнопка "Назад"
# ==============================
back_kb = ReplyKeyboardBuilder()
back_kb.add(KeyboardButton(text="⬅️ Назад"))
back_kb.adjust(1)
