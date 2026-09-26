import sqlite3
import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import aiosqlite
from PIL import Image

from data_base import db
from data_base.backup import backup_database
from utiles import conversion_history
from utiles.conversion_runner import ConversionRunner
from utiles.file_validator import file_validator


class _FakeProgressMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


class _FakeMessage:
    def __init__(self):
        self.progress = _FakeProgressMessage()

    async def answer(self, text, **kwargs):
        return self.progress


class ProjectHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_db_path = db.DB_PATH
        db.DB_PATH = str(self.root / "test.sqlite3")
        await db.init_db()
        await db.add_user(1001, "tester", "Test", "User")
        await db.add_user(1002, "other", "Other", "User")

    async def asyncTearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    async def test_ticket_conversation_owner_and_atomic_creation_limits(self):
        ticket_id = await db.create_support_ticket(1001, "Первое сообщение")
        self.assertFalse(await db.user_reply_to_support_ticket(ticket_id, 1002, "Чужой ответ"))
        self.assertTrue(await db.reply_to_support_ticket(ticket_id, 9001, "Ответ поддержки"))
        self.assertTrue(await db.user_reply_to_support_ticket(ticket_id, 1001, "Уточнение"))
        messages = await db.get_support_ticket_messages(ticket_id)
        self.assertEqual([m["sender_role"] for m in messages], ["user", "admin", "user"])

        second = await db.create_support_ticket(1001, "Второй")
        third = await db.create_support_ticket(1001, "Третий")
        with self.assertRaises(db.SupportTicketLimitReached):
            await db.create_support_ticket(1001, "Четвёртый пока слишком рано")
        await db.close_support_ticket(second)
        replacement = await db.create_support_ticket(1001, "Замена закрытого тикета")
        self.assertGreater(replacement, third)

    async def test_ticket_filters_and_support_status_transitions(self):
        open_id = await db.create_support_ticket(1001, "Открыт")
        waiting_id = await db.create_support_ticket(1002, "Ждёт")
        await db.reply_to_support_ticket(waiting_id, 9001, "Ответ")
        await db.close_support_ticket(open_id)

        self.assertEqual([t["id"] for t in await db.get_support_tickets(status="waiting_user")], [waiting_id])
        self.assertEqual(
            [t["id"] for t in await db.get_support_tickets(status="closed", include_closed=True)],
            [open_id],
        )

    async def test_ticket_creation_limit_is_serialized_between_concurrent_requests(self):
        await db.create_support_ticket(1001, "Первый активный")
        await db.create_support_ticket(1001, "Второй активный")
        results = await asyncio.gather(
            db.create_support_ticket(1001, "Соревнующийся третий тикет"),
            db.create_support_ticket(1001, "Соревнующийся четвёртый тикет"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, int) for result in results), 1)
        self.assertEqual(sum(isinstance(result, db.SupportTicketLimitReached) for result in results), 1)

    async def test_daily_ticket_limit_counts_closed_tickets(self):
        for index in range(5):
            ticket_id = await db.create_support_ticket(1001, f"Обращение {index}")
            await db.close_support_ticket(ticket_id)
        with self.assertRaises(db.SupportTicketLimitReached):
            await db.create_support_ticket(1001, "Шестое обращение за сутки")

    async def test_file_detection_ignores_extension_and_checks_structure(self):
        image_path = self.root / "payload.txt"
        Image.new("RGB", (8, 8), "white").save(image_path, format="PNG")
        self.assertEqual(file_validator.detect_file_type(str(image_path)), "png")
        self.assertTrue(file_validator.validate_file_type(str(image_path), "PNG → JPG")[0])
        self.assertFalse(file_validator.validate_file_type(str(image_path), "MP3")[0])
        self.assertTrue(file_validator.validate_file_integrity(str(image_path), "PNG → JPG")[0])

        fake_image = self.root / "fake.png"
        fake_image.write_text("not an image", encoding="utf-8")
        self.assertEqual(file_validator.detect_file_type(str(fake_image)), "text")
        self.assertFalse(file_validator.validate_file_integrity(str(fake_image), "PNG → JPG")[0])

    async def test_zip_bomb_ratio_is_rejected(self):
        archive_path = self.root / "archive.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("repetitive.txt", "0" * 1024 * 1024)
        valid, error = file_validator.validate_file_integrity(str(archive_path), "TXT")
        self.assertFalse(valid)
        self.assertIn("коэффициент сжатия", error)

    async def test_history_retention_is_user_scoped_and_expires(self):
        history_root = self.root / "uploads" / "history"
        source = self.root / "input.txt"
        source.write_text("short text", encoding="utf-8")
        with patch.object(conversion_history, "PROJECT_ROOT", self.root), patch.object(
            conversion_history, "HISTORY_ROOT", history_root
        ):
            retained_id = await conversion_history.retain_source(1001, "input.txt", "text", str(source))
            self.assertIsNotNone(retained_id)
            row = await db.get_retained_conversion_file(retained_id, 1001)
            self.assertIsNone(await db.get_retained_conversion_file(retained_id, 1002))
            saved = conversion_history.resolve_retained_path(row["stored_path"])
            self.assertIsNotNone(saved)
            self.assertEqual(saved.read_text(encoding="utf-8"), "short text")

            async with aiosqlite.connect(db.DB_PATH) as connection:
                expired = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                await connection.execute(
                    "UPDATE retained_conversion_files SET expires_at = ? WHERE id = ?",
                    (expired, retained_id),
                )
                await connection.commit()
            removed = await conversion_history.cleanup_expired_files()
            self.assertGreaterEqual(removed, 1)
            self.assertFalse(saved.exists())

    async def test_performance_stats_aggregate_persisted_conversion_history(self):
        successful = await db.create_conversion_history(1001, "one.png", "PNG → JPG")
        await db.update_conversion_history(successful, 1001, "success", duration_seconds=2.5)
        failed = await db.create_conversion_history(1002, "two.png", "PNG → JPG")
        await db.update_conversion_history(failed, 1002, "failed", duration_seconds=1.0)
        other_format = await db.create_conversion_history(1001, "three.pdf", "PDF → TXT")
        await db.update_conversion_history(other_format, 1001, "success", duration_seconds=3.5)
        await db.create_conversion_history(1001, "unfinished.docx", "DOCX → PDF")

        stats = await db.get_conversion_performance_stats(days=90)
        self.assertEqual(stats["total_conversions"], 3)
        self.assertEqual(stats["successful_conversions"], 2)
        self.assertEqual(stats["failed_conversions"], 1)
        self.assertEqual(stats["average_time"], 3.0)
        self.assertEqual(stats["formats_used"]["PNG → JPG"]["count"], 2)
        self.assertEqual(stats["formats_used"]["PNG → JPG"]["successful_count"], 1)

    async def test_expired_retained_file_cleanup_preserves_recent_performance_history(self):
        source = self.root / "retained.txt"
        source.write_text("source", encoding="utf-8")
        retained_id = await db.add_retained_conversion_file(
            1001, "retained.txt", "text", str(source), retention_hours=1
        )
        history_id = await db.create_conversion_history(
            1001, "retained.txt", "TXT → PDF", retained_id
        )
        await db.update_conversion_history(history_id, 1001, "success", duration_seconds=1.25)
        async with aiosqlite.connect(db.DB_PATH) as connection:
            expired = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            await connection.execute(
                "UPDATE retained_conversion_files SET expires_at = ? WHERE id = ?",
                (expired, retained_id),
            )
            await connection.commit()

        expired_files = await db.cleanup_expired_conversion_history(retention_days=90)
        self.assertEqual(len(expired_files), 1)
        self.assertIsNone(await db.get_retained_conversion_file(retained_id, 1001))
        async with aiosqlite.connect(db.DB_PATH) as connection:
            async with connection.execute(
                "SELECT status, retained_file_id FROM conversion_history WHERE id = ?",
                (history_id,),
            ) as cursor:
                row = await cursor.fetchone()
        self.assertEqual(tuple(row), ("success", None))
        stats = await db.get_conversion_performance_stats(days=90)
        self.assertEqual(stats["total_conversions"], 1)
        self.assertEqual(stats["successful_conversions"], 1)

    async def test_backup_is_atomic_and_integrity_checked(self):
        backup_dir = self.root / "backups"
        backup_path = await backup_database(db.DB_PATH, backup_dir)
        self.assertIsNotNone(backup_path)
        with closing(sqlite3.connect(backup_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    async def test_conversion_cooldown_is_atomic(self):
        self.assertTrue(await db.claim_conversion_cooldown(1001, cooldown_seconds=5))
        self.assertFalse(await db.claim_conversion_cooldown(1001, cooldown_seconds=5))

    async def test_role_keyboards_separate_user_admin_and_super_admin(self):
        from handlers.adminka import admin_main_kb
        from handlers.user_privatka import build_dynamic_keyboard

        user_keyboard = await build_dynamic_keyboard(1001)
        user_labels = {button.text for row in user_keyboard.keyboard for button in row}
        self.assertIn("🎫 Создать тикет", user_labels)
        self.assertIn("📨 Мои тикеты", user_labels)
        self.assertNotIn("🎫 Тикеты", user_labels)
        self.assertNotIn("🔧 Админка", user_labels)

        async with aiosqlite.connect(db.DB_PATH) as connection:
            await connection.execute("UPDATE users SET is_admin = 1 WHERE user_id = 1001")
            await connection.execute("INSERT INTO super_admin(user_id) VALUES (1002)")
            await connection.commit()

        admin_open = await build_dynamic_keyboard(1001, admin_open=True)
        admin_labels = {button.text for row in admin_open.keyboard for button in row}
        self.assertIn("🎫 Тикеты", admin_labels)
        self.assertNotIn("➕ Добавить админа", admin_labels)

        super_open = await build_dynamic_keyboard(1002, admin_open=True)
        super_labels = {button.text for row in super_open.keyboard for button in row}
        self.assertIn("🎫 Тикеты", super_labels)
        self.assertIn("➕ Добавить админа", super_labels)
        self.assertIn("🎫 Тикеты", {button.text for row in (await admin_main_kb(1001)).keyboard for button in row})
        self.assertIn("🎫 Тикеты", {button.text for row in (await admin_main_kb(1002)).keyboard for button in row})

    async def test_conversion_runner_terminates_cancelled_child_and_updates_progress(self):
        runner = ConversionRunner(max_concurrent=1, timeout_seconds=30)
        message = _FakeMessage()
        input_path = self.root / "tiny.png"
        Image.new("RGB", (8, 8), "white").save(input_path, format="PNG")
        child_waiting = asyncio.Event()
        child_terminated = asyncio.Event()

        class FakeProcess:
            returncode = None
            pid = 12345

            async def wait(self):
                child_waiting.set()
                await asyncio.Event().wait()

        process = FakeProcess()

        async def fake_create_process(*args, **kwargs):
            return process

        async def fake_terminate(child):
            child.returncode = -9
            child_terminated.set()

        runner._terminate_process_tree = fake_terminate
        with patch("utiles.conversion_runner.asyncio.create_subprocess_exec", fake_create_process):
            task = asyncio.create_task(runner.run(message, 1001, str(input_path), "PNG → JPG"))
            for _ in range(100):
                if 1001 in runner.active:
                    break
                await asyncio.sleep(0.01)
            self.assertIn(1001, runner.active)
            await asyncio.wait_for(child_waiting.wait(), timeout=5)
            job_id = runner.active[1001][0]
            self.assertTrue(await runner.cancel(1001, job_id))
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(child_terminated.is_set())
        self.assertNotIn(1001, runner.active)
        self.assertTrue(any("отмен" in text.lower() for text in message.progress.edits))

    async def test_isolated_worker_converts_png_to_jpeg(self):
        input_path = self.root / "small.png"
        Image.new("RGB", (8, 8), "white").save(input_path, format="PNG")
        converted = self.root / "converted"
        converted.mkdir()
        result_path = self.root / "result.json"
        worker_env = os.environ.copy()
        worker_env.update({
            "TMP": str(converted), "TEMP": str(converted), "TMPDIR": str(converted),
            "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
            "CONVERTER_OUTPUT_DIR": str(converted),
        })
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "utiles.conversion_worker", str(input_path),
             "PNG → JPG", "1001", str(result_path)],
            cwd=Path(__file__).resolve().parents[1], env=worker_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=45,
        )
        details = result_path.read_text(encoding="utf-8") if result_path.exists() else proc.stderr[-500:]
        self.assertEqual(proc.returncode, 0, details)
        import json
        output_path = Path(json.loads(result_path.read_text(encoding="utf-8"))["output_path"])
        self.assertTrue(output_path.is_file())
        with Image.open(output_path) as output:
            self.assertEqual(output.format, "JPEG")


if __name__ == "__main__":
    unittest.main()
