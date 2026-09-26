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
    waiting_user_reply = State()
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
        "waiting_user": "💬 Ждёт вашего ответа",
        "closed": "✅ Закрыт",
    }.get(status, "❔ Неизвестный статус")


def _admin_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🆕 Открытые", callback_data="tickets:filter:open"),
            InlineKeyboardButton(text="💬 Ждут пользователя", callback_data="tickets:filter:waiting_user"),
        ],
        [
            InlineKeyboardButton(text="✅ Закрытые", callback_data="tickets:filter:closed"),
            InlineKeyboardButton(text="📂 Активные", callback_data="tickets:filter:all"),
        ],
    ])


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

    data = await state.get_data()
    existing_ticket_id = data.get("ticket_id")
    try:
        await db.add_user(user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
        if existing_ticket_id:
            ticket_id = existing_ticket_id
            saved = await db.user_reply_to_support_ticket(ticket_id, user_id, ticket_text)
            if not saved:
                await state.clear()
                await answer_editable(message, "Этот тикет уже закрыт или ожидает ответа поддержки.")
                return
            await db.log_action(user_id, f"Продолжил тикет #{ticket_id}")
        else:
            ticket_id = await db.create_support_ticket(user_id, ticket_text)
            await db.log_action(user_id, f"Создал тикет #{ticket_id}")
    except db.SupportTicketLimitReached:
        await state.clear()
        from handlers.user_privatka import MenuStates, build_dynamic_keyboard

        await state.set_state(MenuStates.main)
        await answer_editable(
            message,
            "У вас уже есть 3 активных тикета или создано 5 тикетов за последние 24 часа. "
            "Продолжите переписку в существующем тикете или попробуйте позже.",
            reply_markup=await build_dynamic_keyboard(user_id),
        )
        return
    except Exception:
        await answer_editable(message, "Не удалось сохранить тикет. Попробуйте отправить сообщение ещё раз.")
        return
    await state.clear()

    user_label = message.from_user.full_name or str(user_id)
    username = f"@{message.from_user.username}" if message.from_user.username else "нет username"
    await _notify_admins(message.bot, ticket_id, user_id, user_label, username, ticket_text)

    from handlers.user_privatka import MenuStates, build_dynamic_keyboard

    await state.set_state(MenuStates.main)
    await answer_editable(
        message,
        f"✅ Тикет #{ticket_id} создан. Администратор ответит здесь, в боте.",
        reply_markup=await build_dynamic_keyboard(user_id),
    )


async def _notify_admins(bot, ticket_id: int, user_id: int, user_label: str,
                         username: str, text: str) -> None:
    admin_text = (
        f"🎫 <b>Новое сообщение в тикете #{ticket_id}</b>\n"
        f"👤 {escape(user_label[:80])} ({escape(username[:40])})\n"
        f"🆔 ID: <code>{user_id}</code>\n\n{escape(text[:550])}"
    )
    for admin_id in await db.get_admin_user_ids():
        try:
            await bot.send_message(
                admin_id,
                admin_text,
                reply_markup=_admin_ticket_keyboard(ticket_id, can_reply=True),
            )
        except Exception:
            # The ticket stays stored and can still be found in the admin inbox.
            continue


@ticket_router.message(F.text == "📨 Мои тикеты")
async def show_user_tickets(message: types.Message, state: FSMContext):
    tickets = await db.get_support_tickets(limit=3, user_id=message.from_user.id, include_closed=True)
    if not tickets:
        text = "📭 У вас пока нет тикетов. Нажмите «🎫 Создать тикет», чтобы обратиться в поддержку."
    else:
        lines = ["📨 <b>Ваши последние тикеты:</b>"]
        reply_buttons = []
        for ticket in tickets:
            status = _ticket_status(ticket["status"])
            messages = await db.get_support_ticket_messages(ticket["id"])
            lines.append(f"\n<b>#{ticket['id']} · {status}</b>")
            for entry in messages[-2:]:
                label = "Вы" if entry["sender_role"] == "user" else "Поддержка"
                lines.append(f"\n<b>{label}:</b> {escape(entry['message'][:70])}")
            if ticket["status"] == "waiting_user":
                reply_buttons.append([
                    InlineKeyboardButton(
                        text=f"✍️ Ответить на тикет #{ticket['id']}",
                        callback_data=f"ticket:user_reply:{ticket['id']}",
                    )
                ])
        text = "\n".join(lines)

    from handlers.user_privatka import MenuStates, build_dynamic_keyboard

    await state.set_state(MenuStates.main)
    markup = (
        InlineKeyboardMarkup(inline_keyboard=reply_buttons)
        if tickets and reply_buttons
        else await build_dynamic_keyboard(message.from_user.id)
    )
    await answer_editable(message, text, parse_mode="HTML", reply_markup=markup)


@ticket_router.message(F.text == "🎫 Тикеты")
async def show_admin_tickets(message: types.Message, state: FSMContext):
    if not await db.has_admin_access(message.from_user.id):
        return
    await state.clear()
    await _show_admin_ticket_list(message, status=None)


async def _show_admin_ticket_list(
    message: types.Message, status: str | None, *, edit_header: bool = False
) -> None:
    tickets = await db.get_support_tickets(
        limit=10, status=status, include_closed=status == "closed"
    )
    title = {
        "open": "🆕 Открытые тикеты",
        "waiting_user": "💬 Тикеты, ожидающие ответа пользователя",
        "closed": "✅ Закрытые тикеты",
    }.get(status, "🎫 Активные тикеты")
    if not tickets:
        text = f"{title}\n\n📭 Здесь пока нет тикетов."
        if edit_header:
            await message.edit_text(text, reply_markup=_admin_filter_keyboard())
        else:
            await message.answer(text, reply_markup=_admin_filter_keyboard())
        return

    header = f"{title} · показаны последние {len(tickets)}"
    if edit_header:
        await message.edit_text(header, reply_markup=_admin_filter_keyboard())
    else:
        await message.answer(header, reply_markup=_admin_filter_keyboard())

    for ticket in tickets:
        username = f"@{ticket['username']}" if ticket.get("username") else "без username"
        name = ticket.get("first_name") or str(ticket["user_id"])
        messages = await db.get_support_ticket_messages(ticket["id"])
        conversation = "\n\n".join(
            f"<b>{'Пользователь' if entry['sender_role'] == 'user' else 'Админ'}:</b> "
            f"{escape(entry['message'][:70])}"
            for entry in messages[-8:]
        )
        text = (
            f"🎫 <b>Тикет #{ticket['id']}</b> · {_ticket_status(ticket['status'])}\n"
            f"👤 {escape(name)} ({escape(username)})\n"
            f"🆔 ID: <code>{ticket['user_id']}</code>\n"
            f"🕒 {escape(ticket['created_at'])}\n\n"
            f"{conversation}"
        )
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=_admin_ticket_keyboard(ticket["id"], can_reply=ticket["status"] == "open"),
        )

    await message.answer(
        "Действия доступны под каждым тикетом.",
        reply_markup=await _admin_keyboard(message.from_user.id),
    )


