from aiogram import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery
from data_base import db  # твой db.py
import html
import logging
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)

# Временной порог для игнорирования повторных действий (секунды)
DUPLICATE_TIME_THRESHOLD = 2  # если повтор в течение 2 секунд, не логируем

class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Update, data: dict):
        user_id = None
        username = None
        first_name = None
        last_name = None
        action = None

        # ----------------------
        # Сообщение
        # ----------------------
        if event.message:
            msg: Message = event.message
            if msg.from_user is None:
                return await handler(event, data)
            user_id = msg.from_user.id
            username = msg.from_user.username
            first_name = msg.from_user.first_name
            last_name = msg.from_user.last_name

            # Проверяем текст, фото, стикеры и др.
            if msg.text:
                action = msg.text
            elif msg.sticker:
                action = f"<sticker: {msg.sticker.emoji or 'unknown'}>"
            elif msg.photo:
                action = "<photo>"
            elif msg.video:
                action = "<video>"
            elif msg.document:
                action = f"<document: {msg.document.file_name}>"
            else:
                action = "<media/unknown>"

            # Экранируем HTML
            action = html.escape(action)

        # ----------------------
        # Callback query (кнопки)
        # ----------------------
        elif event.callback_query:
            cb: CallbackQuery = event.callback_query
            user_id = cb.from_user.id
            username = cb.from_user.username
            first_name = cb.from_user.first_name
            last_name = cb.from_user.last_name
            action = f"Callback: {html.escape(cb.data)}"

        # ----------------------
        # Логирование в базу с проверкой на дубликат
        # ----------------------
        if user_id and action:
            last_log = await db.get_latest_user_action(user_id)
            if last_log and last_log["action"] == action:
                # Проверка времени последнего действия
                try:
                    last_time = datetime.strptime(last_log["timestamp"], "%Y-%m-%d %H:%M:%S")
                    if datetime.now() - last_time < timedelta(seconds=DUPLICATE_TIME_THRESHOLD):
                        return await handler(event, data)
                except (TypeError, ValueError):
                    logger.warning("Ignoring malformed timestamp for user action log")

            # Сохраняем действие
            await db.log_action(
                user_id=user_id,
                action=action,
                username=username,
                first_name=first_name,
                last_name=last_name
            )

        return await handler(event, data)
