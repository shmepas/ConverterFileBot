import os
import traceback
import tempfile
import uuid
from aiogram import types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.event.bases import SkipHandler
import asyncio
import time
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import FSInputFile
from data_base import db
from data_base.db import add_user, log_action, is_admin, is_super_admin, check_user_limits, get_subscription_status_text
from kbds import reply
from utils import answer_editable
from converter_service import file_converter
from utiles.file_validator import file_validator
from utiles.progress_tracker import progress_tracker

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
# Динамическая клавиатура по роли (единообразная)
# ------------------------------
async def build_dynamic_keyboard(user_id: int, admin_open: bool = False) -> types.ReplyKeyboardMarkup:
    kb_builder = ReplyKeyboardBuilder()

    # Основные кнопки (все одинакового размера)
    main_buttons = [
        "📋 Меню",
        "ℹ️ О боте",
        "💰 Вариант оплаты",
        "🎞 Форматы",
        "💳 Платежи",
        "👤 Моя роль"
    ]

    # Добавляем основные кнопки по 2 в ряд для единообразия
    for i in range(0, len(main_buttons), 2):
        if i + 1 < len(main_buttons):
            kb_builder.row(
                types.KeyboardButton(text=main_buttons[i]),
                types.KeyboardButton(text=main_buttons[i + 1])
            )
        else:
            kb_builder.row(types.KeyboardButton(text=main_buttons[i]))

    # Админские кнопки (только для админов)
    if await is_admin(user_id) or await is_super_admin(user_id):
        if admin_open:
            # Админские функции
            if await is_super_admin(user_id):
                # Супер-админ видит все функции
                kb_builder.row(
                    types.KeyboardButton(text="📜 Просмотр логов"),
                    types.KeyboardButton(text="💎 Управление подписками")
                )
                kb_builder.row(
                    types.KeyboardButton(text="➕ Добавить админа"),
                    types.KeyboardButton(text="➖ Удалить админа")
                )
            else:
                # Обычный админ видит только просмотр логов
                kb_builder.row(
                    types.KeyboardButton(text="📜 Просмотр логов"),
                    types.KeyboardButton(text="💎 Просмотр подписок")
                )
            kb_builder.row(types.KeyboardButton(text="⬅️ Закрыть админку"))
        else:
            # Кнопка открытия админки
            kb_builder.row(types.KeyboardButton(text="🔧 Админка"))

    # Контакт для связи при проблемах (для всех)
    kb_builder.row(types.KeyboardButton(text="🆘 Связь с разработчиком"))

    return kb_builder.as_markup(resize_keyboard=True)

# Универсальная смена состояния удалена - больше не используется

# ------------------------------
# /start
# ------------------------------
@user_privatka_router.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()
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
# /reload - Перезагрузка кода (только для супер-админа)
# ------------------------------
@user_privatka_router.message(Command("reload"))
async def reload_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Проверяем права супер-админа
    if not await is_super_admin(user_id):
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return

    await log_action(user_id, "Выполнил команду /reload")

    try:
        # Импортируем модули для перезагрузки
        import importlib
        import sys

        # Список модулей для перезагрузки
        modules_to_reload = [
            'handlers.user_privatka',
            'handlers.adminka',
            'handlers.user_reply',
            'handlers.formats',
            'converter_service',
            'utils',
            'data_base.db'
        ]

        reloaded_modules = []

        for module_name in modules_to_reload:
            if module_name in sys.modules:
                try:
                    module = sys.modules[module_name]
                    importlib.reload(module)
                    reloaded_modules.append(module_name)
                    print(f"🔄 Перезагружен модуль: {module_name}")
                except Exception as e:
                    print(f"❌ Ошибка при перезагрузке {module_name}: {e}")

        success_msg = f"""✅ **Код успешно перезагружен!**

🔄 Перезагружено модулей: {len(reloaded_modules)}

📋 Перезагруженные модули:
{chr(10).join(f"• {mod}" for mod in reloaded_modules)}

🚀 Теперь бот использует обновленный код!"""

        await message.answer(success_msg, parse_mode="Markdown")

    except Exception as e:
        error_msg = f"""❌ **Ошибка при перезагрузке кода**

🔍 Ошибка: {str(e)}

💡 Возможно, нужно перезапустить бота вручную."""
        await message.answer(error_msg, parse_mode="Markdown")

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
    await state.set_state(MenuStates.main)  # Возвращаемся в главное меню
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
@user_privatka_router.message(F.text.in_(["📋 Меню", "Меню"]))
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
@user_privatka_router.message(F.text.in_(["👤 Моя роль", "Моя роль"]))
async def my_role_handler(message: types.Message, state: FSMContext):
    """Показывает роль пользователя и статус подписки."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'Моя роль'")

    # Определяем роль
    role = "👤 Пользователь"
    if await is_super_admin(user_id):
        role = "👑 Супер-администратор"
    elif await is_admin(user_id):
        role = "🛡️ Администратор"

    # Получаем статус подписки
    subscription_text = await get_subscription_status_text(user_id)

    # Формируем полное сообщение
    full_text = f"""**Ваша роль:** {role}

