import os
import traceback
import tempfile
from aiogram import types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest
import asyncio
import time
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import FSInputFile
from data_base import db
from data_base.db import add_user, log_action, is_admin, is_super_admin
from kbds import reply
from utils import answer_editable
from converter_service import file_converter

user_privatka_router = Router()
user_privatka_router.message.filter(lambda message: message.chat.type == "private")

# ------------------------------
# FSM состояния
# ------------------------------
class MenuStates(StatesGroup):
    main = State()
    about = State()
    payment = State()
    waiting_file = State()
    waiting_format = State()

# Удалены неиспользуемые состояния и дублирование

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

# Универсальная смена состояния удалена - больше не используется

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
# Универсальная кнопка "Назад"
# ------------------------------
@user_privatka_router.message(F.text.in_(["⬅️ Назад в меню", "назад"]))
async def back_handler(message: types.Message, state: FSMContext):
    """Универсальный обработчик кнопки 'Назад' для всех состояний."""
    user_id = message.from_user.id
    await log_action(user_id, f"Нажал кнопку 'Назад' ({message.text})")
    
    data = await state.get_data()
    file_path = data.get("file_path")
    
    # Очищаем временный файл если он есть
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass
    
    # Очищаем state
    await state.clear()
    
    # Возвращаемся в главное меню
    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(message, "↩️ Возврат в главное меню", reply_markup=kb)

# ------------------------------
# Отправка файла
# ------------------------------
@user_privatka_router.message(F.text == "📎 Отправить файл")
async def send_file_handler(message: types.Message, state: FSMContext):
    """Запрашивает у пользователя отправку файла для конвертации."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '📎 Отправить файл'")
    await state.set_state(MenuStates.waiting_file)
    await answer_editable(message, "📎 Отправьте файл для конвертации:\n\n📋 Поддерживаемые форматы:\n• Аудио: MP3, WAV, OGG\n• Видео: MP4, MOV, GIF\n• Изображения: JPG, JPEG, PNG\n• Документы: PDF, TXT, DOCX, MD\n\n🔒 Максимальный размер: 20 МБ", reply_markup=reply.file_menu_kb())

# ------------------------------
# Кнопка "О боте"
# ------------------------------
@user_privatka_router.message(F.text == "О боте")
async def about_bot_handler(message: types.Message, state: FSMContext):
    """Показывает информацию о боте."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'О боте'")
    await state.set_state(MenuStates.about)
    kb = await build_dynamic_keyboard(user_id)
    
    about_text = """ℹ️ **О боте**

Я - бот для конвертации файлов различных форматов. 

🎯 **Возможности:**
• Конвертация аудио (MP3, WAV, OGG)
• Конвертация видео (MP4, MOV → MP3, GIF)
• Обработка изображений (PNG ↔ JPG/JPEG)
• Работа с документами (PDF → PNG, PDF → ZIP, TXT)

📋 **Поддерживаемые форматы:**
• Аудио: MP3, WAV, OGG
• Видео: MP4, MOV, GIF
• Изображения: JPG, JPEG, PNG
• Документы: PDF, TXT, DOCX, MD

💡 **Как использовать:**
1. Отправьте файл боту
2. Выберите нужный формат
3. Получите конвертированный файл

🔒 Максимальный размер файла: 20 МБ
📧 Поддержка: через команду /support"""
    
    await answer_editable(message, about_text, parse_mode="Markdown", reply_markup=kb)

# ------------------------------
# Кнопка "Меню"
# ------------------------------
@user_privatka_router.message(F.text == "Меню")
async def menu_handler(message: types.Message, state: FSMContext):
    """Показывает главное меню."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'Меню'")
    await state.set_state(MenuStates.main)
    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(message, "📋 Главное меню 👇", reply_markup=kb)
    
# ------------------------------
# Кнопка "Моя роль"
# ------------------------------
@user_privatka_router.message(F.text == "Моя роль")
async def my_role_handler(message: types.Message, state: FSMContext):
    """Показывает роль пользователя."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'Моя роль'")
    
    role = "👤 Пользователь"
    if await is_super_admin(user_id):
        role = "👑 Супер-администратор"
    elif await is_admin(user_id):
        role = "🛡️ Администратор"
    
    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(message, f"Ваша роль: {role}", reply_markup=kb)
    
