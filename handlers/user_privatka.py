import os
import shutil
import pypandoc
from aiogram import types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from PIL import Image
from moviepy.editor import VideoFileClip
from data_base import db
from data_base.db import add_user, log_action, is_admin, is_super_admin
from kbds import reply

user_privatka_router = Router()
user_privatka_router.message.filter(lambda message: message.chat.type == "private")

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
    waiting_file = State()
    waiting_format = State()

# ------------------------------
# Динамическая клавиатура по роли
# ------------------------------
async def build_dynamic_keyboard(user_id: int, admin_open: bool = False) -> types.ReplyKeyboardMarkup:
    kb_builder = ReplyKeyboardBuilder()
    
    # Основные кнопки
    kb_builder.add(
        types.KeyboardButton(text="Меню"),
        types.KeyboardButton(text="О боте"),
        types.KeyboardButton(text="Вариант оплаты"),
        types.KeyboardButton(text="Выбор формата")
    )
    kb_builder.row(types.KeyboardButton(text="💳 Платежи"))
    kb_builder.row(types.KeyboardButton(text="Моя роль"))

    # Админка
    if await is_admin(user_id) or await is_super_admin(user_id):
        if admin_open:
            if await is_super_admin(user_id):
                kb_builder.row(
                    types.KeyboardButton(text="📜 Просмотр логов"),
                    types.KeyboardButton(text="➕ Добавить админа"),
                    types.KeyboardButton(text="➖ Удалить админа")
                )
            else:
                kb_builder.row(types.KeyboardButton(text="📜 Просмотр логов"))
            kb_builder.row(types.KeyboardButton(text="⬅️ Закрыть админку"))
        else:
            kb_builder.row(types.KeyboardButton(text="Админка"))

    return kb_builder.as_markup(resize_keyboard=True)

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

    role = "Пользователь"
    if await is_super_admin(user_id):
        role = "Супер-админ"
    elif await is_admin(user_id):
        role = "Админ"

    await log_action(user_id, f"Команда /start ({role})")
    kb = await build_dynamic_keyboard(user_id)
    await message.answer(f"Привет! 👋 Я конвертирую файлы в нужный формат.\nВаша роль: {role}",
                         reply_markup=types.ReplyKeyboardRemove())
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
@user_privatka_router.message(F.text.lower() == "назад")
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
    await message.answer("Главное меню 👇", reply_markup=kb)

# ------------------------------
# Отправка файла
# ------------------------------
@user_privatka_router.message(F.text.lower() == "отправить файл")
async def send_file_handler(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.waiting_file)
    await message.answer("📎 Отправьте файл для конвертации:", reply_markup=reply.file_menu_kb())

# ------------------------------
# Выбор формата
# ------------------------------
@user_privatka_router.message(F.text.lower() == "выбор формата")
async def choose_format_handler(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.waiting_format)
    await message.answer("Выберите формат конвертации:", reply_markup=reply.format_choice_kb())

# ------------------------------
# Получение файла
# ------------------------------
@user_privatka_router.message(F.document)
async def handle_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    file = message.document
    file_name = file.file_name
    file_path = f"downloads/{user_id}_{file_name}"

    os.makedirs("downloads", exist_ok=True)
    await message.bot.download(file, destination=file_path)

    await state.update_data(file_path=file_path)
    await message.answer("Файл получен ✅\nТеперь выберите формат для конвертации:",
                         reply_markup=reply.format_choice_kb())
    await state.set_state(MenuStates.waiting_format)

# ------------------------------
# Конвертация файла
# ------------------------------
@user_privatka_router.callback_query(F.data.in_(["pdf_zip", "jpg_png", "mp4_mp3"]))
async def format_callback_handler(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    src_path = data.get("file_path")
    if not src_path or not os.path.exists(src_path):
        await cb.message.answer("❌ Файл не найден. Отправь заново.")
        await state.set_state(MenuStates.main)
        await cb.answer()
        return

    mapping = {
        "pdf_zip": ("pdf", "zip"),
        "jpg_png": ("jpg", "png"),
        "mp4_mp3": ("mp4", "mp3")
    }

    ext_from, target_format = mapping.get(cb.data)
    base_name = os.path.splitext(os.path.basename(src_path))[0]
    dst_path = f"converted/{base_name}.{target_format}"
    os.makedirs("converted", exist_ok=True)

    try:
        if ext_from in ["txt", "docx", "pdf", "md"]:
            pypandoc.convert_text(
                open(src_path, "r", encoding="utf-8").read(),
                target_format,
                format=ext_from,
                outputfile=dst_path,
                extra_args=['--standalone']
            )
        elif ext_from in ["jpg", "jpeg", "png"]:
            img = Image.open(src_path)
            img.save(dst_path)
        elif ext_from in ["mp4", "mov"] and target_format == "mp3":
            clip = VideoFileClip(src_path)
            clip.audio.write_audiofile(dst_path)
            clip.close()
        elif ext_from in ["mp3", "wav"] and target_format in ["mp3", "wav"]:
            shutil.copy(src_path, dst_path)
        else:
            await cb.message.answer("❌ Невозможно конвертировать этот тип файла в указанный формат.")
            await cb.answer()
            return

        await cb.message.answer_document(types.FSInputFile(dst_path),
                                         caption=f"✅ Конвертация в {target_format.upper()} завершена!")
        await log_action(cb.from_user.id, f"Конвертировал {ext_from} → {target_format}")
    except Exception as e:
        await cb.message.answer(f"⚠️ Ошибка при конвертации: {e}")
    finally:
        await state.set_state(MenuStates.main)
        await cb.answer()

# ------------------------------
# Кнопка "Платежи" — только свои записи
# ------------------------------
@user_privatka_router.message(F.text == "💳 Платежи")
async def show_payments_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        payments = await db.get_user_payments(user_id)
    except Exception as e:
        await message.answer("⚠️ Ошибка при получении истории платежей.")
        print("get_user_payments error:", e)
        return

    if not payments:
        await message.answer("📭 У вас пока нет записей о платежах.")
        return

    lines = []
    for p in payments:
        date = p.get("date", "")
        amount = p.get("amount", 0)
        ptype = p.get("type", "")
        desc = p.get("description", "")
        sign = "+" if ptype.lower().startswith("пополн") else "-" if ptype.lower().startswith("спис") else ""
        lines.append(f"{date} — {sign}{amount} ₽ — {ptype} — {desc}")

    text = "💳 Ваша история платежей:\n\n" + "\n".join(lines)
    kb = await build_dynamic_keyboard(user_id)
    await message.answer(text, reply_markup=kb)