{subscription_text}"""

    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(message, full_text, parse_mode="Markdown", reply_markup=kb)

# ------------------------------
# Кнопка "Вариант оплаты"
# ------------------------------
@user_privatka_router.message(F.text.in_(["💰 Вариант оплаты", "Вариант оплаты"]))
async def payment_option_handler(message: types.Message, state: FSMContext):
    """Показывает информацию о вариантах оплаты."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'Вариант оплаты'")
    await state.set_state(MenuStates.payment)
    kb = await build_dynamic_keyboard(user_id)

    payment_text = """💳 **💎 Премиум подписка - Расширьте возможности!**

🔄 **Ваш текущий тариф - БЕСПЛАТНЫЙ:**
• ✅ 5 конвертаций в день
• ✅ Основные форматы: MP3, MP4, GIF, TXT, PDF, PNG, JPG
• ✅ Максимальный размер файла: 20 МБ

💎 **Премиум подписка - ВСЕ возможности:**
• 🚀 **Безлимитные конвертации** - конвертируйте сколько угодно!
• 📁 **Большие файлы до 100 МБ** - работайте с любыми проектами
• ⚡ **Приоритетная обработка** - ваши файлы обрабатываются первыми
• 🎯 **Все форматы без ограничений**
• 🔧 **Расширенные функции** - доступ к новым возможностям
• 💬 **Приоритетная поддержка**

💰 **Выгодные тарифы:**
┌─ **1 месяц:** 299 ₽
├─ **3 месяца:** 799 ₽ *(экономия 100 ₽)*
└─ **1 год:** 2999 ₽ *(экономия 600 ₽)*

📱 **Способы оплаты:**
💳 Банковские карты • 💰 QIWI • 🟡 ЮMoney • ₿ Криптовалюты

🎯 **Готово к покупке?**
Для оформления подписки нажмите "🆘 Связь с разработчиком" и напишите @claperonn

⏰ **Специальное предложение:** Первый месяц со скидкой 20%!"""

    await answer_editable(message, payment_text, parse_mode="Markdown", reply_markup=kb)

