import os
import tempfile
import traceback
import zipfile
import asyncio  # добавлен для асинхронного вызова ffmpeg
import subprocess
from kbds.reply import main_menu_kb
from aiogram.exceptions import TelegramBadRequest

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None  # Безопасно, если не установлен

from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from moviepy.editor import VideoFileClip
from utils import compress_video_ffmpeg_async, convert_video_to_gif_ffmpeg_async, upload_file_fallback, answer_editable

from data_base.db import (
    log_action, is_admin, is_super_admin,
    add_admin, remove_admin, get_user_logs, get_user_payments
)

admin_router = Router()
PAGE_SIZE = 20

# ------------------------------
# 💉 FIX: Клавиатура обычного пользователя
# ------------------------------
# ------------------------------
# FSM состояния
# ------------------------------
class AdminStates(StatesGroup):
    main = State()
    add_admin_wait_id = State()
    remove_admin_wait_id = State()
    view_logs_page = State()
    view_payments_page = State()

class FormatStates(StatesGroup):
    waiting_format = State()
    waiting_file = State()

# ------------------------------
# Главная клавиатура админа
# ------------------------------
async def admin_main_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    kb_builder = ReplyKeyboardBuilder()
    if await is_super_admin(user_id):
        kb_builder.row(
            KeyboardButton(text="📜 Просмотр логов"),
            KeyboardButton(text="➕ Добавить админа"),
            KeyboardButton(text="➖ Удалить админа"),
            KeyboardButton(text="💳 Платежи"),
            KeyboardButton(text="🎞 Форматы")
        )
    elif await is_admin(user_id):
        kb_builder.row(
            KeyboardButton(text="📜 Просмотр логов"),
            KeyboardButton(text="💳 Платежи"),
            KeyboardButton(text="🎞 Форматы")
        )
    kb_builder.row(KeyboardButton(text="⬅️ Закрыть админку"))
    return kb_builder.as_markup(resize_keyboard=True)

# ------------------------------
# Клавиатура выбора формата
# ------------------------------
def formats_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="MP3"), KeyboardButton(text="MP4"), KeyboardButton(text="GIF")],
            [KeyboardButton(text="TXT"), KeyboardButton(text="PDF → PNG")],
            [KeyboardButton(text="PDF → ZIP"), KeyboardButton(text="PNG → JPG")],
            [KeyboardButton(text="PNG → JPEG"), KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )

# ------------------------------
# Асинхронная функция для конвертации аудио с помощью ffmpeg
# ------------------------------
async def convert_audio_ffmpeg_async(input_path: str, output_path: str):
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vn",
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "192k",
        output_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {stderr.decode()}")

# ------------------------------
# Открытие админки
# ------------------------------
@admin_router.message(F.text == "Админка")
async def open_admin_panel(message: types.Message):
    user_id = message.from_user.id
    if await is_admin(user_id) or await is_super_admin(user_id):
        kb = await admin_main_kb(user_id)
        await message.answer("👋 Добро пожаловать в админ-панель!", reply_markup=kb)
    else:
        await message.answer("🚫 У вас нет доступа к админ-панели.")

# ------------------------------
# Моя роль
# ------------------------------
@admin_router.message(F.text == "Моя роль")
async def show_my_role(message: types.Message):
    user_id = message.from_user.id
    if await is_super_admin(user_id):
        role = "🌟 Супер-админ"
    elif await is_admin(user_id):
        role = "🛠 Админ"
    else:
        role = "👤 Пользователь"
    await message.answer(f"Ваша роль: {role}")

# ------------------------------
# Добавление админа
# ------------------------------
@admin_router.message(F.text == "➕ Добавить админа")
async def add_admin_start(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id):
        await message.answer("❌ Только супер-админ может добавлять админов.")
        return
    await state.set_state(AdminStates.add_admin_wait_id)
    await message.answer("Введите ID пользователя для добавления в админы:")

@admin_router.message(AdminStates.add_admin_wait_id, F.text)
async def add_admin_confirm(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await add_admin(user_id)
        await message.answer(f"✅ Пользователь {user_id} добавлен как админ.")
        await log_action(message.from_user.id, f"Добавил админа {user_id}")
    except ValueError:
        await message.answer("❌ Неверный ID, введите числовой ID.")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)

# ------------------------------
# Удаление админа
# ------------------------------
@admin_router.message(F.text == "➖ Удалить админа")
async def remove_admin_start(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id):
        await message.answer("❌ Только супер-админ может удалять админов.")
        return
    await state.set_state(AdminStates.remove_admin_wait_id)
    await message.answer("Введите ID пользователя для удаления из админов:")

