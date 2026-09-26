from html import escape

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from data_base import db
from utiles.conversion_history import resolve_retained_path
from utiles.conversion_runner import conversion_runner
from utiles.conversion_workflow import ConversionRejected, process_conversion
from utiles.file_validator import file_validator


conversion_router = Router()


@conversion_router.callback_query(F.data == "conversion:history")
async def show_conversion_history(callback: types.CallbackQuery):
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("История доступна только в личном чате.", show_alert=True)
        return
    rows = await db.get_user_conversion_history(callback.from_user.id, limit=10)
    if not rows:
        await callback.answer("История пока пуста.", show_alert=True)
        return

    lines = ["📜 <b>Последние конвертации</b>"]
    buttons = []
    for row in rows:
        status = {
            "success": "✅ Готово",
            "failed": "❌ Ошибка",
            "cancelled": "⏹ Отменено",
            "processing": "⏳ Обрабатывается",
        }.get(row["status"], row["status"])
        lines.append(
            f"\n<b>#{row['id']} · {status}</b>\n"
            f"{escape(row['source_name'][:50])} → {escape(row['target_format'][:30])} · "
            f"{escape(row['created_at'])}"
        )
        if row["status"] == "success" and row.get("retained_file_id"):
            buttons.append([InlineKeyboardButton(
                text=f"🔁 Повторить #{row['id']} ({row['target_format']})",
                callback_data=f"conversion:repeat:{row['id']}",
            )])
    await callback.answer()
    await callback.message.answer(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
    )


@conversion_router.callback_query(F.data.startswith("conversion:repeat:"))
async def repeat_conversion(callback: types.CallbackQuery, state: FSMContext):
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("Повтор доступен только в личном чате.", show_alert=True)
        return
    try:
        history_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer("Некорректная запись истории.", show_alert=True)
        return

    entry = await db.get_conversion_history_entry(history_id, callback.from_user.id)
    if not entry:
        await callback.answer("Исходный файл уже удалён или запись недоступна.", show_alert=True)
        return
    source_path = resolve_retained_path(entry["stored_path"])
    if source_path is None:
        await callback.answer("Срок хранения исходного файла истёк.", show_alert=True)
        return
    allowed = file_validator.TARGET_FORMATS_BY_INPUT_TYPE.get(entry["detected_type"], [])
    if entry["target_format"] not in allowed:
        await callback.answer("Этот формат больше недоступен для файла.", show_alert=True)
        return

    await callback.answer("Повторяю конвертацию…")
    try:
        await process_conversion(
            callback.message,
            state,
            callback.from_user.id,
            str(source_path),
            entry["source_name"],
            entry["detected_type"],
            entry["target_format"],
            retained_file_id=entry["retained_file_id"],
        )
    except ConversionRejected as exc:
        await callback.message.answer(str(exc))


@conversion_router.callback_query(F.data.startswith("convert:cancel:"))
async def cancel_conversion(callback: types.CallbackQuery):
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("Отмена доступна только в личном чате.", show_alert=True)
        return
    job_id = callback.data.rsplit(":", 1)[1]
    cancelled = await conversion_runner.cancel(callback.from_user.id, job_id)
    if cancelled:
        await callback.answer("Останавливаю конвертацию…")
    else:
        await callback.answer("Эта конвертация уже завершена или кнопка устарела.", show_alert=True)
