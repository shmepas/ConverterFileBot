from aiogram import F, types, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from filters.chat_types import ChatTypeFilter
from kbds import reply
from data_base.db import (
    add_user, log_action, get_user_logs,
    is_admin, is_super_admin, add_admin, remove_admin
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder
import html

user_privatka_router = Router()
user_privatka_router.message.filter(ChatTypeFilter(["private"]))

# ------------------------------
# FSM состояния
# ------------------------------
class MenuStates(StatesGroup):
    main = State()
    about = State()
    payment = State()
    formats = State()
    history_payment = State()
    add_admin_wait_id = State()
    remove_admin_wait_id = State()

# ------------------------------
# Динамическая клавиатура по роли
# ------------------------------
async def build_dynamic_keyboard(user_id: int, admin_open: bool = False) -> types.ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.attach(reply.start_kb3)
    kb.row(types.KeyboardButton(text="Моя роль"))

    if await is_admin(user_id) or await is_super_admin(user_id):
        if admin_open:
            if await is_super_admin(user_id):
                kb.row(
                    types.KeyboardButton(text="📜 Просмотр логов"),
                    types.KeyboardButton(text="➕ Добавить админа"),
                    types.KeyboardButton(text="➖ Удалить админа")
                )
            else:
                kb.row(types.KeyboardButton(text="📜 Просмотр логов"))
            kb.row(types.KeyboardButton(text="⬅️ Закрыть админку"))
        else:
            kb.row(types.KeyboardButton(text="Админка"))
    return kb.as_markup(resize_keyboard=True)

# ------------------------------
# Универсальная смена состояния
# ------------------------------
async def change_state(message: types.Message, state: FSMContext, new_state: State, text: str):
    user_id = message.from_user.id
    kb = await build_dynamic_keyboard(user_id)

    data = await state.get_data()
    history = data.get("history", [])
    current_state = await state.get_state()
    if current_state:
        history.append(current_state)
    await state.update_data(history=history)

    await state.set_state(new_state)
    await message.answer(text, reply_markup=kb)

# ------------------------------
# /start
# ------------------------------
@user_privatka_router.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.set_state(MenuStates.main)

    await add_user(user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)

    if await is_super_admin(user_id):
        role = "Супер-админ"
    elif await is_admin(user_id):
        role = "Админ"
    else:
        role = "Пользователь"

    await log_action(user_id, f"Команда /start ({role})")
    kb = await build_dynamic_keyboard(user_id)

    await message.answer(f"Привет! 👋 Я твой личный конвертер файлов.\nВаша роль: {role}", reply_markup=types.ReplyKeyboardRemove())
    await message.answer("Главное меню 👇", reply_markup=kb)

# ------------------------------
# /menu
# ------------------------------
@user_privatka_router.message(Command("menu"))
async def menu_cmd(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.main)
    kb = await build_dynamic_keyboard(message.from_user.id)
    await message.answer("Главное меню 👇", reply_markup=kb)

# ------------------------------
# Кнопка "Назад"
# ------------------------------
@user_privatka_router.message(F.text.lower().contains("назад"))
async def back_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    kb = await build_dynamic_keyboard(user_id)

    data = await state.get_data()
    history = data.get("history", [])
    if not history:
        await state.set_state(MenuStates.main)
        await message.answer("Главное меню 👇", reply_markup=kb)
        return

    last_state = history.pop()
    await state.update_data(history=history)
    await state.set_state(last_state)

    text = "Главное меню 👇"
    if last_state == MenuStates.about.state:
        text = "Я бот для конвертации файлов.\nМогу помочь изменить формат!"
    elif last_state == MenuStates.payment.state:
        text = "Варианты оплаты:\n1. Карта\n2. Qiwi\n3. PayPal"
    elif last_state == MenuStates.formats.state:
        formats = ["PDF", "DOCX", "TXT", "JPEG", "PNG", "MP3", "MP4", "ZIP"]
        text = "Доступные форматы:\n" + "\n".join(f"- {f}" for f in formats)
    elif last_state == MenuStates.history_payment.state:
        text = "В разработке 🚧"

    await message.answer(text, reply_markup=kb)

# ------------------------------
# Основной обработчик кнопок и FSM для админов
# ------------------------------
@user_privatka_router.message(F.text | F.sticker | F.photo | F.document | F.video)
async def keyboard_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text_lower = message.text.lower() if message.text else ""
    current_state = await state.get_state()

    # FSM: добавление админа
    if current_state == MenuStates.add_admin_wait_id.state:
        try:
            new_admin_id = int(message.text)
            if await is_super_admin(new_admin_id):
                await message.answer("❌ Нельзя добавить супер-админа в обычные админы.")
            else:
                await add_admin(new_admin_id)
                await log_action(user_id, f"Добавил нового админа {new_admin_id}")
                await message.answer(f"✅ Пользователь {new_admin_id} теперь админ.")
        except ValueError:
            await message.answer("❌ ID должен быть числом. Попробуйте снова.")
            return
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await message.answer("Главное меню 👇", reply_markup=kb)
        return

    # FSM: удаление админа
    if current_state == MenuStates.remove_admin_wait_id.state:
        try:
            remove_id = int(message.text)
            if await is_super_admin(remove_id):
                await message.answer("❌ Нельзя удалить супер-админа!")
            elif not await is_admin(remove_id):
                await message.answer(f"❌ Пользователь {remove_id} не является админом.")
            else:
                await remove_admin(remove_id)
                await log_action(user_id, f"Удалил админа {remove_id}")
                await message.answer(f"✅ Пользователь {remove_id} больше не админ.")
        except ValueError:
            await message.answer("❌ ID должен быть числом. Попробуйте снова.")
            return
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await message.answer("Главное меню 👇", reply_markup=kb)
        return

    # Основные кнопки меню
    if text_lower in ["меню"]:
        kb = await build_dynamic_keyboard(user_id)
        await state.set_state(MenuStates.main)
        await message.answer("Главное меню 👇", reply_markup=kb)
        return
    elif text_lower in ["о боте", "инфа"]:
        await change_state(message, state, MenuStates.about, "Я бот для конвертации файлов.\nМогу помочь изменить формат!")
        return
    elif text_lower in ["оплата", "вариант оплаты"]:
        await change_state(message, state, MenuStates.payment, "Варианты оплаты:\n1. Карта\n2. Qiwi\n3. PayPal")
        return
    elif text_lower == "платежи":
        await change_state(message, state, MenuStates.history_payment, "В разработке 🚧")
        return
    elif text_lower in ["форматы", "доступные файл-форматы", "формат", "выбор формата"]:
        formats = ["PDF", "DOCX", "TXT", "JPEG", "PNG", "MP3", "MP4", "ZIP"]
        await change_state(message, state, MenuStates.formats, "Доступные форматы:\n" + "\n".join(f"- {f}" for f in formats))
        return
    elif text_lower == "моя роль":
        role = "Пользователь"
        if await is_super_admin(user_id):
            role = "Супер-админ"
        elif await is_admin(user_id):
            role = "Админ"
        await message.answer(f"Ваша роль: {role}")
        return

    # Админка
    if await is_admin(user_id) or await is_super_admin(user_id):
        if text_lower == "админка":
            kb = await build_dynamic_keyboard(user_id, admin_open=True)
            await message.answer("🔧 Админка открыта 👇", reply_markup=kb)
            return
        if text_lower in ["⬅️ закрыть админку", "закрыть админку"]:
            kb = await build_dynamic_keyboard(user_id, admin_open=False)
            await message.answer("Главное меню 👇", reply_markup=kb)
            return
        if text_lower == "📜 просмотр логов":
            logs = await get_user_logs(limit=15)
            if not logs:
                await message.answer("📭 Логи пока пусты.")
                return
            text_lines = [f"👤 <b>{log['user_id']}</b>\n🕓 {log['timestamp']}\n➡️ {html.escape(log['action'])}\n──────────────" for log in logs]
            await message.answer(f"📜 <b>Последние действия пользователей:</b>\n\n" + "\n".join(text_lines), parse_mode="HTML")
            return
        if text_lower == "➕ добавить админа" and await is_super_admin(user_id):
            await state.set_state(MenuStates.add_admin_wait_id)
            await message.answer("Введите Telegram ID нового админа:")
            return
        if text_lower == "➖ удалить админа" and await is_super_admin(user_id):
            await state.set_state(MenuStates.remove_admin_wait_id)
            await message.answer("Введите Telegram ID админа для удаления:")
            return

    # Неизвестная кнопка
    await message.answer("Я не совсем понял твоё сообщение 😅\nПопробуй одну из кнопок")