@admin_router.message(AdminStates.remove_admin_wait_id, F.text)
async def remove_admin_confirm(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await remove_admin(user_id)
        await message.answer(f"✅ Пользователь {user_id} удален из админов.")
        await log_action(message.from_user.id, f"Удалил админа {user_id}")
    except ValueError:
        await message.answer("❌ Неверный ID, введите числовой ID.")
    finally:
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)

# ------------------------------
# Просмотр логов
# ------------------------------
@admin_router.message(F.text == "📜 Просмотр логов")
async def view_logs_start(message: types.Message, state: FSMContext):
    await state.set_state(AdminStates.view_logs_page)
    await state.update_data(page=1)
    await send_logs_page(message, state)

async def send_logs_page(message: types.Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)
    logs = await get_user_logs(limit=500)
    if not logs:
        await message.answer("📭 Логов пока нет.")
        return

    total = len(logs)
    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_logs = logs[start:end]

    text = f"📜 Логи — страница {page}/{total_pages}\n\n"
    for log in page_logs:
        uid = log.get("user_id", "")
        ts = log.get("timestamp", "")
        action = log.get("action", "")
        text += f"👤 {uid} | 🕒 {ts}\n➡️ {action}\n\n"

    builder = ReplyKeyboardBuilder()
    if page > 1:
        builder.add(KeyboardButton(text="⬅️ Назад"))
    if page < total_pages:
        builder.add(KeyboardButton(text="▶️ Далее"))
    builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))
    kb = builder.as_markup(resize_keyboard=True)
    await message.answer(text.strip(), reply_markup=kb)
    await state.update_data(page=page)

@admin_router.message(AdminStates.view_logs_page)
async def logs_navigation(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()
    page = data.get("page", 1)
    if text == "⬅️ Назад":
        await state.update_data(page=max(1, page - 1))
        await send_logs_page(message, state)
    elif text == "▶️ Далее":
        await state.update_data(page=page + 1)
        await send_logs_page(message, state)
    elif text == "⬅️ Выйти в главное меню":
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)
    else:
        await message.answer("❌ Неизвестная команда. Используйте кнопки ниже.")

# ------------------------------
# Просмотр платежей
# ------------------------------
@admin_router.message(F.text == "💳 Платежи")
async def view_payments_start(message: types.Message, state: FSMContext):
    await state.set_state(AdminStates.view_payments_page)
    await state.update_data(page=1)
    await send_payments_page(message, state)

async def send_payments_page(message: types.Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)
    payments = await get_user_payments(limit=500)
    if not payments:
        await message.answer("📭 История платежей пока пуста.")
        return

    total = len(payments)
    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_payments = payments[start:end]

    text = f"💳 История платежей — страница {page}/{total_pages}\n\n"
    for p in page_payments:
        text += f"👤 {p.get('user_id')} | 💰 {p.get('amount')} | 🕒 {p.get('timestamp')}\n\n"

    builder = ReplyKeyboardBuilder()
    if page > 1:
        builder.add(KeyboardButton(text="⬅️ Назад"))
    if page < total_pages:
        builder.add(KeyboardButton(text="▶️ Далее"))
    builder.row(KeyboardButton(text="⬅️ Выйти в главное меню"))
    kb = builder.as_markup(resize_keyboard=True)
    await message.answer(text.strip(), reply_markup=kb)
    await state.update_data(page=page)

