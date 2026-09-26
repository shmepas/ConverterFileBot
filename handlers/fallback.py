"""Friendly responses for private messages no feature handler recognizes."""
from aiogram import Router, types
from aiogram.fsm.context import FSMContext

fallback_router = Router()
fallback_router.message.filter(lambda message: message.chat.type == "private")


def _fallback_prompt(current_state: str | None, is_text: bool) -> str:
    if current_state and current_state not in {
        "MenuStates:main", "MenuStates:about", "MenuStates:payment",
    }:
        if current_state.endswith("waiting_format"):
            return (
                "Не распознал ввод. Выберите формат с клавиатуры или напишите /menu, "
                "чтобы выйти в главное меню."
            )
        if current_state.endswith("waiting_file"):
            return (
                "Сейчас я ожидаю файл подходящего формата. Если хотите выйти, "
                "напишите /menu или /cancel."
            )
        return (
            "Сейчас нужно ответить на текущий вопрос бота. Для справки напишите /help, "
            "чтобы выйти в меню — /menu."
        )

    if is_text:
        return (
            "Не понял сообщение. Выберите действие кнопкой ниже или напишите /help. "
            "Главное меню всегда можно открыть командой /menu."
        )
    return (
        "Пока я не обрабатываю этот тип сообщения. Чтобы конвертировать файл, "
        "нажмите «🎞 Форматы», отправьте файл и выберите доступный результат. "
        "Напишите /help для справки."
    )


@fallback_router.message()
async def unmatched_private_message(message: types.Message, state: FSMContext):
    """Keep the bot responsive when a text or media type has no matching route."""
    from handlers.user_privatka import MenuStates, _clear_user_flow, build_dynamic_keyboard

    current_state = await state.get_state()
    if current_state in {
        None,
        MenuStates.main.state,
        MenuStates.about.state,
        MenuStates.payment.state,
    }:
        await _clear_user_flow(state)
        await state.set_state(MenuStates.main)
        await message.answer(
            _fallback_prompt(None, bool(message.text)),
            reply_markup=await build_dynamic_keyboard(message.from_user.id),
        )
        return

    await message.answer(
        _fallback_prompt(current_state, bool(message.text))
    )
