import os
import sys
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.bot import DefaultBotProperties
from dotenv import find_dotenv, load_dotenv
import signal

from data_base.db import init_db, init_super_admin, log_system_event
from handlers.user_privatka import user_privatka_router
from handlers.adminka import admin_router  # единый админский роутер с пагинацией и разделением прав
from common.bot_cmds_list import private
from middlewares.logging_middleware import LoggingMiddleware

load_dotenv(find_dotenv())

ALLOWED_UPDATES = ['message', 'edited_message']

# --- Инициализация бота с parse_mode через DefaultBotProperties ---
bot = Bot(
    token=os.getenv("TOKEN"),
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=MemoryStorage())

# --- Регистрируем middleware для логирования ---
dp.update.middleware(LoggingMiddleware())

# --- Роутеры ---
dp.include_router(user_privatka_router)
dp.include_router(admin_router)  # подключаем только один админский роутер

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))

# ==============================
# Настройка базы и супер-админа
# ==============================
async def setup_database():
    await init_db()
    await init_super_admin(SUPER_ADMIN_ID)

# ==============================
# Настройка команд
# ==============================
async def setup_commands():
    # Убираем все команды из меню слева для всех пользователей и приватных чатов
    await bot.set_my_commands(commands=[], scope=None)
    await bot.set_my_commands(commands=[], scope=types.BotCommandScopeAllPrivateChats())

# ==============================
# Основная функция
# ==============================
async def main():
    await setup_database()
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()
    
    # Логируем запуск бота
    await log_system_event("Бот запущен системой")

    # Вывод в терминал с цветом (зелёный)
    print("\033[92mБот запущен ✅\033[0m")

    # Обработчики сигналов для корректного логирования остановки
    def handle_exit(*args):
        asyncio.create_task(log_system_event("Бот завершил работу по сигналу"))
        # Чистое завершение процесса
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)   # Ctrl+C
    signal.signal(signal.SIGTERM, handle_exit)  # Сигнал от системы

    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        # Логируем обычную остановку
        await log_system_event("Бот остановлен системой")
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