# ------------------------------
# Кнопка "Вариант оплаты"
# ------------------------------
@user_privatka_router.message(F.text == "Вариант оплаты")
async def payment_option_handler(message: types.Message, state: FSMContext):
    """Показывает информацию о вариантах оплаты."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'Вариант оплаты'")
    await state.set_state(MenuStates.payment)
    kb = await build_dynamic_keyboard(user_id)
    
    payment_text = """💳 **Варианты оплаты**

🔄 **Бесплатные конвертации:**
• До 5 конвертаций в день - бесплатно
• Все основные форматы доступны
• Максимальный размер файла: 20 МБ

💎 **Премиум подписка:**
• Безлимитные конвертации
• Файлы до 100 МБ
• Приоритетная обработка
• Расширенные форматы

💰 **Стоимость:**
• 1 месяц: 299 ₽
• 3 месяца: 799 ₽ (экономия 100 ₽)
• 1 год: 2999 ₽ (экономия 600 ₽)

📱 **Способы оплаты:**
• Банковские карты
• QIWI
• ЮMoney
• Криптовалюты

Для оформления подписки обратитесь к администратору."""
    
    await answer_editable(message, payment_text, parse_mode="Markdown", reply_markup=kb)

# ------------------------------
# Выбор формата
# ------------------------------
@user_privatka_router.message(F.text.lower() == "форматы")
async def choose_format_handler(message: types.Message, state: FSMContext):
    """Запускает процесс выбора формата конвертации."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '🎞 Форматы'")
    
    data = await state.get_data()
    file_path = data.get("file_path")
    
    if not file_path or not os.path.exists(file_path):
        await answer_editable(message, "⚠️ Сначала отправьте файл для конвертации!", reply_markup=reply.file_menu_kb())
        # Возвращаем в главное меню после показа предупреждения
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "📋 Главное меню 👇", reply_markup=kb)
        return

    await state.set_state(MenuStates.waiting_format)
    await answer_editable(message, "🎞 Выберите формат для конвертации:", reply_markup=reply.format_choice_kb())

# ------------------------------
# Получение файла
# ------------------------------
@user_privatka_router.message(F.document)
async def handle_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, f"Отправил файл: {message.document.file_name}")
    
    file = message.document
    file_name = file.file_name
    
    # Безопасное именование файла - убираем небезопасные символы
    import re
    import time
    safe_file_name = re.sub(r'[^\w\-_.]', '_', file_name)
    timestamp = int(time.time())
    file_path = f"downloads/{user_id}_{timestamp}_{safe_file_name}"

    # Максимальный размер входящего файла: 20 МБ (супер-админам без ограничения)
    MAX_INPUT_SIZE = 20 * 1024 * 1024
    file_size = getattr(file, "file_size", 0)

    if file_size > MAX_INPUT_SIZE:
        if await is_super_admin(user_id):
            await answer_editable(message, f"⚠️ Ограничение входящего размера ({MAX_INPUT_SIZE // (1024*1024)} МБ) не применяется к супер-админу. Продолжаю загрузку.")
        else:
            await answer_editable(message, f"🚫 Входящий файл слишком большой ({file_size // (1024*1024)} МБ). Максимум: {MAX_INPUT_SIZE // (1024*1024)} МБ.")
            # Возвращаем в главное меню
            await state.set_state(MenuStates.main)
            kb = await build_dynamic_keyboard(user_id)
            await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
            return

    os.makedirs("downloads", exist_ok=True)

    try:
        await answer_editable(message, "⏳ Скачиваю файл...")
        
        # Оптимизированное скачивание с таймаутом
        file_info = await message.bot.get_file(file.file_id)
        
        # Проверяем размер файла более точно
        actual_size = file_info.file_size or file_size
        if actual_size > MAX_INPUT_SIZE and not await is_super_admin(user_id):
            await answer_editable(message, f"🚫 Файл слишком большой ({actual_size // (1024*1024)} МБ). Максимум: {MAX_INPUT_SIZE // (1024*1024)} МБ.")
            # Возвращаем в главное меню
            await state.set_state(MenuStates.main)
            kb = await build_dynamic_keyboard(user_id)
            await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
            return
        
        # Скачиваем с оптимизированными параметрами
        await message.bot.download_file(
            file_info.file_path, 
            destination=file_path,
            timeout=300  # 5 минут таймаут
        )
        
        # Проверяем, что файл действительно скачался
        if not os.path.exists(file_path):
            raise Exception("Файл не был скачан")
            
    except TelegramBadRequest as e:
        msg = str(e)
        if "file is too big" in msg or "too big" in msg.lower():
            await answer_editable(message, "❌ Не могу скачать этот файл: Telegram отклонил запрос (файл слишком большой для бота).")
        else:
            await answer_editable(message, f"❌ Ошибка при скачивании: {msg}")
        # Возвращаем в главное меню
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return
    except asyncio.TimeoutError:
        await answer_editable(message, "❌ Таймаут при скачивании файла. Попробуйте меньший файл.")
        # Возвращаем в главное меню
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return
    except Exception as e:
        await answer_editable(message, f"❌ Ошибка при скачивании файла: {str(e)}")
        # Возвращаем в главное меню
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return

    await state.update_data(file_path=file_path)
    await state.set_state(MenuStates.waiting_format)
    
    # Показываем сообщение с клавиатурой форматов
    await answer_editable(
        message, 
        f"📁 Файл '{file_name}' загружен!\n\n🎞 Теперь выберите формат для конвертации:", 
        reply_markup=reply.format_choice_kb()
    )

