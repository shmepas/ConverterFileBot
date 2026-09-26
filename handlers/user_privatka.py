import os
import logging
import traceback
import tempfile
import uuid
from aiogram import types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest
import asyncio
import time
from pathlib import Path
from html import escape
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import FSInputFile
from data_base import db
from data_base.db import add_user, log_action, is_admin, is_super_admin, check_user_limits, get_subscription_status_text
from kbds import reply
from utils import answer_editable
from converter_service import file_converter
from utiles.file_validator import file_validator
from utiles.progress_tracker import progress_tracker
from utiles.conversion_workflow import ConversionRejected, process_conversion

user_privatka_router = Router()
logger = logging.getLogger(__name__)
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
        "👤 Моя роль",
        "🎫 Создать тикет",
        "📨 Мои тикеты",
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
                kb_builder.row(types.KeyboardButton(text="🎫 Тикеты"))
            else:
                # Обычный админ видит только просмотр логов
                kb_builder.row(
                    types.KeyboardButton(text="📜 Просмотр логов"),
                    types.KeyboardButton(text="💎 Просмотр подписок")
                )
                kb_builder.row(types.KeyboardButton(text="🎫 Тикеты"))
            kb_builder.row(types.KeyboardButton(text="⬅️ Закрыть админку"))
        else:
            # Кнопка открытия админки
            kb_builder.row(types.KeyboardButton(text="🔧 Админка"))

    # Контакт для связи при проблемах (для всех)
    kb_builder.row(types.KeyboardButton(text="🆘 Связь с разработчиком"))

    return kb_builder.as_markup(resize_keyboard=True)

# Универсальная смена состояния удалена - больше не используется

def _remove_pending_upload(file_path: str | None) -> None:
    if not file_path:
        return
    downloads_root = Path("downloads")
    if downloads_root.is_symlink():
        return
    downloads_root = downloads_root.resolve()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        if candidate.is_symlink():
            return
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(downloads_root)
        if resolved.is_file():
            resolved.unlink()
    except (OSError, ValueError):
        return


async def _clear_user_flow(state: FSMContext) -> None:
    data = await state.get_data()
    _remove_pending_upload(data.get("file_path"))
    await state.clear()


# ------------------------------
# /start
# ------------------------------
@user_privatka_router.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await _clear_user_flow(state)
    await state.set_state(MenuStates.main)
    await add_user(user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)

    role = "Пользователь"
    if await is_super_admin(user_id):
        role = "Супер-админ"
    elif await is_admin(user_id):
        role = "Админ"

    await log_action(user_id, f"Команда /start ({role})")
    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(
        message,
        "Привет! 👋 Я помогу конвертировать файлы.\n"
        "Нажмите «🎞 Форматы» и отправьте файл — бот покажет доступные результаты.\n"
        "Если возникнет проблема, создайте тикет — поддержка ответит здесь, в боте.\n"
        f"Ваша роль: {role}.",
        reply_markup=kb,
    )
# ------------------------------
# /menu
# ------------------------------
@user_privatka_router.message(Command("menu"))
async def menu_cmd(message: types.Message, state: FSMContext):
    await _show_main_menu(message, state, "↩️ Главное меню 👇")


@user_privatka_router.message(Command("cancel"))
async def cancel_flow_cmd(message: types.Message, state: FSMContext):
    await _show_main_menu(message, state, "Текущий шаг отменён. Главное меню 👇")


async def _show_main_menu(message: types.Message, state: FSMContext, text: str) -> None:
    data = await state.get_data()
    return_to_admin = bool(data.get("return_to_admin"))
    await _clear_user_flow(state)
    await state.set_state(MenuStates.main)
    if return_to_admin:
        from handlers.adminka import admin_main_kb
        keyboard = await admin_main_kb(message.from_user.id)
    else:
        keyboard = await build_dynamic_keyboard(message.from_user.id)
    await answer_editable(message, text, reply_markup=keyboard)


@user_privatka_router.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "🤖 <b>Как пользоваться ботом</b>\n\n"
        "1. Нажмите «🎞 Форматы» и отправьте файл.\n"
        "2. Выберите один из доступных результатов.\n"
        "3. Получите готовый файл в чате.\n\n"
        "Для поддержки нажмите «🎫 Создать тикет»: ответ придёт сюда, "
        "без перехода к разработчику.\n\n"
        "/menu — выйти в главное меню\n"
        "/cancel — отменить текущий ввод"
    )
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
                    logger.debug("Reloaded module %s", module_name)
                except Exception as e:
                    logger.exception("Could not reload module %s", module_name)

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
@user_privatka_router.message(F.text.in_([
    "⬅️ Назад в меню", "📋 Меню", "Меню", "назад",
]))
async def back_handler(message: types.Message, state: FSMContext):
    """Return to the main menu and discard any unconverted temporary upload."""
    await log_action(message.from_user.id, f"Вернулся в меню ({message.text})")
    await _show_main_menu(message, state, "↩️ Возврат в главное меню 👇")

