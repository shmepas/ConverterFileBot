from aiogram import F, types, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from filters.chat_types import ChatTypeFilter
from kbds import reply

# === Инициализация ===
user_privatka_router = Router()
user_privatka_router.message.filter(ChatTypeFilter(["private"]))

# === Состояния меню ===
class MenuStates(StatesGroup):
    main = State()
    about = State()
    payment = State()
    formats = State()
    history_payment = State()  # отдельное состояние для кнопки "Платежи"

# === Универсальная функция перехода ===
async def change_state(message: types.Message, state: FSMContext, new_state: State, text: str, keyboard):
    data = await state.get_data()
    history = data.get("history", [])

    current_state = await state.get_state()
    if current_state:
        history.append(current_state)

    await state.update_data(history=history)
    await state.set_state(new_state)

    # Просто отправляем новое сообщение с клавиатурой
    await message.answer(
        text,
        reply_markup=keyboard.as_markup(resize_keyboard=True)
    )



# === /start ===
@user_privatka_router.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.main)
    await message.answer(
        "Привет! 👋 Я твой личный конвертер файлов.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await message.answer(
        "Главное меню 👇",
        reply_markup=reply.start_kb3.as_markup(resize_keyboard=True)
    )


# === /menu ===
@user_privatka_router.message(Command("menu"))
async def menu_cmd(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.main)
    await message.answer(
        "Главное меню 👇",
        reply_markup=reply.start_kb3.as_markup(resize_keyboard=True)
    )


# === Кнопка «Назад» ===
@user_privatka_router.message(F.text.lower().contains("назад"))
async def back_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    history = data.get("history", [])

    if not history:
        await state.set_state(MenuStates.main)
        await message.answer(
            "Главное меню 👇",
            reply_markup=reply.start_kb3.as_markup(resize_keyboard=True)
        )
        return

    last_state = history.pop()
    await state.update_data(history=history)
    await state.set_state(last_state)

    # выбираем текст и клавиатуру по состоянию
    if last_state == MenuStates.main.state:
        text = "Главное меню 👇"
        kb = reply.start_kb3
    elif last_state == MenuStates.about.state:
        text = "Я бот для конвертации файлов.\nМогу помочь изменить формат!"
        kb = reply.back_kb
    elif last_state == MenuStates.payment.state:
        text = "Варианты оплаты:\n1. Карта\n2. Qiwi\n3. PayPal"
        kb = reply.back_kb
    elif last_state == MenuStates.formats.state:
        formats = ["PDF", "DOCX", "TXT", "JPEG", "PNG", "MP3", "MP4", "ZIP"]
        text = "Доступные форматы:\n" + "\n".join(f"- {f}" for f in formats)
        kb = reply.back_kb
    elif last_state == MenuStates.history_payment.state:
        text = "В разработке 🚧"
        kb = reply.back_kb

    # Очистка предыдущей клавиатуры
    await message.answer(
        text,
        reply_markup=types.ReplyKeyboardRemove()
    )
    await message.answer(
        text,
        reply_markup=kb.as_markup(resize_keyboard=True)
    )


# === Обработка кнопок (ReplyKeyboard) ===
@user_privatka_router.message(F.text)
async def keyboard_handler(message: types.Message, state: FSMContext):
    # автоинициализация состояния, если бот перезапущен
    if not await state.get_state():
        await state.set_state(MenuStates.main)

    text = message.text.lower()

    if text in ["меню"]:
        await state.set_state(MenuStates.main)
        await message.answer(
            "Главное меню 👇",
            reply_markup=reply.start_kb3.as_markup(resize_keyboard=True)
        )

    elif text in ["о боте", "инфа"]:
        await change_state(
            message,
            state,
            MenuStates.about,
            "Я бот для конвертации файлов.\nМогу помочь изменить формат!",
            reply.back_kb
        )

    elif text in ["оплата", "вариант оплаты"]:
        await change_state(
            message,
            state,
            MenuStates.payment,
            "Варианты оплаты:\n1. Карта\n2. Qiwi\n3. PayPal",
            reply.back_kb
        )

    elif text == "платежи":
        await change_state(
            message,
            state,
            MenuStates.history_payment,
            "В разработке 🚧",
            reply.back_kb
        )

    elif text in ["форматы", "доступные файл-форматы", "формат", "выбор формата"]:
        formats = ["PDF", "DOCX", "TXT", "JPEG", "PNG", "MP3", "MP4", "ZIP"]
        await change_state(
            message,
            state,
            MenuStates.formats,
            "Доступные форматы:\n" + "\n".join(f"- {f}" for f in formats),
            reply.back_kb
        )

    elif text in ["⬅️ назад"]:
        await back_handler(message, state)

    else:
        await message.answer(
            "Я не совсем понял твоё сообщение 😅\n"
            "Попробуй одну из кнопок или команд:\n/menu, /about, /payment, /formats"
        )
        