# ------------------------------
# Выбор формата после загрузки файла
# ------------------------------
@user_privatka_router.message(MenuStates.waiting_format, F.text.in_({"MP3", "MP4", "GIF", "PDF → PNG", "PDF → ZIP", "PNG → JPG", "PNG → JPEG", "TXT"}))
async def user_format_selected(message: types.Message, state: FSMContext):
    """Обработка выбранного формата конвертации."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    # Дополнительная проверка состояния
    if current_state != MenuStates.waiting_format:
        await answer_editable(message, "⚠️ Неожиданное состояние. Возвращаю в главное меню.")
        await state.clear()
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "📋 Главное меню 👇", reply_markup=kb)
        return

    await log_action(user_id, f"Выбрал формат конвертации: {message.text}")
    
    data = await state.get_data()
    file_path = data.get("file_path")
    
    if not file_path or not os.path.exists(file_path):
        await answer_editable(message, "❌ Файл не найден. Отправьте его заново.")
        # Возвращаем в главное меню
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return

    fmt = message.text
    output_path = None
    
    try:
        await answer_editable(message, "⏳ Конвертирую файл, это может занять время...")
        
        # Используем централизованный сервис конвертации
        output_path = file_converter.convert_file(file_path, fmt)
        
        # Отправляем результат пользователю
        await message.answer_document(FSInputFile(output_path), caption=f"✅ Файл конвертирован в {fmt}")
        await log_action(user_id, f"Конвертировал файл в формат {fmt}")

    except Exception as e:
        # Логируем ошибку
        tb = traceback.format_exc()
        os.makedirs("logs", exist_ok=True)
        log_file = os.path.join("logs", f"convert_error_{int(time.time())}.log")
        try:
            with open(log_file, "w", encoding="utf-8") as lf:
                lf.write(tb)
        except Exception:
            pass
        
        # Сохраняем в базу данных
        try:
            await log_action(user_id, f"Ошибка конвертации, см. {log_file}")
        except Exception:
            pass
        
        # Уведомляем пользователя
        short = str(e)[:200]
        await answer_editable(message, f"❌ Ошибка конвертации: {short}\n(Полный лог сохранён)")
    
    finally:
        # Очищаем временные файлы
        file_converter.cleanup_files(file_path, output_path)
        
        # Возвращаем пользователя в главное меню с клавиатурой
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Конвертация завершена! Возвращаю в главное меню", reply_markup=kb)

# Удален дублирующий обработчик - теперь используется универсальный back_handler

# Кнопка "Платежи" — только свои записи

@user_privatka_router.message(F.text == "💳 Платежи")
async def show_payments_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '💳 Платежи'")
    
    try:
        payments = await db.get_user_payments(user_id)
    except Exception as e:
        await answer_editable(message, "⚠️ Ошибка при получении истории платежей.")
        print("get_user_payments error:", e)
        return

    if not payments:
        await answer_editable(message, "📭 У вас пока нет записей о платежах.")
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
    await answer_editable(message, text, reply_markup=kb)
