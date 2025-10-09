from aiogram import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery
from data_base import db  # твой db.py

class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Update, data: dict):
        user_id = None
        username = None
        first_name = None
        last_name = None
        action = None

        # Сообщение
        if event.message:
            msg: Message = event.message
            user_id = msg.from_user.id
            username = msg.from_user.username
            first_name = msg.from_user.first_name
            last_name = msg.from_user.last_name
            action = msg.text or "<media/unknown>"

        # Callback query (кнопки)
        elif event.callback_query:
            cb: CallbackQuery = event.callback_query
            user_id = cb.from_user.id
            username = cb.from_user.username
            first_name = cb.from_user.first_name
            last_name = cb.from_user.last_name
            action = f"Callback: {cb.data}"

        # Логирование в базу
        if user_id and action:
            await db.log_action(
                user_id=user_id,
                action=action,
                username=username,
                first_name=first_name,
                last_name=last_name
            )

        return await handler(event, data)
