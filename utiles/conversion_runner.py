"""Run untrusted media conversions in killable, resource-bounded child processes."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from data_base import db


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_int_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


MAX_CONCURRENT_CONVERSIONS = _read_int_setting("MAX_CONCURRENT_CONVERSIONS", 2, 1, 4)
CONVERSION_TIMEOUT_SECONDS = _read_int_setting("CONVERSION_TIMEOUT_SECONDS", 600, 30, 1800)
MAX_CONVERSION_OUTPUT_BYTES = _read_int_setting(
    "MAX_CONVERSION_OUTPUT_MB", 500, 20, 1024
) * 1024 * 1024


class ConversionAlreadyRunning(Exception):
    pass


class ConversionCooldown(Exception):
    pass


class ConversionTimedOut(Exception):
    pass


class ConversionRunner:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT_CONVERSIONS,
                 timeout_seconds: int = CONVERSION_TIMEOUT_SECONDS):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.timeout_seconds = timeout_seconds
        self.active: dict[int, tuple[str, asyncio.Task, Message | None]] = {}

    async def _edit_progress(self, message: Message, text: str, job_id: str) -> None:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏹ Отменить", callback_data=f"convert:cancel:{job_id}")
        ]])
        try:
            await message.edit_text(text, reply_markup=markup)
        except Exception:
            pass

    async def run(self, message: Message, user_id: int, input_path: str,
                  target_format: str) -> tuple[str, Message]:
        if user_id in self.active:
            raise ConversionAlreadyRunning
        if not await db.claim_conversion_cooldown(user_id, cooldown_seconds=5):
            raise ConversionCooldown

        job_id = uuid.uuid4().hex
        progress_message = await message.answer(
            f"⏳ Поставил конвертацию в {target_format} в очередь…",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⏹ Отменить", callback_data=f"convert:cancel:{job_id}")
            ]]),
        )
        task = asyncio.current_task()
        self.active[user_id] = (job_id, task, progress_message)

        try:
            async with self.semaphore:
                await self._edit_progress(progress_message, f"⚙️ Конвертирую файл в {target_format}…", job_id)
                with tempfile.TemporaryDirectory(prefix="converter_worker_") as temp_dir:
                    result_path = Path(temp_dir) / "result.json"
                    command = [
                        sys.executable, "-B", "-m", "utiles.conversion_worker",
                        str(Path(input_path).resolve()), target_format,
                        str(user_id), str(result_path),
                    ]
                    creation_options = {}
                    if os.name == "nt":
                        creation_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                    else:
                        creation_options["start_new_session"] = True

                    converted_base = PROJECT_ROOT / "converted"
                    converted_root = converted_base / "runtime"
                    if converted_base.is_symlink() or converted_root.is_symlink():
                        raise RuntimeError("Каталог временных файлов недоступен")
                    converted_root.mkdir(parents=True, exist_ok=True)
                    converted_root = converted_root.resolve(strict=True)
                    converted_root.relative_to(PROJECT_ROOT)
                    worker_env = os.environ.copy()
                    worker_env.update({
                        "TMP": str(converted_root),
                        "TEMP": str(converted_root),
                        "TMPDIR": str(converted_root),
                        "CONVERTER_OUTPUT_DIR": str(converted_root),
                        "PYTHONIOENCODING": "utf-8",
                        "PYTHONUTF8": "1",
                    })

                    process = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=str(PROJECT_ROOT),
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        env=worker_env,
                        **creation_options,
                    )
                    try:
                        await asyncio.wait_for(process.wait(), timeout=self.timeout_seconds)
                    except asyncio.TimeoutError as exc:
                        await self._terminate_process_tree(process)
                        raise ConversionTimedOut from exc
                    except asyncio.CancelledError:
                        await self._terminate_process_tree(process)
                        raise

                    if not result_path.is_file():
                        raise RuntimeError("Конвертер завершился без результата")
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
                    if process.returncode != 0 or not payload.get("ok"):
                        raise RuntimeError(payload.get("error", "Не удалось обработать файл"))
                    raw_output_path = Path(payload.get("output_path", ""))
                    if raw_output_path.is_symlink():
                        raise RuntimeError("Конвертер не создал выходной файл")
                    output_path = raw_output_path.resolve(strict=True)
                    output_path.relative_to(converted_root)
                    if not output_path.is_file():
                        raise RuntimeError("Конвертер не создал выходной файл")
                    if output_path.stat().st_size > MAX_CONVERSION_OUTPUT_BYTES:
                        raise RuntimeError("Результат превышает допустимый размер")
                    return str(output_path), progress_message
        except ConversionTimedOut:
            await self._edit_progress(
                progress_message, "⌛ Конвертация заняла слишком много времени и была остановлена.", job_id
            )
            raise
        except asyncio.CancelledError:
            await self._edit_progress(progress_message, "⏹ Конвертация отменена.", job_id)
            raise
        finally:
            current = self.active.get(user_id)
            if current and current[0] == job_id:
                self.active.pop(user_id, None)

    async def _terminate_process_tree(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def cancel(self, user_id: int, job_id: str) -> bool:
        current = self.active.get(user_id)
        if not current or current[0] != job_id:
            return False
        task = current[1]
        if task is asyncio.current_task() or task.done():
            return False
        task.cancel()
        return True


conversion_runner = ConversionRunner()
