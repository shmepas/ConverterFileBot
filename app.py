import asyncio
import os

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage

from data_base.db import (
    init_db,
    add_user,
    log_action,
    is_admin,
    is_super_admin,
    init_super_admin
)

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

from handlers.user_privatka import user_privatka_router
from handlers.adminka import admin_router

from common.bot_cmds_list import private

ALLOWED_UPDATES = ['message', 'edited_message']

# 🔹 Инициализация бота и диспетчера
bot = Bot(token=os.getenv('TOKEN'))
dp = Dispatcher(storage=MemoryStorage())

# 🔹 Регистрируем роутеры
dp.include_router(user_privatka_router)
dp.include_router(admin_router)

# 🔹 Инициализация базы данных перед запуском бота
init_db()

# 🔹 Добавляем себя как супер-админа (замени на свой Telegram ID)
SUPER_ADMIN_ID = int(os.getenv('SUPER_ADMIN_ID'))
init_super_admin(SUPER_ADMIN_ID)

# 🔹 Устанавливаем команды бота
async def setup_commands():
    await bot.set_my_commands(
        commands=private,
        scope=types.BotCommandScopeAllPrivateChats()
    )

# 🔹 Основной цикл
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()
    await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)

# 🔹 Запуск
if __name__ == "__main__":
    asyncio.run(main())
