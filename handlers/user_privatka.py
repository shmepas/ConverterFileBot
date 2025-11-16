import os
import shutil
import pypandoc
import traceback
import tempfile
import zipfile
from aiogram import types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest
import asyncio
import time
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import FSInputFile
from PIL import Image
from moviepy.editor import VideoFileClip
from PyPDF2 import PdfReader
from data_base import db
from data_base.db import add_user, log_action, is_admin, is_super_admin
from kbds import reply
from utils import convert_audio_ffmpeg_async, compress_video_ffmpeg_async, convert_video_to_gif_ffmpeg_async, answer_editable

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

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

class FormatStates(StatesGroup):
    waiting_format = State()
    waiting_file = State()

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
        types.KeyboardButton(text="🎞 Форматы")
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
    await answer_editable(message, "Главное меню 👇", reply_markup=kb)

# ------------------------------
# /menu
# ------------------------------
@user_privatka_router.message(Command("menu"))
async def menu_cmd(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.main)
    kb = await build_dynamic_keyboard(message.from_user.id)
    await answer_editable(message, "Главное меню 👇", reply_markup=kb)

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
        await answer_editable(message, "Главное меню 👇", reply_markup=kb)
        return
    last_state = history.pop()
    await state.update_data(history=history)
    await state.set_state(last_state)
    await answer_editable(message, "Главное меню 👇", reply_markup=kb)

# ------------------------------
# Отправка файла
# ------------------------------
@user_privatka_router.message(F.text.lower() == "отправить файл")
async def send_file_handler(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.waiting_file)
    await answer_editable(message, "📎 Отправьте файл для конвертации:", reply_markup=reply.file_menu_kb())

# ------------------------------
# Выбор формата
# ------------------------------
@user_privatka_router.message(F.text.lower() == "форматы")
async def choose_format_handler(message: types.Message, state: FSMContext):
    await state.set_state(MenuStates.waiting_format)
    await answer_editable(message, "Выберите формат конвертации:", reply_markup=reply.format_choice_kb())

# ------------------------------
# Получение файла
# ------------------------------
@user_privatka_router.message(F.document)
async def handle_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    file = message.document
    file_name = file.file_name
    file_path = f"downloads/{user_id}_{file_name}"

    # Максимальный размер входящего файла: 20 МБ (супер-админам без ограничения)
    MAX_INPUT_SIZE = 20 * 1024 * 1024
    file_size = getattr(file, "file_size", 0)

    if file_size > MAX_INPUT_SIZE:
        if await is_super_admin(user_id):
            await message.answer(
                f"⚠️ Ограничение входящего размера ({MAX_INPUT_SIZE // (1024*1024)} МБ) не применяется к супер-админу. Продолжаю загрузку."
            )
        else:
            await message.answer(f"🚫 Входящий файл слишком большой ({file_size // (1024*1024)} МБ). Максимум: {MAX_INPUT_SIZE // (1024*1024)} МБ.")
            await state.clear()
            return

    os.makedirs("downloads", exist_ok=True)

    try:
        await answer_editable(message, "⏳ Скачиваю файл...")
        await message.bot.download(file, destination=file_path)
    except TelegramBadRequest as e:
        msg = str(e)
        if "file is too big" in msg or "too big" in msg.lower():
            await message.answer("❌ Не могу скачать этот файл: Telegram отклонил запрос (файл слишком большой для бота).")
        else:
            await message.answer(f"❌ Ошибка при скачивании: {msg}")
        await state.clear()
        return
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании файла: {traceback.format_exc()}")
        await state.clear()
        return

    await state.update_data(file_path=file_path)
    await state.set_state(MenuStates.waiting_format)
    # Отправляем минимальное сообщение с клавиатурой и сразу удаляем его,
    # чтобы показать только клавиатуру без заметного текста.
    # Отправляем короткое сообщение с клавиатурой и НЕ удаляем его,
    # чтобы клавиатура оставалась видимой у пользователя.
    # Send minimal keyboard prompt but replace previous bot message to avoid clutter
    await answer_editable(message, ".", reply_markup=reply.format_choice_kb())

