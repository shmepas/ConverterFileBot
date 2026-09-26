import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from utiles.logging_config import configure_logging

configure_logging()

from data_base.db import init_db, init_super_admin, log_system_event
from data_base.backup import backup_database
from data_base.fsm_storage import KeyedEventIsolation, SQLiteFSMStorage
from handlers.user_privatka import user_privatka_router
from handlers.adminka import admin_router
from handlers.user_reply import user_reply_router
from middlewares.logging_middleware import LoggingMiddleware
from handlers.formats import formats_router
from handlers.logs_router import logs_router
from handlers.tickets import ticket_router
from handlers.conversions import conversion_router
from handlers.fallback import fallback_router
from utiles.conversion_history import cleanup_expired_files
import logging


logger = logging.getLogger(__name__)

ALLOWED_UPDATES = ['message', 'edited_message', 'callback_query']

TOKEN = os.getenv("TOKEN")
try:
    SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", ""))
except ValueError as exc:
    raise RuntimeError("SUPER_ADMIN_ID must be a valid Telegram user ID") from exc
if not TOKEN:
    raise RuntimeError("TOKEN is missing; configure it in the environment or .env file")

# --------------------------
# Инициализация бота
# --------------------------
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=SQLiteFSMStorage(), events_isolation=KeyedEventIsolation())
dp.update.middleware(LoggingMiddleware())

# --------------------------
# Подключаем роутеры
# --------------------------
dp.include_router(ticket_router)
dp.include_router(conversion_router)
dp.include_router(user_privatka_router)
dp.include_router(admin_router)
dp.include_router(formats_router)
dp.include_router(user_reply_router)
dp.include_router(logs_router)
dp.include_router(fallback_router)

# --------------------------
# Настройка базы и супер-админа
# --------------------------
async def setup_database():
    await init_db()
    await init_super_admin(SUPER_ADMIN_ID)


async def maintenance_loop():
    while True:
        await asyncio.sleep(60 * 60)
        try:
            removed = await cleanup_expired_files()
            if removed:
                logger.info("Expired temporary files removed: %s", removed)
        except Exception as exc:
            logger.exception("Expired-file maintenance failed")


async def backup_loop():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            backup_path = await backup_database()
            if backup_path is None:
                logger.error("Scheduled database backup was not created")
        except Exception as exc:
            logger.exception("Scheduled database backup failed")

# --------------------------
# Настройка команд бота
# --------------------------
async def setup_commands():
    commands = [
        types.BotCommand(command="start", description="Открыть главное меню"),
        types.BotCommand(command="menu", description="Выйти в главное меню"),
        types.BotCommand(command="help", description="Как пользоваться ботом"),
        types.BotCommand(command="cancel", description="Отменить текущий ввод"),
    ]
    await bot.set_my_commands(
        commands=commands,
        scope=types.BotCommandScopeAllPrivateChats(),
    )
    await bot.set_chat_menu_button(menu_button=types.MenuButtonCommands())

# --------------------------
# Корректная остановка бота
# --------------------------
async def shutdown():
    try:
        await log_system_event("Бот завершает работу")
    except Exception:
        pass
    await bot.session.close()
    logger.info("Bot stopped")

# --------------------------
# Основная функция запуска
# --------------------------
async def main():
    await setup_database()
    try:
        await cleanup_expired_files()
        await backup_database()
    except Exception as exc:
        logger.exception("Initial database maintenance failed")
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()

    # Логируем запуск
    await log_system_event("Бот запущен системой")
    logger.info("Bot started")

    maintenance_task = asyncio.create_task(maintenance_loop())
    backup_task = asyncio.create_task(backup_loop())
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        for task in (maintenance_task, backup_task):
            task.cancel()
        await asyncio.gather(maintenance_task, backup_task, return_exceptions=True)
        # На всякий случай закрываем сессии
        await shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        # Корректное завершение при Ctrl+C или сигналах
        pass
