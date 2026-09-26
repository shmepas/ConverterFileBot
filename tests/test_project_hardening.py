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
from data_base.fsm_storage import KeyedEventIsolation, SQLiteFSMStorage
from utiles import conversion_history
from utiles.conversion_runner import ConversionRunner
from utiles.file_validator import file_validator
from aiogram.fsm.storage.base import StorageKey


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


class _FakeTicketBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})

    async def send_photo(self, chat_id, photo, **kwargs):
        self.sent.append({"chat_id": chat_id, "photo": photo, "kwargs": kwargs})

    async def send_document(self, chat_id, document, **kwargs):
        self.sent.append({"chat_id": chat_id, "document": document, "kwargs": kwargs})


class _FakeTicketMessage:
    def __init__(self, from_user, text, bot):
        from types import SimpleNamespace

        self.from_user = from_user
        self.text = text
        self.bot = bot
        self.chat = SimpleNamespace(type="private", id=from_user.id)

    async def answer(self, text, **kwargs):
        return self

    async def edit_reply_markup(self, **kwargs):
        return None


class _FakeTicketCallback:
    def __init__(self, message, user_id, data, bot):
        from types import SimpleNamespace

        self.message = message
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.bot = bot

    async def answer(self, text=None, **kwargs):
        return None


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

    async def test_ticket_menu_command_cancels_draft_instead_of_creating_ticket(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from handlers import tickets
        from handlers.user_privatka import MenuStates

        storage = SQLiteFSMStorage()
        state = FSMContext(
            storage=storage,
            key=StorageKey(bot_id=77, chat_id=1001, user_id=1001),
        )
        await state.set_state(tickets.TicketStates.waiting_message)
        await state.update_data(draft="draft text")
        person = SimpleNamespace(
            id=1001, username="tester", first_name="Test",
            last_name="User", full_name="Test User",
        )
        message = _FakeTicketMessage(person, "/menu", _FakeTicketBot())

        with patch("handlers.tickets.answer_editable", new=AsyncMock()) as answer:
            with patch(
                "handlers.user_privatka.build_dynamic_keyboard",
                new=AsyncMock(return_value="main-menu"),
            ):
                await tickets.receive_ticket(message, state)

        self.assertEqual(await state.get_state(), MenuStates.main.state)
        self.assertEqual(await state.get_data(), {})
        self.assertEqual(await db.get_support_tickets(user_id=1001), [])
        self.assertIn("отменён", answer.await_args.args[1])

    async def test_unmatched_private_text_gets_menu_and_help_hint(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from handlers import fallback
        from handlers.user_privatka import MenuStates

        state = FSMContext(
            storage=SQLiteFSMStorage(),
            key=StorageKey(bot_id=77, chat_id=1001, user_id=1001),
        )
        await state.set_state(MenuStates.main)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=SimpleNamespace(id=1001),
            text="неизвестный текст",
            answer=AsyncMock(),
        )
        with patch(
            "handlers.user_privatka.build_dynamic_keyboard",
            new=AsyncMock(return_value="main-menu"),
        ):
            await fallback.unmatched_private_message(message, state)

        self.assertIn("/help", message.answer.await_args.args[0])
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "main-menu")

    async def test_setup_commands_registers_recovery_commands_and_menu_button(self):
        import importlib
        from unittest.mock import AsyncMock

        with patch.dict(os.environ, {"TOKEN": "123456:TEST", "SUPER_ADMIN_ID": "1001"}):
            app = importlib.import_module("app")
        with patch.object(app.bot, "set_my_commands", new=AsyncMock()) as set_commands:
            with patch.object(app.bot, "set_chat_menu_button", new=AsyncMock()) as set_menu:
                await app.setup_commands()

        command_names = [command.command for command in set_commands.await_args.kwargs["commands"]]
        self.assertEqual(command_names, ["start", "menu", "help", "cancel"])
        self.assertIsInstance(
            set_commands.await_args.kwargs["scope"],
            app.types.BotCommandScopeAllPrivateChats,
        )
        self.assertIsInstance(
            set_menu.await_args.kwargs["menu_button"],
            app.types.MenuButtonCommands,
        )

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

    async def test_ticket_attachments_assignment_and_open_counter(self):
        await db.add_user(9001, "admin", "Admin", "")
        await db.add_user(9002, "second_admin", "Second", "Admin")
        async with aiosqlite.connect(db.DB_PATH) as connection:
            await connection.execute("UPDATE users SET is_admin = 1 WHERE user_id IN (9001, 9002)")
            await connection.commit()

        attachment = {"type": "document", "file_id": "telegram-file-id", "file_name": "trace.log"}
        ticket_id = await db.create_support_ticket(1001, "", attachment=attachment)
        self.assertEqual(await db.get_open_support_ticket_count(), 1)
        self.assertEqual(await db.claim_support_ticket(ticket_id, 9001), "assigned")
        self.assertIsNone(await db.claim_support_ticket(ticket_id, 9002))
        ticket = await db.get_support_ticket(ticket_id)
        self.assertEqual(ticket["assigned_admin_id"], 9001)
        messages = await db.get_support_ticket_messages(ticket_id)
        self.assertEqual(messages[0]["message"], "Вложение: trace.log")
        self.assertEqual(messages[0]["attachment_file_id"], "telegram-file-id")

        admin_attachment = {"type": "photo", "file_id": "admin-photo-id"}
        self.assertTrue(await db.reply_to_support_ticket(ticket_id, 9001, "", attachment=admin_attachment))
        self.assertEqual(await db.get_open_support_ticket_count(), 0)
        self.assertEqual(await db.claim_support_ticket(ticket_id, 9001), "released")
        self.assertTrue(await db.user_reply_to_support_ticket(ticket_id, 1001, "", attachment={
            "type": "photo", "file_id": "user-photo-id",
        }))
        self.assertEqual(await db.get_open_support_ticket_count(), 1)
        self.assertEqual(len(await db.get_support_ticket_messages(ticket_id)), 3)

        self.assertTrue(await db.close_support_ticket(ticket_id))
        self.assertEqual(await db.get_open_support_ticket_count(), 0)
        with self.assertRaises(ValueError):
            await db.create_support_ticket(1002, "", attachment={"type": "photo", "file_id": ""})

    async def test_admin_keyboard_shows_open_ticket_counter(self):
        from handlers.adminka import admin_main_kb

        async with aiosqlite.connect(db.DB_PATH) as connection:
            await connection.execute("INSERT INTO super_admin(user_id) VALUES (1001)")
            await connection.commit()
        await db.create_support_ticket(1002, "Нужна помощь")
        keyboard = await admin_main_kb(1001)
        labels = {button.text for row in keyboard.keyboard for button in row}
        self.assertIn("🎫 Тикеты (1)", labels)

    async def test_legacy_ticket_schema_is_upgraded_by_numbered_migrations(self):
        legacy_path = self.root / "legacy.sqlite3"
        async with aiosqlite.connect(legacy_path) as connection:
            await connection.execute("""
                CREATE TABLE support_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    admin_reply TEXT,
                    replied_by INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await connection.execute("""
                CREATE TABLE support_ticket_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL,
                    sender_role TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            await connection.commit()

        current_path = db.DB_PATH
        try:
            db.DB_PATH = str(legacy_path)
            await db.init_db()
            async with aiosqlite.connect(db.DB_PATH) as connection:
                async with connection.execute("PRAGMA table_info(support_ticket_messages)") as cursor:
                    message_columns = {row[1] for row in await cursor.fetchall()}
                async with connection.execute("PRAGMA table_info(support_tickets)") as cursor:
                    ticket_columns = {row[1] for row in await cursor.fetchall()}
                async with connection.execute("SELECT version FROM schema_migrations ORDER BY version") as cursor:
                    versions = [row[0] for row in await cursor.fetchall()]
            self.assertTrue({"attachment_type", "attachment_file_id", "attachment_file_name"} <= message_columns)
            self.assertIn("assigned_admin_id", ticket_columns)
            self.assertEqual(versions, [1, 2])
        finally:
            db.DB_PATH = current_path

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

    async def test_latest_user_action_query_is_scoped_and_health_report_reads_metrics(self):
        await db.log_action(1001, "Пользовательское действие")
        await db.log_action(1002, "Действие другого пользователя")
        latest = await db.get_latest_user_action(1001)
        self.assertEqual(latest["action"], "Пользовательское действие")

        from handlers.adminka import _build_bot_health_report

        report = await _build_bot_health_report()
        self.assertIn("База: <b>OK</b>", report)
        self.assertIn("Схема БД: версия 2", report)
        self.assertIn("пользователей: 2", report)

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

    async def test_fsm_state_and_data_survive_storage_recreation(self):
        key = StorageKey(bot_id=77, chat_id=1001, user_id=1001)
        storage = SQLiteFSMStorage(ttl_seconds=3600)
        await storage.set_state(key, "TicketStates:waiting_admin_reply")
        await storage.set_data(key, {"ticket_id": 12, "selected_format": "PNG → JPG"})

        restarted_storage = SQLiteFSMStorage(ttl_seconds=3600)
        self.assertEqual(await restarted_storage.get_state(key), "TicketStates:waiting_admin_reply")
        self.assertEqual(
            await restarted_storage.get_data(key),
            {"ticket_id": 12, "selected_format": "PNG → JPG"},
        )

    async def test_fsm_storage_expires_idle_sessions_and_scopes_all_key_fields(self):
        storage = SQLiteFSMStorage(ttl_seconds=60)
        first_key = StorageKey(bot_id=77, chat_id=1001, user_id=1001, destiny="default")
        other_key = StorageKey(bot_id=77, chat_id=1001, user_id=1001, destiny="admin")
        await storage.set_state(first_key, "state-one")
        await storage.set_state(other_key, "state-two")
        self.assertEqual(await storage.get_state(other_key), "state-two")

        async with aiosqlite.connect(db.DB_PATH) as connection:
            expired = (datetime.now() - timedelta(minutes=5)).timestamp()
            await connection.execute(
                "UPDATE fsm_sessions SET updated_at = ? WHERE storage_key = ?",
                (expired, storage._storage_key(first_key)),
            )
            await connection.commit()

        self.assertIsNone(await storage.get_state(first_key))
        self.assertEqual(await storage.get_state(other_key), "state-two")

    async def test_fsm_event_isolation_serializes_each_key_and_releases_locks(self):
        isolation = KeyedEventIsolation()
        key = StorageKey(bot_id=77, chat_id=1001, user_id=1001)
        entered = asyncio.Event()
        release = asyncio.Event()
        order = []

        async def first_update():
            async with isolation.lock(key):
                order.append("first-start")
                entered.set()
                await release.wait()
                order.append("first-end")

        async def second_update():
            async with isolation.lock(key):
                order.append("second")

        first = asyncio.create_task(first_update())
        await entered.wait()
        second = asyncio.create_task(second_update())
        await asyncio.sleep(0)
        self.assertEqual(order, ["first-start"])
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(order, ["first-start", "first-end", "second"])
        self.assertEqual(isolation._locks, {})

    async def test_ticket_handlers_cover_user_admin_reply_and_close_flow(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from aiogram.fsm.context import FSMContext
        from handlers import tickets

        await db.add_user(9001, "admin", "Admin", "")
        async with aiosqlite.connect(db.DB_PATH) as connection:
            await connection.execute("UPDATE users SET is_admin = 1 WHERE user_id = 9001")
            await connection.commit()

        storage = SQLiteFSMStorage(ttl_seconds=3600)
        user_state = FSMContext(
            storage=storage,
            key=StorageKey(bot_id=77, chat_id=1001, user_id=1001),
        )
        admin_state = FSMContext(
            storage=storage,
            key=StorageKey(bot_id=77, chat_id=9001, user_id=9001),
        )
        bot = _FakeTicketBot()

        def make_message(user_id, text, username):
            person = SimpleNamespace(
                id=user_id,
                username=username,
                first_name="Test",
                last_name="User",
                full_name="Test User",
            )
            return _FakeTicketMessage(person, text, bot)

        with patch("handlers.tickets.answer_editable", new=AsyncMock()):
            await tickets.start_ticket(make_message(1001, "", "tester"), user_state)
            await tickets.receive_ticket(
                make_message(1001, "Конвертация завершилась ошибкой", "tester"), user_state
            )
            ticket = (await db.get_support_tickets(user_id=1001))[0]

            admin_callback = _FakeTicketCallback(
                make_message(9001, "", "admin"), 9001,
                f"ticket:reply:{ticket['id']}", bot,
            )
            await tickets.admin_ticket_action(admin_callback, admin_state)
            await tickets.receive_admin_reply(
                make_message(9001, "Попробуйте отправить файл повторно", "admin"), admin_state
            )
            refreshed = await db.get_support_ticket(ticket["id"])
            self.assertEqual(refreshed["status"], "waiting_user")
            self.assertEqual(bot.sent[-1]["chat_id"], 1001)

            user_callback = _FakeTicketCallback(
                make_message(1001, "", "tester"), 1001,
                f"ticket:user_reply:{ticket['id']}", bot,
            )
            await tickets.start_user_ticket_reply(user_callback, user_state)
            await tickets.receive_user_ticket_reply(
                make_message(1001, "Спасибо, заработало", "tester"), user_state
            )
            self.assertEqual((await db.get_support_ticket(ticket["id"]))["status"], "open")

            close_callback = _FakeTicketCallback(
                make_message(9001, "", "admin"), 9001,
                f"ticket:close:{ticket['id']}", bot,
            )
            await tickets.admin_ticket_action(close_callback, admin_state)

        self.assertEqual((await db.get_support_ticket(ticket["id"]))["status"], "closed")
        self.assertEqual(
            [row["sender_role"] for row in await db.get_support_ticket_messages(ticket["id"])],
            ["user", "admin", "user"],
        )

    async def test_ticket_handler_accepts_and_notifies_about_document_attachment(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from aiogram.fsm.context import FSMContext
        from handlers import tickets

        await db.add_user(9001, "admin", "Admin", "")
        async with aiosqlite.connect(db.DB_PATH) as connection:
            await connection.execute("UPDATE users SET is_admin = 1 WHERE user_id = 9001")
            await connection.commit()

        storage = SQLiteFSMStorage()
        state = FSMContext(storage, StorageKey(bot_id=77, chat_id=1001, user_id=1001))
        bot = _FakeTicketBot()
        person = SimpleNamespace(
            id=1001, username="tester", first_name="Test", last_name="User", full_name="Test User"
        )
        with patch("handlers.tickets.answer_editable", new=AsyncMock()):
            await tickets.start_ticket(_FakeTicketMessage(person, "", bot), state)
            document_message = _FakeTicketMessage(person, None, bot)
            document_message.caption = "Лог ошибки"
            document_message.document = SimpleNamespace(
                file_id="telegram-doc-1", file_size=1024, file_name="error.log"
            )
            await tickets.receive_ticket(document_message, state)

        ticket = (await db.get_support_tickets(user_id=1001))[0]
        messages = await db.get_support_ticket_messages(ticket["id"])
        self.assertEqual(messages[0]["message"], "Лог ошибки")
        self.assertEqual(messages[0]["attachment_file_name"], "error.log")
        self.assertTrue(any(
            item.get("document") == "telegram-doc-1" and item["chat_id"] == 9001
            for item in bot.sent
        ))

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
