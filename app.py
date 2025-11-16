import os
import sys
import asyncio
import signal
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.bot import DefaultBotProperties
from dotenv import find_dotenv, load_dotenv

from data_base.db import init_db

from data_base.db import init_db, init_super_admin, log_system_event
from handlers.user_privatka import user_privatka_router
from handlers.adminka import admin_router
from handlers.user_reply import user_reply_router
from middlewares.logging_middleware import LoggingMiddleware
from handlers.formats import formats_router

load_dotenv(find_dotenv())

ALLOWED_UPDATES = ['message', 'edited_message']

# --------------------------
# Инициализация бота
# --------------------------
bot = Bot(
    token=os.getenv("TOKEN"),
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=MemoryStorage())
dp.update.middleware(LoggingMiddleware())

# --------------------------
# Подключаем роутеры
# --------------------------
dp.include_router(user_privatka_router)
dp.include_router(admin_router)
dp.include_router(formats_router)
dp.include_router(user_reply_router)

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))

# --------------------------
# Настройка базы и супер-админа
# --------------------------
async def setup_database():
    await init_db()
    await init_super_admin(SUPER_ADMIN_ID)

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
    print("\033[91mБот остановлен ✅\033[0m")
    # Завершаем процесс
    sys.exit(0)

def handle_exit(sig, frame):
    # Используем ensure_future чтобы создать таск внутри события loop
    loop = asyncio.get_event_loop()
    if loop.is_running():
        asyncio.ensure_future(shutdown())
    else:
        # Если loop уже закрыт (редко)
        sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# --------------------------
# Основная функция запуска
# --------------------------
async def main():
    init_db()
    await setup_database()
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()

    # Логируем запуск
    await log_system_event("Бот запущен системой")
    print("\033[92mБот запущен ✅\033[0m")

    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        # На всякий случай закрываем сессии
        await shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        # Корректное завершение при Ctrl+C или сигналах
        pass