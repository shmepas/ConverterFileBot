"""Shared quota, isolated conversion, history, upload, and cancellation workflow."""
import asyncio
import logging
import os
import time

from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from data_base import db
from utiles.conversion_history import retain_source
from utiles.conversion_runner import (
    ConversionAlreadyRunning,
    ConversionCooldown,
    ConversionTimedOut,
    conversion_runner,
)
from utiles.file_validator import file_validator


logger = logging.getLogger(__name__)


class ConversionRejected(Exception):
    pass


async def process_conversion(
    message: Message,
    state,
    user_id: int,
    source_path: str,
    source_name: str,
    source_type: str,
    target_format: str,
    retained_file_id: int | None = None,
    charge_quota: bool = True,
) -> bool:
    """Process an accepted conversion; ``False`` means the task failed/cancelled."""
    limits = await db.check_user_limits(user_id)
    if not os.path.isfile(source_path) or os.path.islink(source_path):
        raise ConversionRejected("Исходный файл уже удалён. Отправьте его заново.")
    if target_format not in file_validator.TARGET_FORMATS_BY_INPUT_TYPE.get(source_type, []):
        raise ConversionRejected("Этот формат нельзя получить из выбранного файла.")
    if os.path.getsize(source_path) > limits["max_file_size"]:
        raise ConversionRejected(
            f"Файл больше лимита вашего тарифа ({limits['max_file_size'] // (1024 * 1024)} МБ)."
        )

    charged = False
    history_id = None
    output_path = None
    progress_message = None
    start_time = time.monotonic()
    try:
        if charge_quota and not limits["is_premium"]:
            if not await db.increment_conversion_count(user_id):
                raise ConversionRejected("Дневной лимит конвертаций исчерпан.")
            charged = True

        output_path, progress_message = await conversion_runner.run(
            message, user_id, source_path, target_format
        )

        if retained_file_id is None:
            retained_file_id = await retain_source(
                user_id, source_name, source_type, source_path
            )
        history_id = await db.create_conversion_history(
            user_id, source_name, target_format, retained_file_id
        )

        try:
            await progress_message.edit_text(f"📤 Отправляю результат {target_format}…")
        except Exception:
            pass
        await message.answer_document(
            FSInputFile(output_path),
            caption=f"✅ Готово: {target_format}",
        )
        duration = time.monotonic() - start_time
        await db.update_conversion_history(
            history_id, user_id, "success", duration_seconds=duration,
            output_size=os.path.getsize(output_path),
        )
        await db.log_action(user_id, f"Конвертировал {source_name} в {target_format}")

        try:
            await progress_message.edit_text(
                f"✅ Конвертация завершена: {target_format} · {duration:.1f} с",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📜 История конвертаций", callback_data="conversion:history")
                ]]),
            )
        except Exception:
            pass
        if charge_quota and not limits["is_premium"]:
            remaining = max(0, limits["daily_limit"] - await db.get_daily_conversion_count(user_id))
            await message.answer(f"Сегодня осталось конвертаций: {remaining} из {limits['daily_limit']}.")
        return True
    except asyncio.CancelledError:
        if history_id is None:
            history_id = await db.create_conversion_history(
                user_id, source_name, target_format, retained_file_id
            )
        await db.update_conversion_history(
            history_id, user_id, "cancelled",
            duration_seconds=time.monotonic() - start_time,
        )
        if charged:
            await db.decrement_conversion_count(user_id)
        if progress_message:
            try:
                await progress_message.edit_text("⏹ Конвертация отменена.")
            except Exception:
                pass
        return False
    except ConversionAlreadyRunning:
        if charged:
            await db.decrement_conversion_count(user_id)
        raise ConversionRejected("У вас уже выполняется конвертация.")
    except ConversionCooldown:
        if charged:
            await db.decrement_conversion_count(user_id)
        raise ConversionRejected("Подождите несколько секунд перед следующей конвертацией.")
    except ConversionTimedOut:
        logger.warning("Conversion timed out for user_id=%s format=%s", user_id, target_format)
        if history_id is None:
            history_id = await db.create_conversion_history(
                user_id, source_name, target_format, retained_file_id
            )
        await db.update_conversion_history(
            history_id, user_id, "failed", error_message="Истекло время обработки",
            duration_seconds=time.monotonic() - start_time,
        )
        if charged:
            await db.decrement_conversion_count(user_id)
        if progress_message:
            try:
                await progress_message.edit_text("⌛ Конвертация заняла слишком много времени и была остановлена.")
            except Exception:
                pass
        return False
    except ConversionRejected:
        if charged:
            await db.decrement_conversion_count(user_id)
        raise
    except Exception as exc:
        logger.exception("Conversion failed for user_id=%s format=%s", user_id, target_format)
        if history_id is None:
            history_id = await db.create_conversion_history(
                user_id, source_name, target_format, retained_file_id
            )
        await db.update_conversion_history(
            history_id, user_id, "failed", error_message=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - start_time,
        )
        if charged:
            await db.decrement_conversion_count(user_id)
        if progress_message:
            try:
                await progress_message.edit_text(
                    "❌ Не удалось обработать файл. Проверьте формат и попробуйте ещё раз."
                )
            except Exception:
                pass
        return False
    finally:
        if output_path:
            from converter_service import file_converter

            file_converter.cleanup_files(output_path)
        from handlers.user_privatka import MenuStates

        await state.set_state(MenuStates.main)