# ------------------------------
# Выбор формата после загрузки файла
# ------------------------------
@user_privatka_router.message(MenuStates.waiting_format, F.text.in_({"MP3", "MP4", "GIF", "PDF → PNG", "PDF → ZIP", "PNG → JPG", "PNG → JPEG", "TXT"}))
async def user_format_selected(message: types.Message, state: FSMContext):
    """Перенаправляем на обработчик форматов из formats_router"""
    # Перехватываем выбор формата и перенаправляем на формат-роутер
    data = await state.get_data()
    file_path = data.get("file_path")
    
    if not file_path or not os.path.exists(file_path):
        await message.answer("❌ Файл не найден. Отправьте его заново.")
        await state.clear()
        return
    
    # Сохраняем выбор формата в state и перенаправляем
    await state.update_data(selected_format=message.text)
    # Переключаемся на обработчик из formats_router
    await state.set_state(FormatStates.waiting_file)
    # Создаём фиктивный message с document для имитации загрузки через основной формат-роутер
    # На самом деле просто вызываем логику конвертации прямо здесь
    
    fmt = message.text
    temp_dir = tempfile.gettempdir()
    out_file = os.path.join(temp_dir, f"{os.path.splitext(os.path.basename(file_path))[0]}.{fmt.split('→')[-1].strip().lower()}")
    
    try:
        await message.answer("⏳ Конвертирую файл, это может занять время...")
        
        if fmt == "MP3":
            if file_path.lower().endswith((".mp3", ".wav", ".ogg")):
                await convert_audio_ffmpeg_async(file_path, out_file)
            else:
                clip = VideoFileClip(file_path)
                clip.audio.write_audiofile(out_file, verbose=False, logger=None)
                clip.close()

        elif fmt == "MP4":
            try:
                await compress_video_ffmpeg_async(file_path, out_file, crf=30, max_width=640, audio_bitrate="64k", preset="fast")
            except Exception:
                clip = VideoFileClip(file_path)
                clip.write_videofile(out_file, codec="libx264", bitrate="600k", fps=24, audio=True, verbose=False, logger=None)
                clip.close()

        elif fmt == "GIF":
            try:
                await convert_video_to_gif_ffmpeg_async(file_path, out_file, width=480, fps=12)
            except Exception:
                clip = VideoFileClip(file_path)
                clip.write_gif(out_file, fps=12)
                clip.close()

        elif fmt == "PDF → PNG":
            if fitz is None:
                await message.answer("❌ Для конвертации PDF установите PyMuPDF: `pip install PyMuPDF`")
                return

            doc = fitz.open(file_path)
            out_dir = os.path.join(temp_dir, "pdf_images")
            os.makedirs(out_dir, exist_ok=True)
            image_paths = []

            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_path = os.path.join(out_dir, f"page_{i + 1}.png")
                pix.save(img_path)
                image_paths.append(img_path)

            if len(image_paths) == 1:
                await message.answer_document(FSInputFile(image_paths[0]), caption="✅ Конвертация завершена!")
            else:
                zip_path = os.path.join(temp_dir, "pdf_pages.zip")
                with zipfile.ZipFile(zip_path, "w") as zipf:
                    for img in image_paths:
                        zipf.write(img, os.path.basename(img))
                await message.answer_document(FSInputFile(zip_path), caption="✅ Все страницы в ZIP-архиве!")

        elif fmt == "PDF → ZIP":
            if fitz is None:
                await message.answer("❌ Для работы с PDF установите PyMuPDF: `pip install PyMuPDF`")
                return

            doc = fitz.open(file_path)
            out_file = os.path.join(temp_dir, f"{os.path.splitext(os.path.basename(file_path))[0]}.zip")
            with zipfile.ZipFile(out_file, "w") as zipf:
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_name = f"page_{i + 1}.png"
                    img_path = os.path.join(temp_dir, img_name)
                    pix.save(img_path)
                    zipf.write(img_path, img_name)
                    os.remove(img_path)

        elif fmt == "PNG → JPG" or fmt == "PNG → JPEG":
            target_ext = "jpg" if fmt == "PNG → JPG" else "jpeg"
            out_file = os.path.join(temp_dir, f"{os.path.splitext(os.path.basename(file_path))[0]}.{target_ext}")
            img = Image.open(file_path)
            if img.mode in ("RGBA", "LA", "P"):
                rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "RGBA":
                    rgb_img.paste(img, mask=img.split()[-1])
                else:
                    rgb_img.paste(img)
                rgb_img.save(out_file, "JPEG", quality=90)
            else:
                img.save(out_file, "JPEG", quality=90)

        elif fmt == "TXT":
            ext = os.path.splitext(os.path.basename(file_path))[1].lower()
            
            if ext == ".pdf":
                reader = PdfReader(file_path)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() or ""
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(text)
            elif ext == ".zip":
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    extracted_files = zip_ref.namelist()
                    text = ""
                    for ef in extracted_files:
                        if ef.lower().endswith(('.txt', '.md', '.log')):
                            with zip_ref.open(ef) as file:
                                content = file.read().decode('utf-8', errors='ignore')
                                text += f"\n\n--- {ef} ---\n\n" + content
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(text)
            elif ext in [".txt", ".md", ".log"]:
                shutil.copy(file_path, out_file)
            else:
                await message.answer("❌ Неподдерживаемый текстовый формат для конвертации.")
                return

        if not os.path.exists(out_file):
            await message.answer("❌ Ошибка конвертации: файл не был создан.")
            return

        await message.answer_document(FSInputFile(out_file), caption=f"✅ Файл конвертирован в {fmt}")
        await log_action(message.from_user.id, f"Конвертировал в {fmt}")

    except Exception as e:
        # Log full traceback to a file for debugging and notify admin logs
        tb = traceback.format_exc()
        os.makedirs("logs", exist_ok=True)
        log_file = os.path.join("logs", f"convert_error_{int(time.time())}.log")
        try:
            with open(log_file, "w", encoding="utf-8") as lf:
                lf.write(tb)
        except Exception:
            pass
        # Save short log to DB if available
        try:
            await log_action(message.from_user.id, f"Conversion error, see {log_file}")
        except Exception:
            pass
        # Notify user with concise message
        short = str(e)[:200]
        await message.answer(f"❌ Ошибка конвертации: {short}\n(Полный лог сохранён)")
    finally:
        for path in (file_path, out_file):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        await state.clear()

@user_privatka_router.message(MenuStates.waiting_format, F.text == "⬅️ Назад в меню")
async def back_from_format(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_path = data.get("file_path")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass
    await state.clear()
    kb = await build_dynamic_keyboard(message.from_user.id)
    await message.answer("↩️ Возврат в главное меню.", reply_markup=kb)

# Кнопка "Платежи" — только свои записи

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
