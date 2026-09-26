import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.bot import DefaultBotProperties
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from data_base.db import init_db, init_super_admin, log_system_event
from data_base.backup import backup_database
from handlers.user_privatka import user_privatka_router
from handlers.adminka import admin_router
from handlers.user_reply import user_reply_router
from middlewares.logging_middleware import LoggingMiddleware
from handlers.formats import formats_router
from handlers.logs_router import logs_router
from handlers.tickets import ticket_router
from handlers.conversions import conversion_router
from utiles.conversion_history import cleanup_expired_files

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
dp = Dispatcher(storage=MemoryStorage())
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
                print(f"Очищено временных файлов: {removed}")
        except Exception as exc:
            print(f"Ошибка очистки временных файлов: {type(exc).__name__}")


async def backup_loop():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            await backup_database()
        except Exception as exc:
            print(f"Ошибка резервного копирования БД: {type(exc).__name__}")

# --------------------------
# Настройка команд бота
# --------------------------
async def setup_commands():
    # Убираем все команды из меню слева
    await bot.set_my_commands(commands=[], scope=None)
    await bot.set_my_commands(commands=[], scope=types.BotCommandScopeAllPrivateChats())

# --------------------------
# Корректная остановка бота
# --------------------------
async def shutdown():
    try:
        await log_system_event("Бот завершает работу")
    except Exception:
        pass
    await bot.session.close()
    print("\033[91mБот остановлен\033[0m")

# --------------------------
# Основная функция запуска
# --------------------------
async def main():
    await setup_database()
    try:
        await cleanup_expired_files()
        await backup_database()
    except Exception as exc:
        print(f"Начальное обслуживание завершилось с ошибкой: {type(exc).__name__}")
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()

    # Логируем запуск
    await log_system_event("Бот запущен системой")
    print("\033[92mБот запущен\033[0m")

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