# ------------------------------
# Отправка файла
# ------------------------------
@user_privatka_router.message(F.text == "📎 Отправить файл")
async def send_file_handler(message: types.Message, state: FSMContext):
    await start_conversion_flow(message, state)

# ------------------------------
# Кнопка "О боте"
# ------------------------------
@user_privatka_router.message(F.text == "О боте")
async def about_bot_handler(message: types.Message, state: FSMContext):
    """Показывает информацию о боте."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'О боте'")
    await _clear_user_flow(state)
    await state.set_state(MenuStates.main)
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
🆘 Для помощи нажмите «🎫 Создать тикет» или напишите /help"""

    await answer_editable(message, about_text, parse_mode="Markdown", reply_markup=kb)

# ------------------------------
# Кнопка "Меню"
# ------------------------------
@user_privatka_router.message(F.text.in_(["📋 Меню", "Меню"]))
async def menu_handler(message: types.Message, state: FSMContext):
    """Показывает главное меню."""
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку 'Меню'")
    await _clear_user_flow(state)
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
Для оформления подписки нажмите "🆘 Связь с разработчиком" и напишите @elfiienlied

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
# Единый сценарий конвертации: сначала файл, затем доступные форматы
# ------------------------------
@user_privatka_router.message(F.text.in_({"🎞 Форматы", "Форматы"}))
async def start_conversion_flow(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await _clear_user_flow(state)
    await log_action(user_id, "Открыл единый сценарий конвертации")
    await state.update_data(return_to_admin=await is_admin(user_id) or await is_super_admin(user_id))
    await state.set_state(MenuStates.waiting_file)
    await answer_editable(
        message,
        "📎 Отправьте файл. Я проверю его и покажу только доступные форматы.\n"
        "Можно вернуться в меню кнопкой «⬅️ Назад в меню» или командой /cancel.",
        reply_markup=reply.file_menu_kb(),
    )

# ------------------------------
# Получение файла
# ------------------------------
@user_privatka_router.message(F.document | F.video | F.audio)
async def handle_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    file = message.document or message.video or message.audio
    if not file:
        return
    file_name = getattr(file, "file_name", None)
    if not file_name:
        file_name = f"upload_{file.file_unique_id}.bin"
    await log_action(user_id, f"Отправил файл для конвертации: {file_name}")

    # Валидация имени файла
    if not file_validator.validate_filename(file_name):
        await answer_editable(message, "❌ Небезопасное имя файла. Попробуйте переименовать файл.")
        return

    # Безопасное именование файла
    current_data = await state.get_data()
    _remove_pending_upload(current_data.get("file_path"))
    await state.update_data(file_path=None, source_name=None, source_type=None, available_formats=[])
    await state.set_state(MenuStates.waiting_file)
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
        logger.debug("Validating uploaded file for user_id=%s", user_id)

        detected_type = file_validator.detect_file_type(file_path)
        available_formats = file_validator.TARGET_FORMATS_BY_INPUT_TYPE.get(detected_type, [])
        if not available_formats:
            await answer_editable(message, "❌ Этот тип файла не поддерживается для конвертации.")
            os.remove(file_path)
            return

        canonical_path = os.path.splitext(file_path)[0] + file_validator.canonical_extension(file_path, detected_type)
        if canonical_path != file_path:
            os.replace(file_path, canonical_path)
            file_path = canonical_path

    except TelegramBadRequest as e:
        msg = str(e)
        if "file is too big" in msg or "too big" in msg.lower():
            await answer_editable(message, "❌ Не могу скачать этот файл: Telegram отклонил запрос (файл слишком большой для бота).")
        else:
            await answer_editable(message, f"❌ Ошибка при скачивании: {msg}")
        if os.path.exists(file_path):
            os.remove(file_path)
        # Возвращаем в главное меню
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return
    except asyncio.TimeoutError:
        await answer_editable(message, "❌ Таймаут при скачивании файла. Попробуйте меньший файл.")
        if os.path.exists(file_path):
            os.remove(file_path)
        # Возвращаем в главное меню
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return
    except Exception:
        await answer_editable(message, "❌ Не удалось скачать или проверить файл. Попробуйте отправить его ещё раз.")
        if os.path.exists(file_path):
            os.remove(file_path)
        # Возвращаем в главное меню
        await state.set_state(MenuStates.main)
        kb = await build_dynamic_keyboard(user_id)
        await answer_editable(message, "↩️ Возвращаю в главное меню", reply_markup=kb)
        return

    await state.update_data(
        file_path=file_path,
        source_name=file_name,
        source_type=detected_type,
        available_formats=available_formats,
    )
    await state.set_state(MenuStates.waiting_format)

    # Показываем сообщение с клавиатурой форматов
    await answer_editable(
        message,
        f"📁 Файл '{escape(file_name)}' загружен!\n\n🎞 Доступные форматы:",
        reply_markup=reply.format_choice_kb(available_formats)
    )

# ------------------------------
# Выбор формата после загрузки файла
# ------------------------------
@user_privatka_router.message(MenuStates.waiting_format, F.text.in_({"MP3", "MP4", "GIF", "PDF → PNG", "PDF → ZIP", "PNG → JPG", "PNG → JPEG", "TXT"}))
async def user_format_selected_handler(message: types.Message, state: FSMContext):
    """Dispatch a selected, file-compatible target format to the shared workflow."""
    await user_format_selected(message, state)
async def user_format_selected(message: types.Message, state: FSMContext):
    """Run a conversion using the shared bounded worker and history workflow."""
    user_id = message.from_user.id
    data = await state.get_data()
    file_path = data.get("file_path")
    source_name = data.get("source_name") or "uploaded_file"
    source_type = data.get("source_type") or "unknown"
    available_formats = data.get("available_formats") or []

    if await state.get_state() != MenuStates.waiting_format:
        await answer_editable(message, "⚠️ Выбор формата устарел. Отправьте файл заново.")
        await state.clear()
        return
    if message.text not in available_formats:
        await answer_editable(message, "⚠️ Выберите один из форматов, показанных для этого файла.")
        return
    if not file_path or not os.path.isfile(file_path):
        await answer_editable(message, "❌ Файл не найден. Отправьте его заново.")
        await state.set_state(MenuStates.main)
        await answer_editable(message, "Главное меню 👇", reply_markup=await build_dynamic_keyboard(user_id))
        return

    await log_action(user_id, f"Выбрал формат конвертации: {message.text}")
    try:
        await process_conversion(
            message, state, user_id, file_path, source_name,
            source_type, message.text,
            charge_quota=not (await is_admin(user_id) or await is_super_admin(user_id)),
        )
    except ConversionRejected as exc:
        await answer_editable(message, str(exc))
    finally:
        file_converter.cleanup_files(file_path)
        return_to_admin = bool((await state.get_data()).get("return_to_admin"))
        await state.clear()
        await state.set_state(MenuStates.main)
        if return_to_admin:
            from handlers.adminka import admin_main_kb
            keyboard = await admin_main_kb(user_id)
        else:
            keyboard = await build_dynamic_keyboard(user_id)
        await answer_editable(
            message, "↩️ Возвращаю в главное меню",
            reply_markup=keyboard,
        )

# Удален дублирующий обработчик - теперь используется универсальный back_handler

# Кнопка "Платежи" — только свои записи

@user_privatka_router.message(F.text == "💳 Платежи")
async def show_payments_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await log_action(user_id, "Нажал кнопку '💳 Платежи'")

    try:
        payments = await db.get_user_payments(user_id)
    except Exception:
        await answer_editable(message, "⚠️ Ошибка при получении истории платежей.")
        logger.exception("Could not read payment history for user_id=%s", user_id)
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

Если у вас возникли проблемы с ботом или есть предложения, создайте тикет кнопкой «🎫 Создать тикет» — я отвечу здесь, в боте. Также можно связаться со мной напрямую:

👤 **Telegram:** @elfiienlied

📝 **При обращении укажите:**
- Описание проблемы
- Ваш User ID: `{user_id}`
- Время возникновения проблемы

💬 Я постараюсь помочь вам в кратчайшие сроки!"""

    await state.set_state(MenuStates.main)
    kb = await build_dynamic_keyboard(user_id)
    await answer_editable(message, contact_text.format(user_id=user_id), parse_mode="Markdown", reply_markup=kb)
