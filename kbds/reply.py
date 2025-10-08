from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder


start_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Меню"),
            KeyboardButton(text="О боте"),
        ],
        {
            KeyboardButton(text="Вариант оплаты"),
            KeyboardButton(text="Выбор формата"),
        }
    ],
    resize_keyboard=True,
    input_field_placeholder='Что вас интересует?'
)

del_kb = ReplyKeyboardRemove()

start_kb2 = ReplyKeyboardBuilder()
start_kb2.add(
    KeyboardButton(text="Меню"),
    KeyboardButton(text="О боте"),
    KeyboardButton(text="Вариант оплаты"),
    KeyboardButton(text="Выбор формата"),
)
start_kb2.adjust(2, 2)


start_kb3 = ReplyKeyboardBuilder()
start_kb3.attach(start_kb2)
start_kb3.row(KeyboardButton(text="Платежи"),)



back_kb = ReplyKeyboardBuilder()
back_kb.button(text="⬅️ Назад")
back_kb.adjust(1)
