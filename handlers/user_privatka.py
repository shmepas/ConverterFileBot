from aiogram import  F, types, Router
from aiogram.filters import CommandStart, Command, or_f
from filters.chat_types import ChatTypeFilter

user_privatka_router = Router()
user_privatka_router.message.filter(ChatTypeFilter(['private']))


@user_privatka_router.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer('Здравствуй! Я твой личный преобразователь файлов!')


@user_privatka_router.message(or_f(Command("menu"), (F.text.lower().contains('меню')) | (F.text.lower() == "меню")))
async def menu_cmd(message: types.Message):
    await message.answer('Вот список доступных команд:')


@user_privatka_router.message((F.text.lower().contains('инфа')) | (F.text.lower() == 'о боте'))
@user_privatka_router.message(Command('about'))
async def about_cmd(message: types.Message):
    await message.answer("О боте:")


@user_privatka_router.message((F.text.lower().contains('оплата')) | (F.text.lower() == "варианты оплаты"))
@user_privatka_router.message(Command("payment"))
async def payment_cmd(message: types.Message):
    await message.answer("Варианты оплаты:")
    

@user_privatka_router.message((F.text.lower().contains('формат')) | (F.text.lower() == 'Доступные файл-форматы'))
@user_privatka_router.message(Command("formats"))
async def formats_cmd(message: types.Message):
    await message.answer("Доступные файл-форматы:")