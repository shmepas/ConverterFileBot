from aiogram import  F, types, Router
from aiogram.filters import CommandStart, Command, or_f
from filters.chat_types import ChatTypeFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F, types


user_privatka_router = Router()
user_privatka_router.message.filter(ChatTypeFilter(['private']))


@user_privatka_router.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "Здравствуй! Я твой личный преобразователь файлов!\n\n"
        "Чтобы увидеть список команд, напиши /menu или 'меню'."
    )


@user_privatka_router.message(or_f(Command("menu"), (F.text.lower().contains('меню')) | (F.text.lower() == "меню")))
async def menu_cmd(message: types.Message):
    await message.answer(
        "Вот список доступных команд:\n"
        "/start - приветствие\n"
        "/menu - показать команды\n"
        "/about - информация о боте\n"
        "/payment - варианты оплаты\n"
        "/formats - доступные файл-форматыы"
    )


@user_privatka_router.message((F.text.lower().contains('инфа')) | (F.text.lower() == 'о боте'))
@user_privatka_router.message(Command('about'))
async def about_cmd(message: types.Message):
    await message.answer(
        "Я бот для конвертации файлов. Могу помочь с изменением формата!"
    )


@user_privatka_router.message((F.text.lower().contains('оплата')) | (F.text.lower() == "варианты оплаты"))
@user_privatka_router.message(Command("payment"))
async def payment_cmd(message: types.Message):
    await message.answer(
        "Варианты оплаты:\n"
        "1. Карта Visa/Mastercard\n"
        "2. Qiwi / YooMoney\n"
        "3. PayPal"
    )
    

@user_privatka_router.message((F.text.lower().contains('формат')) | (F.text.lower() == 'Доступные файл-форматы'))
@user_privatka_router.message(Command("formats"))
async def formats_cmd(message: types.Message):
    formats_list = [
        "PDF", "DOCX", "TXT", "JPEG", "PNG", "MP3", "MP4"
    ]
    await message.answer(
        "Доступные файл-форматы:\n" + "\n".join(f"- {f}" for f in formats_list)
    )



# Обработчик, который отвечает на любое сообщение пользователя в личке
@user_privatka_router.message()
async def echo_all_messages(message: types.Message):
    # Здесь можно обработать текст как угодно
    await message.reply(message.text)



@user_privatka_router.message(F.text)
async def catch_all_messages(message: types.Message):
    await message.answer(
        "Я не совсем понял твоё сообщение 😅\n"
        "Попробуй одну из команд:\n/menu, /about, /payment, /formats"
    )