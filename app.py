import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import find_dotenv, load_dotenv

from data_base.db import init_db, init_super_admin
from handlers.user_privatka import user_privatka_router
from handlers.adminka import admin_router
from common.bot_cmds_list import private

load_dotenv(find_dotenv())

ALLOWED_UPDATES = ['message', 'edited_message']

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher(storage=MemoryStorage())

dp.include_router(user_privatka_router)
dp.include_router(admin_router)

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))

async def setup_database():
    await init_db()
    await init_super_admin(SUPER_ADMIN_ID)

async def setup_commands():
    await bot.set_my_commands(commands=private, scope=types.BotCommandScopeAllPrivateChats())

async def main():
    await setup_database()
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_commands()
    await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)

if __name__ == "__main__":
    import asyncio
    from aiogram import F

    asyncio.run(dp.start_polling(bot))