# ------------------------------
# Кнопка "Админка"
# ------------------------------
@user_privatka_router.message(F.text.in_(["🔧 Админка", "Админка"]))
async def admin_panel_handler(message: types.Message, state: FSMContext):
    """Открывает админскую панель для админов."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'Админка'")

    # Проверяем права администратора
    if not (await is_admin(user_id) or await is_super_admin(user_id)):
        await answer_editable(message, "❌ У вас нет прав для доступа к админской панели.")
        return

    # Показываем админскую панель
    await state.clear()
    from handlers.adminka import admin_main_kb
    kb = await admin_main_kb(user_id)
    await answer_editable(message, "🔧 Админская панель открыта 👇", reply_markup=kb)

# ------------------------------
# Кнопка "Закрыть админку"
# ------------------------------
@user_privatka_router.message(F.text.in_(["⬅️ Закрыть админку", "Закрыть админку"]))
async def close_admin_panel_handler(message: types.Message, state: FSMContext):
    """Закрывает админскую панель."""
    user_id = message.from_user.id
    await state.clear()
    await log_action(user_id, "Нажал кнопку 'Закрыть админку'")

    # Проверяем права администратора
    if not (await is_admin(user_id) or await is_super_admin(user_id)):
        await answer_editable(message, "❌ У вас нет прав для доступа к админской панели.")
        return

    # Закрываем админскую панель и возвращаемся в главное меню
    await state.set_state(MenuStates.main)
    kb = await build_dynamic_keyboard(user_id, admin_open=False)
    await answer_editable(message, "✅ Админская панель закрыта", reply_markup=kb)

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
    if await state.get_state() == "FormatStates:waiting_file":
        # Let the admin conversion router consume files during its format flow.
        raise SkipHandler
    user_id = message.from_user.id
    print(f"[USER_PRIVATKA DEBUG] 📁 DOCUMENT RECEIVED via user_privatka.py for user {user_id}")
    print(f"[USER_PRIVATKA DEBUG] File name: {message.document.file_name}")
    print(f"[USER_PRIVATKA DEBUG] File size: {message.document.file_size}")
    print(f"[USER_PRIVATKA DEBUG] Current state: {await state.get_state()}")
    print(f"[USER_PRIVATKA DEBUG] File type: {message.document.mime_type}")

    await log_action(user_id, f"Отправил файл: {message.document.file_name}")

    file = message.document
    file_name = file.file_name

    # Валидация имени файла
    if not file_validator.validate_filename(file_name):
        await answer_editable(message, "❌ Небезопасное имя файла. Попробуйте переименовать файл.")
        return

    # Безопасное именование файла
    safe_file_name = file_validator.get_safe_filename(file_name, user_id)
    timestamp = int(time.time())
    file_path = os.path.join("downloads", f"{user_id}_{timestamp}_{uuid.uuid4().hex}_{safe_file_name}")

    # Respect the current subscription limit before downloading the file.
    limits = await db.check_user_limits(user_id)
    max_input_size = limits["max_file_size"]
    file_size = getattr(file, "file_size", 0)

    if file_size > max_input_size:
        await answer_editable(message, f"🚫 Файл слишком большой ({file_size // (1024*1024)} МБ). Лимит вашего тарифа: {max_input_size // (1024*1024)} МБ.")
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
        if actual_size > max_input_size:
            await answer_editable(message, f"🚫 Файл слишком большой ({actual_size // (1024*1024)} МБ). Лимит вашего тарифа: {max_input_size // (1024*1024)} МБ.")
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

        # Валидация загруженного файла
        print(f"🔍 Валидация загруженного файла: {file_path}")

        # Определяем ожидаемый формат на основе MIME-типа
        mime_type = file.mime_type
        expected_format = "UNKNOWN"

        if mime_type.startswith("audio/"):
            expected_format = "MP3"
        elif mime_type.startswith("video/"):
            expected_format = "MP4"
        elif mime_type == "image/gif":
            expected_format = "GIF"
        elif mime_type == "image/png":
            expected_format = "PNG"
        elif mime_type in ["image/jpeg", "image/jpg"]:
            expected_format = "JPG"
        elif mime_type == "application/pdf":
            expected_format = "PDF"
        elif mime_type in ["text/plain", "text/markdown"]:
            expected_format = "TXT"

        # Проводим валидацию исходного файла (не формата конвертации)
        is_valid, error_message, validation_info = file_validator.full_validation(file_path, expected_format, user_id)

        if not is_valid:
            await answer_editable(message, f"❌ Файл не прошел валидацию: {error_message}")
            # Удаляем невалидный файл
            try:
                os.remove(file_path)
            except:
                pass
            return

        print(f"✅ Файл прошел валидацию (время: {validation_info['validation_time']:.2f}с)")

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
    except Exception:
        await answer_editable(message, "❌ Не удалось скачать или проверить файл. Попробуйте отправить его ещё раз.")
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
async def user_format_selected_debug(message: types.Message, state: FSMContext):
    """Обработка выбранного формата конвертации с проверкой лимитов - DEBUG VERSION."""
    user_id = message.from_user.id
    print(f"[USER_PRIVATKA DEBUG] 🎯 FORMAT SELECTED CALLED via user_privatka.py for user {user_id}")
    print(f"[USER_PRIVATKA DEBUG] Selected format: {message.text}")
    print(f"[USER_PRIVATKA DEBUG] Current state: {await state.get_state()}")

    # Вызываем основную функцию
    await user_format_selected(message, state)
async def user_format_selected(message: types.Message, state: FSMContext):
    """Обработка выбранного формата конвертации с проверкой лимитов."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    selected_format = message.text

    print(f"[DEBUG MP3] user_format_selected called for user {user_id}, format: {selected_format}")

    # Дополнительная проверка состояния
    if current_state != MenuStates.waiting_format:
        print(f"[DEBUG MP3] Unexpected state: {current_state}, expected: {MenuStates.waiting_format}")
        await answer_editable(message, "⚠️ Неожиданное состояние. Возвращаю в главное меню.")
        await state.clear()
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "📋 Главное меню 👇", reply_markup=kb)
        return

    print(f"[DEBUG MP3] Starting conversion process for format: {selected_format}")
    await log_action(user_id, f"Выбрал формат конвертации: {selected_format}")

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

    # ПРОВЕРКА ЛИМИТОВ ПОДПИСКИ
    limits = await db.check_user_limits(user_id)

    # Проверяем лимит конвертаций для бесплатных пользователей
    if not limits['is_premium']:
        print(f"[DEBUG MP3] User {user_id} is free user, checking limits for format {fmt}")
        print(f"[DEBUG MP3] Current limits: {limits}")

        if not await db.increment_conversion_count(user_id):
            # Лимит исчерпан - предлагаем купить подписку
            limit_message = f"""🚫 **Дневной лимит исчерпан!**

📊 **Ваш статус:**
• Использовано сегодня: {limits['current_count']}/{limits['daily_limit']} конвертаций
• Осталось: 0 конвертаций

💎 **Хотите больше возможностей?**

✅ **Премиум подписка даёт:**
• 🚀 Безлимитные конвертации
• 📁 Файлы до 100 МБ (вместо 20 МБ)
• 🎯 Все форматы без ограничений
• ⚡ Приоритетная обработка
• 🔧 Расширенные функции

💰 **Стоимость:** от 299₽/месяц

Для покупки подписки нажмите "💰 Вариант оплаты" в меню 👇"""

            await answer_editable(message, limit_message, parse_mode="Markdown")
            await state.set_state(MenuStates.main)
            kb = await build_dynamic_keyboard(user_id)
            await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
            return
        else:
            print(f"[DEBUG MP3] Successfully incremented conversion count for user {user_id}")
    else:
        print(f"[DEBUG MP3] User {user_id} is premium user, no limits")

    # Проверяем размер файла в зависимости от подписки
    if os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
        if file_size > limits['max_file_size']:
            if not limits['is_premium']:
                await db.decrement_conversion_count(user_id)
            await answer_editable(message, f"❌ Файл слишком большой для вашего тарифа!\n\n📄 Размер файла: {file_size // (1024*1024)} МБ\n🎯 Лимит: {limits['max_file_size'] // (1024*1024)} МБ")
            await state.set_state(MenuStates.main)
            kb = await build_dynamic_keyboard(user_id)
            await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
            return

    output_path = None

    try:
        print(f"[DEBUG MP3] Starting conversion for user {user_id}, format: {fmt}")

        # Создаем прогресс-трекер для этой конвертации
        track_id = await progress_tracker.start_conversion_progress(user_id, message, fmt)

        # Создаем callback для обновления прогресса
        from utiles.progress_tracker import ConversionProgressCallback
        progress_callback = ConversionProgressCallback(user_id, progress_tracker)

        # Используем централизованный сервис конвертации с retry логикой и прогресс-баром
        print(f"[DEBUG MP3] Calling file_converter.convert_file_with_retry for format: {fmt}")
        output_path = await file_converter.convert_file_with_retry(file_path, fmt, user_id=user_id, progress_callback=progress_callback)
        print(f"[DEBUG MP3] Conversion completed, output path: {output_path}")

        # Отправляем результат пользователю
        print(f"[DEBUG MP3] Sending result to user {user_id}")
        await message.answer_document(FSInputFile(output_path), caption=f"✅ Файл конвертирован в {fmt}")
        await log_action(user_id, f"Конвертировал файл в формат {fmt}")
        print(f"[DEBUG MP3] Successfully sent result for user {user_id}, format: {fmt}")

        # Для бесплатных пользователей показываем информацию об оставшихся конвертациях
        if not limits['is_premium']:
            remaining = max(0, limits['daily_limit'] - await db.get_daily_conversion_count(user_id))
            if remaining >= 0:
                remaining_message = f"""📊 **Конвертация выполнена!**

✅ Файл успешно конвертирован в формат {fmt}

📈 **Ваш статус:**
• Осталось конвертаций на сегодня: {remaining}/5
• Использовано: {5 - remaining}/5

💎 **Хотите больше возможностей?**
Премиум подписка даёт безлимитные конвертации и файлы до 100 МБ!

Для покупки нажмите "💰 Вариант оплаты" в меню 👇"""
                await answer_editable(message, remaining_message, parse_mode="Markdown")

    except Exception as e:
        if not limits['is_premium']:
            try:
                await db.decrement_conversion_count(user_id)
            except Exception:
                pass
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
        # Возвращаем в главное меню при ошибке
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return

    # Получаем статус подписки
    subscription_text = await get_subscription_status_text(user_id)

    if not payments:
        text = f"{subscription_text}\n\n📭 У вас пока нет записей о платежах."
    else:
        lines = []
        for p in payments:
            date = p.get("date", "")
            amount = p.get("amount", 0)
            ptype = p.get("type", "")
            desc = p.get("description", "")
            sign = "+" if ptype.lower().startswith("пополн") else "-" if ptype.lower().startswith("спис") else ""
            lines.append(f"{date} — {sign}{amount} ₽ — {ptype} — {desc}")

        payments_text = "💳 Ваша история платежей:\n\n" + "\n".join(lines)
        text = f"{subscription_text}\n\n{payments_text}"

    # Возвращаемся в главное меню после показа платежей
    await state.set_state(MenuStates.main)
    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(message, text + "\n\n↩️ Возвращаю в главное меню", parse_mode="Markdown", reply_markup=kb)

# ------------------------------
# Связь с разработчиком
# ------------------------------
@user_privatka_router.message(F.text == "🆘 Связь с разработчиком")
async def contact_developer_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '🆘 Связь с разработчиком'")

    contact_text = """🆘 **Связь с разработчиком**

Если у вас возникли проблемы с ботом или есть предложения по улучшению, вы можете связаться со мной:

👤 **Telegram:** @claperonn

📝 **При обращении укажите:**
- Описание проблемы
- Ваш User ID: `{user_id}`
- Время возникновения проблемы

💬 Я постараюсь помочь вам в кратчайшие сроки!"""

    await state.set_state(MenuStates.main)
    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(message, contact_text.format(user_id=user_id), parse_mode="Markdown", reply_markup=kb)
