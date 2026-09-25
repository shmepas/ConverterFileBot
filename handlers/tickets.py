from html import escape

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from data_base import db
from utils import answer_editable


ticket_router = Router()
ticket_router.message.filter(lambda message: message.chat.type == "private")

MAX_TICKET_LENGTH = 1800


class TicketStates(StatesGroup):
    waiting_message = State()
    waiting_admin_reply = State()


def _admin_ticket_keyboard(ticket_id: int, can_reply: bool) -> InlineKeyboardMarkup:
    buttons = []
    if can_reply:
        buttons.append(InlineKeyboardButton(text="✍️ Ответить", callback_data=f"ticket:reply:{ticket_id}"))
    buttons.append(InlineKeyboardButton(text="✅ Закрыть", callback_data=f"ticket:close:{ticket_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def _ticket_status(status: str) -> str:
    return {
        "open": "🆕 Открыт",
        "answered": "💬 Ответ отправлен",
        "closed": "✅ Закрыт",
    }.get(status, "❔ Неизвестный статус")


async def _admin_keyboard(user_id: int):
    from handlers.adminka import admin_main_kb

    return await admin_main_kb(user_id)


@ticket_router.message(F.text == "🎫 Создать тикет")
async def start_ticket(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(TicketStates.waiting_message)
    cancel_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить тикет")]], resize_keyboard=True
    )
    await answer_editable(
        message,
        "Опишите проблему или предложение одним сообщением. "
        f"Максимум {MAX_TICKET_LENGTH} символов.",
        reply_markup=cancel_kb,
    )


@ticket_router.message(TicketStates.waiting_message)
async def receive_ticket(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "❌ Отменить тикет":
        await state.clear()
        from handlers.user_privatka import MenuStates, build_dynamic_keyboard

        await state.set_state(MenuStates.main)
        await answer_editable(
            message, "Создание тикета отменено.",
            reply_markup=await build_dynamic_keyboard(user_id),
        )
        return

    if not message.text or not message.text.strip():
        await answer_editable(message, "Пришлите описание тикета текстом или нажмите «❌ Отменить тикет».")
        return
    ticket_text = message.text.strip()
    if len(ticket_text) > MAX_TICKET_LENGTH:
        await answer_editable(message, f"Текст слишком длинный. Максимум — {MAX_TICKET_LENGTH} символов.")
        return

    try:
        await db.add_user(user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
        ticket_id = await db.create_support_ticket(user_id, ticket_text)
    except Exception:
        await answer_editable(message, "Не удалось сохранить тикет. Попробуйте отправить сообщение ещё раз.")
        return
    await db.log_action(user_id, f"Создал тикет #{ticket_id}")
    await state.clear()

    user_label = message.from_user.full_name or str(user_id)
    username = f"@{message.from_user.username}" if message.from_user.username else "нет username"
    admin_text = (
        f"🎫 <b>Новый тикет #{ticket_id}</b>\n"
        f"👤 {escape(user_label)} ({escape(username)})\n"
        f"🆔 ID: <code>{user_id}</code>\n\n"
        f"{escape(ticket_text)}"
    )
    for admin_id in await db.get_admin_user_ids():
        try:
            await message.bot.send_message(
                admin_id,
                admin_text,
                reply_markup=_admin_ticket_keyboard(ticket_id, can_reply=True),
            )
        except Exception:
            # Notification failure must not discard a ticket already stored in DB.
            continue

    from handlers.user_privatka import MenuStates, build_dynamic_keyboard

    await state.set_state(MenuStates.main)
    await answer_editable(
        message,
        f"✅ Тикет #{ticket_id} создан. Администратор ответит здесь, в боте.",
        reply_markup=await build_dynamic_keyboard(user_id),
    )


@ticket_router.message(F.text == "📨 Мои тикеты")
async def show_user_tickets(message: types.Message, state: FSMContext):
    tickets = await db.get_support_tickets(limit=3, user_id=message.from_user.id, include_closed=True)
    if not tickets:
        text = "📭 У вас пока нет тикетов. Нажмите «🎫 Создать тикет», чтобы обратиться в поддержку."
    else:
        lines = ["📨 <b>Ваши последние тикеты:</b>"]
        for ticket in tickets:
            status = _ticket_status(ticket["status"])
            lines.append(
                f"\n<b>#{ticket['id']} · {status}</b>\n"
                f"{escape(ticket['message'][:250])}"
            )
            if ticket.get("admin_reply"):
                lines.append(f"\n<b>Ответ:</b>\n{escape(ticket['admin_reply'][:250])}")
        text = "\n".join(lines)

    from handlers.user_privatka import MenuStates, build_dynamic_keyboard

    await state.set_state(MenuStates.main)
    await answer_editable(
        message, text, parse_mode="HTML",
        reply_markup=await build_dynamic_keyboard(message.from_user.id),
    )


@ticket_router.message(F.text == "🎫 Тикеты")
async def show_admin_tickets(message: types.Message, state: FSMContext):
    if not await db.has_admin_access(message.from_user.id):
        return
    await state.clear()
    tickets = await db.get_support_tickets(limit=10)
    if not tickets:
        await answer_editable(
            message,
            "📭 Активных тикетов нет.",
            reply_markup=await _admin_keyboard(message.from_user.id),
        )
        return

    for ticket in tickets:
        username = f"@{ticket['username']}" if ticket.get("username") else "без username"
        name = ticket.get("first_name") or str(ticket["user_id"])
        text = (
            f"🎫 <b>Тикет #{ticket['id']}</b> · {_ticket_status(ticket['status'])}\n"
            f"👤 {escape(name)} ({escape(username)})\n"
            f"🆔 ID: <code>{ticket['user_id']}</code>\n"
            f"🕒 {escape(ticket['created_at'])}\n\n"
            f"{escape(ticket['message'])}"
        )
        if ticket.get("admin_reply"):
            text += f"\n\n<b>Ваш ответ:</b>\n{escape(ticket['admin_reply'])}"
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=_admin_ticket_keyboard(ticket["id"], can_reply=ticket["status"] == "open"),
        )

    await message.answer(
        "Действия доступны под каждым тикетом.",
        reply_markup=await _admin_keyboard(message.from_user.id),
    )


@ticket_router.callback_query(F.data.startswith("ticket:"))
async def admin_ticket_action(callback: types.CallbackQuery, state: FSMContext):
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("Действие доступно только в личном чате с ботом.", show_alert=True)
        return
    if not await db.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    try:
        _, action, raw_ticket_id = callback.data.split(":", 2)
        ticket_id = int(raw_ticket_id)
    except (ValueError, AttributeError):
        await callback.answer("Некорректный тикет.", show_alert=True)
        return

    ticket = await db.get_support_ticket(ticket_id)
    if not ticket:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    if action == "reply":
        if ticket["status"] != "open":
            await callback.answer("На этот тикет уже ответили или он закрыт.", show_alert=True)
            return
        await state.clear()
        await state.set_state(TicketStates.waiting_admin_reply)
        await state.update_data(ticket_id=ticket_id)
        await callback.answer()
        await callback.message.answer(f"Введите ответ для тикета #{ticket_id} (до {MAX_TICKET_LENGTH} символов).")
    elif action == "close":
        closed = await db.close_support_ticket(ticket_id)
        await callback.answer("Тикет закрыт." if closed else "Тикет уже закрыт.")
        if closed:
            try:
                await callback.bot.send_message(
                    ticket["user_id"], f"✅ Ваш тикет #{ticket_id} закрыт администратором."
                )
            except Exception:
                pass
            await callback.message.edit_reply_markup(reply_markup=None)
    else:
        await callback.answer("Неизвестное действие.", show_alert=True)


@ticket_router.message(TicketStates.waiting_admin_reply)
async def receive_admin_reply(message: types.Message, state: FSMContext):
    admin_id = message.from_user.id
    if not await db.has_admin_access(admin_id):
        await state.clear()
        return
    if not message.text or not message.text.strip():
        await answer_editable(message, "Ответ должен быть текстом.")
        return
    reply_text = message.text.strip()
    if len(reply_text) > MAX_TICKET_LENGTH:
        await answer_editable(message, f"Ответ слишком длинный. Максимум — {MAX_TICKET_LENGTH} символов.")
        return

    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    ticket = await db.get_support_ticket(ticket_id) if ticket_id else None
    if not ticket or ticket["status"] != "open":
        await state.clear()
        await answer_editable(message, "Тикет уже обработан или не найден.")
        return
    if not await db.reply_to_support_ticket(ticket_id, admin_id, reply_text):
        await state.clear()
        await answer_editable(message, "Не удалось сохранить ответ: тикет уже обработан.")
        return

    try:
        await message.bot.send_message(
            ticket["user_id"],
            f"💬 <b>Ответ на ваш тикет #{ticket_id}</b>\n\n{escape(reply_text)}",
            parse_mode="HTML",
        )
    except Exception:
        await answer_editable(
            message,
            "Ответ сохранён, но Telegram не доставил его пользователю. "
            "Пользователь увидит ответ в разделе «📨 Мои тикеты».",
        )
    else:
        await answer_editable(message, f"✅ Ответ на тикет #{ticket_id} отправлен пользователю.")

    await db.log_action(admin_id, f"Ответил на тикет #{ticket_id}")
    await state.clear()
    await answer_editable(message, "Выберите действие в админ-панели:", reply_markup=await _admin_keyboard(admin_id))