@ticket_router.callback_query(F.data.startswith("tickets:filter:"))
async def filter_admin_tickets(callback: types.CallbackQuery):
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("Список тикетов доступен только в личном чате.", show_alert=True)
        return
    if not await db.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw_status = callback.data.rsplit(":", 1)[1]
    status = None if raw_status == "all" else raw_status
    if status not in {None, "open", "waiting_user", "closed"}:
        await callback.answer("Неизвестный фильтр.", show_alert=True)
        return
    await callback.answer()
    await _show_admin_ticket_list(callback.message, status, edit_header=True)


@ticket_router.callback_query(F.data.startswith("ticket:user_reply:"))
async def start_user_ticket_reply(callback: types.CallbackQuery, state: FSMContext):
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("Ответить можно только в личном чате с ботом.", show_alert=True)
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer("Некорректный тикет.", show_alert=True)
        return
    ticket = await db.get_support_ticket(ticket_id)
    if not ticket or ticket["user_id"] != callback.from_user.id:
        await callback.answer("Тикет не найден.", show_alert=True)
        return
    if ticket["status"] != "waiting_user":
        await callback.answer("На тикет нельзя ответить в текущем статусе.", show_alert=True)
        return
    await state.clear()
    await state.set_state(TicketStates.waiting_user_reply)
    await state.update_data(ticket_id=ticket_id)
    await callback.answer()
    await callback.message.answer(f"Напишите сообщение в тикет #{ticket_id} (до {MAX_TICKET_LENGTH} символов).")


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


@ticket_router.message(TicketStates.waiting_user_reply)
async def receive_user_ticket_reply(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not message.text or not message.text.strip():
        await answer_editable(message, "Ответ должен быть текстом.")
        return
    reply_text = message.text.strip()
    if len(reply_text) > MAX_TICKET_LENGTH:
        await answer_editable(message, f"Сообщение слишком длинное. Максимум — {MAX_TICKET_LENGTH} символов.")
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id or not await db.user_reply_to_support_ticket(ticket_id, user_id, reply_text):
        await state.clear()
        await answer_editable(message, "Этот тикет закрыт или уже обработан.")
        return
    await db.log_action(user_id, f"Продолжил тикет #{ticket_id}")
    await state.clear()
    username = f"@{message.from_user.username}" if message.from_user.username else "нет username"
    await _notify_admins(
        message.bot, ticket_id, user_id,
        message.from_user.full_name or str(user_id), username, reply_text,
    )
    from handlers.user_privatka import MenuStates, build_dynamic_keyboard

    await state.set_state(MenuStates.main)
    await answer_editable(
        message, f"✅ Сообщение добавлено в тикет #{ticket_id}.",
        reply_markup=await build_dynamic_keyboard(user_id),
    )


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