@admin_router.message(AdminStates.view_payments_page)
async def payments_navigation(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()
    page = data.get("page", 1)
    if text == "⬅️ Назад":
        await state.update_data(page=max(1, page - 1))
        await send_payments_page(message, state)
    elif text == "▶️ Далее":
        await state.update_data(page=page + 1)
        await send_payments_page(message, state)
    elif text == "⬅️ Выйти в главное меню":
        await state.set_state(AdminStates.main)
        kb = await admin_main_kb(message.from_user.id)
        await message.answer("Возврат в главное меню админки 👇", reply_markup=kb)
    else:
        await message.answer("❌ Неизвестная команда. Используйте кнопки ниже.")

# ------------------------------
# Работа с форматами
# ------------------------------
@admin_router.message(F.text == "🎞 Форматы")
async def choose_format(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    # 🔒 Проверка доступа
    if not (await is_admin(user_id) or await is_super_admin(user_id)):
        await message.answer("🚫 У вас нет доступа к этому разделу.")
        return

    await state.set_state(FormatStates.waiting_format)
    await answer_editable(message, "Выберите формат для конвертации 👇", reply_markup=formats_kb())

@admin_router.message(FormatStates.waiting_format, F.text.in_({"MP3", "MP4", "GIF", "TXT", "PDF → PNG", "PDF → ZIP", "PNG → JPG", "PNG → JPEG"}))
async def format_selected(message: types.Message, state: FSMContext):
    await state.update_data(selected_format=message.text)
    await state.set_state(FormatStates.waiting_file)
    await answer_editable(
        message,
        f"📁 Отправьте файл для конвертации в {message.text} формат.\n\n"
        "Когда закончите — нажмите ⬅️ Назад.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]],
            resize_keyboard=True
        )
    )

@admin_router.message(FormatStates.waiting_file, F.content_type.in_({"document", "video", "audio"}))
async def convert_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fmt = data.get("selected_format")
    file_obj = message.document or message.video or message.audio
    if not fmt:
        await message.answer("⚠️ Сначала выберите формат.")
        return

    # Максимальный размер входящего файла для Telegram: 20 МБ
    MAX_INPUT_SIZE = 50 * 1024 * 1024

    # Проверяем размер входящего файла (супер-админам — без ограничения)
    file_size = getattr(file_obj, "file_size", 0)
    user_id = message.from_user.id
    if file_size > MAX_INPUT_SIZE:
        if await is_super_admin(user_id):
            await message.answer(f"⚠️ Ограничение входящего размера ({MAX_INPUT_SIZE // (1024*1024)} МБ) не применяется к супер-админу. Продолжаю загрузку и конвертацию.")
        else:
            await message.answer(f"🚫 Входящий файл слишком большой ({file_size // (1024*1024)} МБ). Максимум: {MAX_INPUT_SIZE // (1024*1024)} МБ.")
            await state.clear()
            return

    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, file_obj.file_name)
    out_file = os.path.join(temp_dir, f"{os.path.splitext(file_obj.file_name)[0]}.{fmt.lower()}")
    
    try:
        await message.answer("⏳ Скачиваю файл...")

        # Скачиваем файл через бота
        try:
            file_info = await message.bot.get_file(file_obj.file_id)
            await message.bot.download_file(file_info.file_path, destination=file_path)
        except TelegramBadRequest as e:
            # Telegram может отказать в выдаче файла, если он слишком большой для ботов
            msg = str(e)
            if "file is too big" in msg or "too big" in msg.lower():
                await message.answer("❌ Не могу скачать этот файл: Telegram отклонил запрос (файл слишком большой для бота).")
            else:
                await message.answer(f"❌ Ошибка при скачивании файла: {msg}")
            await state.clear()
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка при скачивании файла: {traceback.format_exc()}")
            await state.clear()
            return

        await message.answer("⏳ Конвертирую файл, это может занять время...")

        if fmt == "MP3":
            if file_path.lower().endswith((".mp3", ".wav", ".ogg")):
                await convert_audio_ffmpeg_async(file_path, out_file)
            else:
                clip = VideoFileClip(file_path)
                clip.audio.write_audiofile(out_file, verbose=False, logger=None)
                clip.close()

        elif fmt == "MP4":
            # Используем ffmpeg компрессию с агрессивными, но разумными настройками по умолчанию
            # Начинаем с CRF=28 и max_width=720 чтобы избежать резкого роста размера
            compressed = None
            try:
                await compress_video_ffmpeg_async(file_path, out_file, crf=30, max_width=640, audio_bitrate="64k", preset="fast")
            except Exception:
                # Если ffmpeg по какой-то причине недоступен, делаем fallback на moviepy,
                # но ставим ограничения (низкий fps/качество) чтобы не вырастал размер.
                try:
                    clip = VideoFileClip(file_path)
                    clip.write_videofile(out_file, codec="libx264", bitrate="600k", fps=24, audio=True, verbose=False, logger=None)
                    clip.close()
                except Exception:
                    # Если и это провалилось — отдаём ошибку ниже
                    pass

            # Если файл очень большой — пробуем итеративно сильнее сжимать
            if os.path.exists(out_file) and os.path.getsize(out_file) > (100 * 1024 * 1024):
                    await message.answer("⚠️ Файл слишком большой, пробую сильнее сжать...")
                    for crf in (34, 38, 42):
                        temp_comp = out_file + f".c{crf}.mp4"
                        try:
                            await compress_video_ffmpeg_async(file_path, temp_comp, crf=crf, max_width=640, audio_bitrate="64k", preset="slow")
                            if os.path.getsize(temp_comp) <= (100 * 1024 * 1024):
                                compressed = temp_comp
                                break
                            else:
                                os.remove(temp_comp)
                        except Exception:
                            try:
                                if os.path.exists(temp_comp):
                                    os.remove(temp_comp)
                            except Exception:
                                pass
                            continue

                    if compressed:
                        try:
                            if os.path.exists(out_file):
                                os.remove(out_file)
                        except Exception:
                            pass
                        out_file = compressed
                    else:
                        # Попробуем загрузить оригинал/сконвертированный файл на внешний хостинг и отправить ссылку
                        try:
                            upload_target = out_file if os.path.exists(out_file) else file_path
                            link = await upload_file_fallback(upload_target)
                            await message.answer(f"Файл слишком большой для отправки через Telegram, загрузил на временный хостинг: {link}")
                        except Exception:
                            await message.answer("❌ Невозможно сжать видео до допустимого размера и загрузить его. Попробуйте отправить меньший файл.")
                        return

        elif fmt == "GIF":
            # Используем ffmpeg palettegen/paletteuse для компактного GIF
            try:
                await convert_video_to_gif_ffmpeg_async(file_path, out_file, width=480, fps=12)
            except Exception:
                # fallback: moviepy (медленнее и может дать большой файл)
                clip = VideoFileClip(file_path)
                clip.write_gif(out_file, fps=12)
                clip.close()

        elif fmt == "PNG → JPG" or fmt == "PNG → JPEG":
            target_ext = "jpg" if fmt == "PNG → JPG" else "jpeg"
            out_file = os.path.join(temp_dir, f"{os.path.splitext(file_obj.file_name)[0]}.{target_ext}")
            try:
                from PIL import Image
                img = Image.open(file_path)
                # Convert RGBA to RGB if needed
                if img.mode in ("RGBA", "LA", "P"):
                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "RGBA":
                        rgb_img.paste(img, mask=img.split()[-1])
                    else:
                        rgb_img.paste(img)
                    rgb_img.save(out_file, "JPEG", quality=90)
                else:
                    img.save(out_file, "JPEG", quality=90)
            except Exception as e:
                await message.answer(f"❌ Ошибка при конвертации PNG: {str(e)}")
                return

        elif fmt == "PDF → PNG":
            if fitz is None:
                await message.answer("❌ Для работы с PDF установите PyMuPDF: `pip install PyMuPDF`")
                return

            doc = fitz.open(file_path)
            # Конвертируем первую страницу PDF в PNG
            if len(doc) > 0:
                page = doc[0]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                out_file = os.path.join(temp_dir, f"{os.path.splitext(file_obj.file_name)[0]}.png")
                pix.save(out_file)
            else:
                await message.answer("❌ PDF файл пуст.")
                return

        elif fmt == "PDF → ZIP":
            if fitz is None:
                await message.answer("❌ Для работы с PDF установите PyMuPDF: `pip install PyMuPDF`")
                return

            doc = fitz.open(file_path)
            out_file = os.path.join(temp_dir, f"{os.path.splitext(file_obj.file_name)[0]}.zip")
            with zipfile.ZipFile(out_file, "w") as zipf:
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_name = f"page_{i + 1}.png"
                    img_path = os.path.join(temp_dir, img_name)
                    pix.save(img_path)
                    zipf.write(img_path, img_name)
                    os.remove(img_path)

        elif fmt == "TXT":
            # Простая конвертация: пытаемся прочитать файл как текст
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                out_file = os.path.join(temp_dir, f"{os.path.splitext(file_obj.file_name)[0]}.txt")
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                await message.answer(f"❌ Ошибка при конвертации в TXT: {str(e)}")
                return

        await message.answer_document(types.FSInputFile(out_file), caption=f"✅ Файл конвертирован в {fmt}")

    except Exception:
        await message.answer(f"❌ Ошибка конвертации:\n<code>{traceback.format_exc()}</code>", parse_mode="HTML")

    finally:
        for path in (file_path, out_file):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

@admin_router.message(F.text == "⬅️ Назад", FormatStates.waiting_file)
@admin_router.message(F.text == "⬅️ Назад", FormatStates.waiting_format)
async def back_from_formats(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id

    # 🔍 Проверяем, админ ли пользователь
    if await is_admin(user_id) or await is_super_admin(user_id):
        kb = await admin_main_kb(user_id)
        text = "↩️ Возврат в главное меню админки."
    else:
        # 💡 Обычному пользователю — его клавиатура
        kb = main_menu_kb()
        text = "↩️ Возврат в главное меню."

    await message.answer(text, reply_markup=kb)

# ------------------------------
# 💉 FIX: Закрытие админки (исправлено)
# ------------------------------
@admin_router.message(F.text == "⬅️ Закрыть админку")
async def close_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Админка закрыта ✅", reply_markup=main_menu_kb())
    await log_action(message.from_user.id, "Закрыл админку")

