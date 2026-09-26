import aiosqlite
from datetime import datetime, timedelta
import logging
import os

DB_PATH = os.getenv("BOT_DB_PATH", os.path.join(os.path.dirname(__file__), "bot_database.db"))
logger = logging.getLogger(__name__)


class SupportTicketLimitReached(Exception):
    """Raised when a user reaches the active or daily ticket creation limit."""


async def _migration_add_fsm_sessions(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS fsm_sessions ("
        "storage_key TEXT PRIMARY KEY, state TEXT, data_json TEXT NOT NULL DEFAULT '{}', "
        "updated_at REAL NOT NULL)"
    )
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_fsm_sessions_updated ON fsm_sessions(updated_at)"
    )


async def _migration_add_ticket_workflow_fields(connection: aiosqlite.Connection) -> None:
    async with connection.execute("PRAGMA table_info(support_tickets)") as cursor:
        ticket_columns = {row[1] for row in await cursor.fetchall()}
    if "assigned_admin_id" not in ticket_columns:
        await connection.execute(
            "ALTER TABLE support_tickets ADD COLUMN assigned_admin_id INTEGER"
        )

    async with connection.execute("PRAGMA table_info(support_ticket_messages)") as cursor:
        message_columns = {row[1] for row in await cursor.fetchall()}
    attachment_columns = {
        "attachment_type": "TEXT CHECK (attachment_type IS NULL OR attachment_type IN ('photo', 'document'))",
        "attachment_file_id": "TEXT",
        "attachment_file_name": "TEXT",
    }
    for column, declaration in attachment_columns.items():
        if column not in message_columns:
            await connection.execute(
                f"ALTER TABLE support_ticket_messages ADD COLUMN {column} {declaration}"
            )
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_support_tickets_assigned_status "
        "ON support_tickets(assigned_admin_id, status, created_at DESC)"
    )


async def _run_schema_migrations(connection: aiosqlite.Connection) -> None:
    """Apply additive, numbered schema changes once per SQLite database."""
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    migrations = (
        (1, "persistent_fsm_sessions", _migration_add_fsm_sessions),
        (2, "ticket_attachments_and_assignment", _migration_add_ticket_workflow_fields),
    )
    for version, name, apply in migrations:
        async with connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ) as cursor:
            if await cursor.fetchone():
                continue
        await apply(connection)
        await connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await connection.commit()


async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TEXT,
            is_admin INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            timestamp TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS super_admin (
            user_id INTEGER PRIMARY KEY,
            added_at TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            type TEXT,
            description TEXT,
            date TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """)

        await db.commit()

        # Таблица обратной связи (feedback/support)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            email TEXT,
            message TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """)
        await db.commit()

        # Table to keep last bot message per user (to edit/delete and avoid clutter)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS last_bot_message (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER,
            message_id INTEGER,
            updated_at TEXT
        )
        """)
        await db.commit()

        # Subscriptions table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            plan_type TEXT DEFAULT 'free', -- 'free' or 'premium'
            is_active INTEGER DEFAULT 0, -- 1 for active, 0 for inactive
            start_date TEXT,
            end_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        await db.commit()

        # Daily conversion counters - исправленная версия
        await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_conversion_counters (
            user_id INTEGER,
            date TEXT, -- YYYY-MM-DD format
            conversion_count INTEGER DEFAULT 0,
            last_reset TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, date)
        )
        """)
        await db.commit()

        # Payment history for admin reference
        await db.execute("""
        CREATE TABLE IF NOT EXISTS payment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            payment_type TEXT, -- 'subscription', 'one_time'
            plan_duration TEXT, -- '1_month', '3_months', '1_year'
            status TEXT DEFAULT 'pending', -- 'pending', 'completed', 'failed'
            admin_notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_actions_user_timestamp ON user_actions(user_id, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_date ON payments(user_id, date DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payment_history_status_created ON payment_history(status, created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_active_end ON subscriptions(is_active, end_date)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            admin_reply TEXT,
            replied_by INTEGER,
            assigned_admin_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_support_tickets_status_created "
            "ON support_tickets(status, created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_support_tickets_user_created "
            "ON support_tickets(user_id, created_at DESC)"
        )
        await db.execute("""
        CREATE TABLE IF NOT EXISTS support_ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            sender_role TEXT NOT NULL CHECK (sender_role IN ('user', 'admin')),
            message TEXT NOT NULL,
            attachment_type TEXT CHECK (attachment_type IS NULL OR attachment_type IN ('photo', 'document')),
            attachment_file_id TEXT,
            attachment_file_name TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES support_tickets(id) ON DELETE CASCADE
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_support_ticket_messages_ticket "
            "ON support_ticket_messages(ticket_id, id)"
        )
        await db.execute("""
            INSERT INTO support_ticket_messages
                (ticket_id, sender_id, sender_role, message, created_at)
            SELECT t.id, t.user_id, 'user', t.message, t.created_at
            FROM support_tickets t
            WHERE NOT EXISTS (
                SELECT 1 FROM support_ticket_messages m WHERE m.ticket_id = t.id
            )
        """)
        await db.execute(
            "UPDATE support_tickets SET status = 'waiting_user' WHERE status = 'answered'"
        )
        await db.execute("""
            INSERT INTO support_ticket_messages
                (ticket_id, sender_id, sender_role, message, created_at)
            SELECT t.id, COALESCE(t.replied_by, 0), 'admin', t.admin_reply, t.updated_at
            FROM support_tickets t
            WHERE t.admin_reply IS NOT NULL AND t.admin_reply != ''
              AND NOT EXISTS (
                SELECT 1 FROM support_ticket_messages m
                WHERE m.ticket_id = t.id AND m.sender_role = 'admin'
              )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS retained_conversion_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            detected_type TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_retained_conversion_files_expiry "
            "ON retained_conversion_files(expires_at)"
        )
        await db.execute("""
        CREATE TABLE IF NOT EXISTS conversion_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            retained_file_id INTEGER,
            source_name TEXT NOT NULL,
            target_format TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('processing', 'success', 'failed', 'cancelled')),
            error_message TEXT,
            duration_seconds REAL,
            output_size INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (retained_file_id) REFERENCES retained_conversion_files(id) ON DELETE SET NULL
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS conversion_rate_limits (
            user_id INTEGER PRIMARY KEY,
            last_started REAL NOT NULL
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversion_history_user_created "
            "ON conversion_history(user_id, created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversion_history_status_created "
            "ON conversion_history(status, created_at DESC)"
        )
        await db.commit()

        # Миграция для обновления существующих таблиц
        try:
            # Проверяем, есть ли старая версия таблицы daily_conversion_counters
            cursor = await db.execute("PRAGMA table_info(daily_conversion_counters)")
            columns = await cursor.fetchall()

            primary_key_columns = [column for column in columns if column[5]]
            # Older versions keyed this table by user_id alone; the current
            # schema uses the composite key (user_id, date).
            if len(primary_key_columns) == 1 and primary_key_columns[0][1] == "user_id":
                logger.info("Migrating legacy daily conversion counters")
                column_names = {column[1] for column in columns}
                if "date" in column_names:
                    cursor = await db.execute(
                        "SELECT user_id, date, conversion_count FROM daily_conversion_counters"
                    )
                    old_rows = await cursor.fetchall()
                elif "last_reset" in column_names:
                    cursor = await db.execute(
                        "SELECT user_id, conversion_count, last_reset FROM daily_conversion_counters"
                    )
                    old_rows = await cursor.fetchall()
                else:
                    cursor = await db.execute(
                        "SELECT user_id, conversion_count FROM daily_conversion_counters"
                    )
                    old_rows = await cursor.fetchall()

                await db.execute("DROP TABLE IF EXISTS daily_conversion_counters")
                await db.execute("""
                CREATE TABLE daily_conversion_counters (
                    user_id INTEGER,
                    date TEXT,
                    conversion_count INTEGER DEFAULT 0,
                    last_reset TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, date)
                )
                """)
                today = datetime.now().strftime("%Y-%m-%d")
                for row in old_rows:
                    if "date" in column_names:
                        user_id, counter_date, count = row
                    elif "last_reset" in column_names:
                        user_id, count, last_reset = row
                        counter_date = (last_reset or today)[:10]
                    else:
                        user_id, count = row
                        counter_date = today
                    if counter_date:
                        await db.execute(
                            "INSERT OR REPLACE INTO daily_conversion_counters "
                            "(user_id, date, conversion_count) VALUES (?, ?, ?)",
                            (user_id, counter_date, count or 0),
                        )
                await db.commit()
                logger.info("Legacy daily conversion counters migrated")
        except Exception as e:
            logger.exception("Legacy daily counter migration failed")

        await _run_schema_migrations(db)

async def add_user(user_id: int, username: str, first_name: str, last_name: str):
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name
        """, (user_id, username or "", first_name or "", last_name or "",
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()

async def log_action(user_id: int, action: str, username: str = None, first_name: str = None, last_name: str = None):
    """Логирование действий с улучшенной обработкой ошибок."""
    # Валидация входных данных
    if not isinstance(user_id, int) or user_id < 0:
        return
    if not action or not isinstance(action, str):
        return

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Добавляем пользователя если его нет и есть данные
            if username or first_name or last_name:
                await add_user(user_id, username or "", first_name or "", last_name or "")

            # Логируем действие
            await db.execute("""
                INSERT INTO user_actions (user_id, action, timestamp)
                VALUES (?, ?, ?)
            """, (user_id, action[:500], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))  # Ограничиваем длину action
            await db.commit()
    except Exception:
        logger.exception("Failed to save user action")

async def log_system_event(action: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pid = os.getpid()
    full_action = f"{action} | PID: {pid} | {timestamp}"
    await log_action(user_id=0, action=full_action)


async def get_latest_user_action(user_id: int) -> dict | None:
    """Read only one user's last action for duplicate-update suppression."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT action, timestamp FROM user_actions WHERE user_id = ? "
            "ORDER BY timestamp DESC, id DESC LIMIT 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_user_logs(limit: int = 50):
    """Получение логов с улучшенной производительностью и валидацией."""
    # Валидация лимита
    if not isinstance(limit, int) or limit <= 0 or limit > 1000:
        limit = 50

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, user_id, action, timestamp FROM user_actions ORDER BY id DESC LIMIT ?",
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    except Exception:
        logger.exception("Failed to read user action logs")
        return []


async def set_last_bot_message(user_id: int, chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("REPLACE INTO last_bot_message (user_id, chat_id, message_id, updated_at) VALUES (?, ?, ?, ?)",
                         (user_id, chat_id, message_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()


async def get_last_bot_message(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id, message_id FROM last_bot_message WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {'chat_id': row[0], 'message_id': row[1]}
            return None


async def clear_last_bot_message(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM last_bot_message WHERE user_id = ?", (user_id,))
        await db.commit()

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

async def add_admin(user_id: int):
    """Добавляет пользователя в админы с проверками"""
    logger.debug("Admin grant requested for user_id=%s", user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем существование пользователя
        async with db.execute("SELECT user_id, is_admin FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user:
            logger.debug("Admin grant target was not found: user_id=%s", user_id)
            return False, "Пользователь не найден в базе данных"

        # Проверяем, не является ли уже админом
        if user[1] == 1:  # user[1] это is_admin
            logger.debug("Admin grant skipped for existing admin: user_id=%s", user_id)
            return False, "Пользователь уже является админом"

        # Добавляем в админы
        await db.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
        logger.info("Admin role granted to user_id=%s", user_id)

    return True, f"Пользователь {user_id} добавлен как админ"

async def remove_admin(user_id: int):
    """Удаляет пользователя из админов с проверками"""
    logger.debug("Admin removal requested for user_id=%s", user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем существование пользователя
        async with db.execute("SELECT user_id, is_admin FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user:
            logger.debug("Admin removal target was not found: user_id=%s", user_id)
            return False, "Пользователь не найден в базе данных"

        # Проверяем, является ли админом
        if user[1] == 0:  # user[1] это is_admin
            logger.debug("Admin removal skipped for non-admin user_id=%s", user_id)
            return False, "Пользователь не является админом"

        # Удаляем из админов
        await db.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (user_id,))
        await db.commit()
        logger.info("Admin role removed from user_id=%s", user_id)

    return True, f"Пользователь {user_id} удален из админов"

async def init_super_admin(user_id: int):
    logger.debug("Super-admin setup requested for user_id=%s", user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM super_admin WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            await db.execute("""
                INSERT INTO super_admin (user_id, added_at)
                VALUES (?, ?)
            """, (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await db.commit()
            logger.info("Super-admin initialized for user_id=%s", user_id)
        else:
            logger.debug("Super-admin already initialized for user_id=%s", user_id)

async def is_super_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM super_admin WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
    return result is not None


async def has_admin_access(user_id: int) -> bool:
    """Check either admin role in one database round trip."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        async with db.execute(
            "SELECT EXISTS(SELECT 1 FROM users WHERE user_id = ? AND is_admin = 1) "
            "OR EXISTS(SELECT 1 FROM super_admin WHERE user_id = ?)",
            (user_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])


async def get_admin_user_ids() -> list[int]:
    """Return all regular and super-admin IDs for support notifications."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE is_admin = 1 "
            "UNION SELECT user_id FROM super_admin ORDER BY user_id"
        ) as cursor:
            return [row[0] for row in await cursor.fetchall()]


def _prepare_ticket_message(message: str, attachment: dict | None = None) -> tuple[str, str | None, str | None, str | None]:
    clean_message = message.strip() if isinstance(message, str) else ""
    attachment_type = file_id = file_name = None
    if attachment is not None:
        if not isinstance(attachment, dict):
            raise ValueError("Invalid ticket attachment")
        attachment_type = attachment.get("type")
        file_id = attachment.get("file_id")
        file_name = attachment.get("file_name")
        if (
            attachment_type not in {"photo", "document"}
            or not isinstance(file_id, str)
            or not file_id
            or len(file_id) > 2048
        ):
            raise ValueError("Invalid ticket attachment")
        file_name = file_name[:255] if isinstance(file_name, str) and file_name else None
        if not clean_message:
            clean_message = f"Вложение: {file_name or attachment_type}"
    if not clean_message or len(clean_message) > 1800:
        raise ValueError("Ticket message must contain 1 to 1800 characters")
    return clean_message, attachment_type, file_id, file_name


async def create_support_ticket(user_id: int, message: str, *, attachment: dict | None = None) -> int:
    """Create a support ticket and return its ID."""
    clean_message, attachment_type, file_id, file_name = _prepare_ticket_message(message, attachment)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    recent_cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE user_id = ? "
            "AND status IN ('open', 'waiting_user')",
            (user_id,),
        ) as cursor:
            active_count = (await cursor.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE user_id = ? AND created_at >= ?",
            (user_id, recent_cutoff),
        ) as cursor:
            daily_count = (await cursor.fetchone())[0]
        if active_count >= 3 or daily_count >= 5:
            await db.rollback()
            raise SupportTicketLimitReached
        cursor = await db.execute(
            "INSERT INTO support_tickets "
            "(user_id, message, status, created_at, updated_at) "
            "VALUES (?, ?, 'open', ?, ?)",
            (user_id, clean_message, now, now),
        )
        ticket_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO support_ticket_messages "
            "(ticket_id, sender_id, sender_role, message, attachment_type, "
            "attachment_file_id, attachment_file_name, created_at) "
            "VALUES (?, ?, 'user', ?, ?, ?, ?, ?)",
            (ticket_id, user_id, clean_message, attachment_type, file_id, file_name, now),
        )
        await db.commit()
        return ticket_id


async def get_support_tickets(limit: int = 20, *, user_id: int | None = None,
                              include_closed: bool = False,
                              status: str | None = None) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    query = (
        "SELECT t.id, t.user_id, t.message, t.status, t.admin_reply, "
        "t.replied_by, t.assigned_admin_id, t.created_at, t.updated_at, u.username, u.first_name "
        "FROM support_tickets t LEFT JOIN users u ON u.user_id = t.user_id"
    )
    clauses: list[str] = []
    params: list[object] = []
    if user_id is not None:
        clauses.append("t.user_id = ?")
        params.append(user_id)
    if status in {"open", "waiting_user", "closed"}:
        clauses.append("t.status = ?")
        params.append(status)
    if not include_closed and status != "closed":
        clauses.append("t.status != 'closed'")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY t.created_at DESC, t.id DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_support_ticket(ticket_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT t.id, t.user_id, t.message, t.status, t.admin_reply, "
            "t.replied_by, t.assigned_admin_id, t.created_at, t.updated_at, u.username, u.first_name "
            "FROM support_tickets t LEFT JOIN users u ON u.user_id = t.user_id "
            "WHERE t.id = ?",
            (ticket_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_support_ticket_messages(ticket_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT sender_id, sender_role, message, attachment_type, "
            "attachment_file_id, attachment_file_name, created_at "
            "FROM support_ticket_messages WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def reply_to_support_ticket(
    ticket_id: int, admin_id: int, reply: str, *, attachment: dict | None = None
) -> bool:
    clean_reply, attachment_type, file_id, file_name = _prepare_ticket_message(reply, attachment)
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await db.execute(
            "UPDATE support_tickets SET status = 'waiting_user', admin_reply = ?, "
            "replied_by = ?, updated_at = ? WHERE id = ? AND status = 'open'",
            (clean_reply, admin_id, now, ticket_id),
        )
        if cursor.rowcount == 1:
            await db.execute(
                "INSERT INTO support_ticket_messages "
                "(ticket_id, sender_id, sender_role, message, attachment_type, "
                "attachment_file_id, attachment_file_name, created_at) "
                "VALUES (?, ?, 'admin', ?, ?, ?, ?, ?)",
                (ticket_id, admin_id, clean_reply, attachment_type, file_id, file_name, now),
            )
        await db.commit()
        return cursor.rowcount == 1


async def user_reply_to_support_ticket(
    ticket_id: int, user_id: int, message: str, *, attachment: dict | None = None
) -> bool:
    clean_message, attachment_type, file_id, file_name = _prepare_ticket_message(message, attachment)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "UPDATE support_tickets SET status = 'open', updated_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'waiting_user'",
            (now, ticket_id, user_id),
        )
        if cursor.rowcount == 1:
            await db.execute(
                "INSERT INTO support_ticket_messages "
                "(ticket_id, sender_id, sender_role, message, attachment_type, "
                "attachment_file_id, attachment_file_name, created_at) "
                "VALUES (?, ?, 'user', ?, ?, ?, ?, ?)",
                (ticket_id, user_id, clean_message, attachment_type, file_id, file_name, now),
            )
        await db.commit()
        return cursor.rowcount == 1


async def get_open_support_ticket_count() -> int:
    """Return the number of tickets currently waiting for an admin."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE status = 'open'"
        ) as cursor:
            return (await cursor.fetchone())[0]


async def claim_support_ticket(ticket_id: int, admin_id: int) -> str | None:
    """Claim an unassigned ticket or release one's own assignment atomically."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT status, assigned_admin_id FROM support_tickets WHERE id = ?",
            (ticket_id,),
        ) as cursor:
            ticket = await cursor.fetchone()
        async with db.execute(
            "SELECT EXISTS(SELECT 1 FROM users WHERE user_id = ? AND is_admin = 1) "
            "OR EXISTS(SELECT 1 FROM super_admin WHERE user_id = ?)",
            (admin_id, admin_id),
        ) as cursor:
            is_admin_user = (await cursor.fetchone())[0]
        if not ticket or not is_admin_user or ticket[0] == "closed":
            await db.rollback()
            return None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if ticket[1] is None:
            await db.execute(
                "UPDATE support_tickets SET assigned_admin_id = ?, updated_at = ? WHERE id = ?",
                (admin_id, now, ticket_id),
            )
            result = "assigned"
        elif ticket[1] == admin_id:
            await db.execute(
                "UPDATE support_tickets SET assigned_admin_id = NULL, updated_at = ? WHERE id = ?",
                (now, ticket_id),
            )
            result = "released"
        else:
            await db.rollback()
            return None
        await db.commit()
        return result


async def close_support_ticket(ticket_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "UPDATE support_tickets SET status = 'closed', updated_at = ? "
            "WHERE id = ? AND status != 'closed'",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ticket_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def add_retained_conversion_file(
    user_id: int, source_name: str, detected_type: str, stored_path: str,
    retention_hours: int = 24,
) -> int:
    now = datetime.now()
    expires_at = now + timedelta(hours=max(1, retention_hours))
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "INSERT INTO retained_conversion_files "
            "(user_id, source_name, detected_type, stored_path, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, source_name[:255], detected_type, stored_path,
             now.strftime("%Y-%m-%d %H:%M:%S"), expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
        await db.commit()
        return cursor.lastrowid


async def get_retained_conversion_file(file_id: int, user_id: int) -> dict | None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, source_name, detected_type, stored_path, expires_at "
            "FROM retained_conversion_files WHERE id = ? AND user_id = ? AND expires_at > ?",
            (file_id, user_id, now),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_conversion_history(
    user_id: int, source_name: str, target_format: str,
    retained_file_id: int | None = None,
) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "INSERT INTO conversion_history "
            "(user_id, retained_file_id, source_name, target_format, status, created_at) "
            "VALUES (?, ?, ?, ?, 'processing', ?)",
            (user_id, retained_file_id, source_name[:255], target_format, now),
        )
        await db.commit()
        return cursor.lastrowid


async def update_conversion_history(
    history_id: int, user_id: int, status: str, *, error_message: str | None = None,
    duration_seconds: float | None = None, output_size: int | None = None,
) -> bool:
    if status not in {"success", "failed", "cancelled"}:
        raise ValueError("Invalid conversion history status")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "UPDATE conversion_history SET status = ?, error_message = ?, "
            "duration_seconds = ?, output_size = ? WHERE id = ? AND user_id = ?",
            (status, (error_message or "")[:500] or None, duration_seconds,
             output_size, history_id, user_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def get_conversion_performance_stats(days: int = 90) -> dict:
    """Aggregate completed conversion jobs from persistent history."""
    days = max(1, min(int(days), 3650))
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) AS total_conversions, "
            "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful_conversions, "
            "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_conversions, "
            "AVG(CASE WHEN status = 'success' THEN duration_seconds END) AS average_time "
            "FROM conversion_history "
            "WHERE status IN ('success', 'failed') AND created_at >= ?",
            (cutoff,),
        ) as cursor:
            totals = dict(await cursor.fetchone())

        async with db.execute(
            "SELECT target_format, COUNT(*) AS count, "
            "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful_count, "
            "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count, "
            "COALESCE(SUM(CASE WHEN status = 'success' "
            "THEN duration_seconds ELSE 0 END), 0) AS total_time "
            "FROM conversion_history "
            "WHERE status IN ('success', 'failed') AND created_at >= ? "
            "GROUP BY target_format "
            "ORDER BY successful_count DESC, count DESC, target_format COLLATE NOCASE",
            (cutoff,),
        ) as cursor:
            formats = {
                row["target_format"]: {
                    "count": row["count"],
                    "successful_count": row["successful_count"],
                    "failed_count": row["failed_count"],
                    "total_time": row["total_time"],
                }
                for row in await cursor.fetchall()
            }

    return {
        "total_conversions": totals["total_conversions"] or 0,
        "successful_conversions": totals["successful_conversions"] or 0,
        "failed_conversions": totals["failed_conversions"] or 0,
        "average_time": totals["average_time"] or 0.0,
        "formats_used": formats,
    }


async def get_user_conversion_history(user_id: int, limit: int = 10) -> list[dict]:
    limit = max(1, min(int(limit), 50))
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT h.id, h.source_name, h.target_format, h.status, h.error_message, "
            "h.duration_seconds, h.output_size, h.created_at, "
            "r.id AS retained_file_id, r.expires_at "
            "FROM conversion_history h LEFT JOIN retained_conversion_files r "
            "ON r.id = h.retained_file_id AND r.expires_at > ? "
            "WHERE h.user_id = ? ORDER BY h.created_at DESC, h.id DESC LIMIT ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id, limit),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_conversion_history_entry(history_id: int, user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT h.id, h.user_id, h.source_name, h.target_format, h.status, "
            "r.id AS retained_file_id, r.stored_path, r.detected_type, r.expires_at "
            "FROM conversion_history h JOIN retained_conversion_files r "
            "ON r.id = h.retained_file_id "
            "WHERE h.id = ? AND h.user_id = ? AND h.status = 'success' "
            "AND r.expires_at > ?",
            (history_id, user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def cleanup_expired_conversion_history(retention_days: int = 90) -> list[dict]:
    """Expire retained source files and old history, preserving metrics for the retention period."""
    now = datetime.now()
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")
    cutoff = (now - timedelta(days=max(1, retention_days))).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, stored_path FROM retained_conversion_files WHERE expires_at <= ?",
            (now_text,),
        ) as cursor:
            expired = [dict(row) for row in await cursor.fetchall()]
        expired_ids = [row["id"] for row in expired]
        for offset in range(0, len(expired_ids), 400):
            batch = expired_ids[offset:offset + 400]
            placeholders = ",".join("?" for _ in batch)
            await db.execute(
                f"UPDATE conversion_history SET retained_file_id = NULL "
                f"WHERE retained_file_id IN ({placeholders})",
                batch,
            )
            await db.execute(
                f"DELETE FROM retained_conversion_files WHERE id IN ({placeholders})",
                batch,
            )
        await db.execute("DELETE FROM conversion_history WHERE created_at < ?", (cutoff,))
        await db.commit()
        return expired


async def claim_conversion_cooldown(user_id: int, cooldown_seconds: int = 5) -> bool:
    """Atomically enforce a small cooldown between a user's conversion jobs."""
    import time

    now = time.time()
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "UPDATE conversion_rate_limits SET last_started = ? "
            "WHERE user_id = ? AND last_started <= ?",
            (now, user_id, now - max(1, cooldown_seconds)),
        )
        if cursor.rowcount == 1:
            await db.commit()
            return True
        cursor = await db.execute(
            "INSERT OR IGNORE INTO conversion_rate_limits (user_id, last_started) VALUES (?, ?)",
            (user_id, now),
        )
        await db.commit()
        return cursor.rowcount == 1

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, username, first_name, last_name, is_admin FROM users ORDER BY user_id") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

# -----------------------------
# Платежи
# -----------------------------
async def add_payment(user_id: int, amount: float, payment_type: str, description: str):
    """Добавляет запись о платеже (асинхронно)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO payments (user_id, amount, type, description, date)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, amount, payment_type, description, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()

async def get_user_payments(user_id: int, limit: int = 50):
    """Возвращает последние платежи пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT amount, type, description, date FROM payments WHERE user_id = ? ORDER BY date DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_payments(limit: int = 500):
    """Returns recent payments for the admin payment history screen."""
    limit = max(1, min(int(limit), 1000))
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, amount, type, description, date "
            "FROM payments ORDER BY date DESC, id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


        # -----------------------------
# Обратная связь / поддержка
# -----------------------------
async def add_feedback(name: str, email: str, message: str, user_id: int | None = None):
    """Добавление обратной связи с валидацией данных."""
    # Валидация входных данных
    if not name or not isinstance(name, str) or len(name.strip()) == 0:
        return False
    if not email or not isinstance(email, str) or "@" not in email:
        return False
    if not message or not isinstance(message, str) or len(message.strip()) == 0:
        return False

    # Ограничиваем длину полей
    name = name.strip()[:100]
    email = email.strip()[:200]
    message = message.strip()[:1000]

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO feedbacks (user_id, name, email, message, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, name, email, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await db.commit()
        return True
    except Exception:
        logger.exception("Failed to save user feedback")
        return False

async def reset_daily_counters():
    """Сбрасывает счетчики конвертаций (для cron или ручного запуска)."""
    from datetime import datetime
    yesterday = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM daily_conversion_counters WHERE date < ?", (yesterday,))
        await db.commit()
        logger.debug("Daily conversion counters before %s removed", yesterday)

async def get_feedbacks(limit: int = 100):
    """Получение обратной связи с улучшенной производительностью."""
    # Валидация лимита
    if not isinstance(limit, int) or limit <= 0 or limit > 1000:
        limit = 100

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Добавляем индекс для ускорения (если его нет)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_feedbacks_id ON feedbacks(id)")

            async with db.execute(
                "SELECT id, user_id, name, email, message, status, created_at FROM feedbacks ORDER BY id DESC LIMIT ?",
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    except Exception:
        logger.exception("Failed to read feedback entries")
        return []

async def delete_feedback(feedback_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM feedbacks WHERE id = ?", (feedback_id,))
        await db.commit()

async def update_feedback_status(feedback_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE feedbacks SET status = ? WHERE id = ?", (status, feedback_id))
        await db.commit()




# ==============================
# SUBSCRIPTION MANAGEMENT
# ==============================

async def get_all_subscriptions(limit: int = 100):
    """Получает список всех подписок для админа."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT s.user_id, u.username, u.first_name, s.plan_type, s.is_active,
                   s.start_date, s.end_date, s.created_at
            FROM subscriptions s
            JOIN users u ON s.user_id = u.user_id
            ORDER BY s.created_at DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_pending_payments(limit: int = 50):
    """Получает список ожидающих платежей для админа."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT ph.*, u.username, u.first_name
            FROM payment_history ph
            JOIN users u ON ph.user_id = u.user_id
            WHERE ph.status = 'pending'
            ORDER BY ph.created_at DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def mark_payment_completed(payment_id: int, admin_user_id: int, admin_notes: str = ""):
    """Отмечает платеж как завершенный и активирует подписку."""
    from datetime import datetime

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем информацию о платеже
        async with db.execute(
            "SELECT user_id, plan_duration FROM payment_history WHERE id = ? AND status = 'pending'",
            (payment_id,)
        ) as cursor:
            payment = await cursor.fetchone()
            if not payment:
                return False, "Платеж не найден или уже обработан"

        user_id, plan_duration = payment

        # Определяем длительность подписки
        duration_months = 1
        if plan_duration == "3_months":
            duration_months = 3
        elif plan_duration == "1_year":
            duration_months = 12

        # Активируем подписку
        await activate_premium_subscription(user_id, duration_months, admin_notes)

        # Обновляем статус платежа
        await db.execute("""
            UPDATE payment_history
            SET status = 'completed', admin_notes = ?, processed_at = ?
            WHERE id = ?
        """, (admin_notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), payment_id))
        await db.commit()

        return True, f"Подписка активирована для пользователя {user_id}"

async def activate_premium_subscription(user_id: int, duration_months: int = 1, admin_notes: str = ""):
    """Активирует премиум подписку для пользователя."""
    from datetime import datetime, timedelta

    logger.info("Premium activation started for user_id=%s duration_months=%s", user_id, duration_months)

    start_date = datetime.now()
    end_date = start_date + timedelta(days=30 * duration_months)

    async with aiosqlite.connect(DB_PATH) as db:
        # Сначала проверяем, есть ли пользователь в базе
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user_exists = await cursor.fetchone()
            if not user_exists:
                logger.debug("Creating user row for subscription target user_id=%s", user_id)
                # Создаём пользователя если его нет
                await db.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name, created_at, is_admin)
                    VALUES (?, '', '', '', ?, 0)
                """, (user_id, start_date.strftime("%Y-%m-%d %H:%M:%S")))

        # Активируем подписку
        await db.execute("""
            INSERT OR REPLACE INTO subscriptions
            (user_id, plan_type, is_active, start_date, end_date, updated_at)
            VALUES (?, 'premium', 1, ?, ?, CURRENT_TIMESTAMP)
        """, (user_id, start_date.strftime("%Y-%m-%d %H:%M:%S"), end_date.strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()
        logger.info("Premium subscription activated for user_id=%s", user_id)

        # Логируем активацию
        await db.execute("""
            INSERT INTO payment_history (user_id, amount, payment_type, plan_duration, status, admin_notes, processed_at)
            VALUES (?, 0, 'subscription', ?, 'completed', ?, ?)
        """, (user_id, f"{duration_months}_months", admin_notes, start_date.strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()
        logger.debug("Subscription payment history recorded for user_id=%s", user_id)

        # Также создаём бесплатную подписку по умолчанию для новых пользователей
        await db.execute("""
            INSERT OR IGNORE INTO subscriptions (user_id, plan_type, is_active)
            VALUES (?, 'free', 0)
        """, (user_id,))
        await db.commit()

async def deactivate_subscription(user_id: int):
    """Деактивирует подписку пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE subscriptions SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (user_id,))
        await db.commit()

async def get_user_subscription(user_id: int) -> dict:
    """Получает информацию о подписке пользователя."""
    logger.debug("Reading subscription for user_id=%s", user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT plan_type, is_active, start_date, end_date FROM subscriptions WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                subscription = dict(row)
                logger.debug("Subscription found for user_id=%s plan=%s", user_id, subscription["plan_type"])
                return subscription
            else:
                logger.debug("Creating default free subscription for user_id=%s", user_id)
                # Создаем бесплатную подписку по умолчанию
                await create_free_subscription(user_id)
                subscription = {
                    'plan_type': 'free',
                    'is_active': 0,
                    'start_date': None,
                    'end_date': None
                }
                return subscription

async def create_free_subscription(user_id: int):
    """Создает бесплатную подписку для нового пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO subscriptions (user_id, plan_type, is_active)
            VALUES (?, 'free', 0)
        """, (user_id,))
        await db.commit()

async def check_user_limits(user_id: int) -> dict:
    """Проверяет лимиты пользователя и возвращает информацию о подписке."""
    subscription = await get_user_subscription(user_id)

    if subscription['plan_type'] == 'premium' and subscription['is_active']:
        # Премиум подписка - без ограничений
        return {
            'plan_type': 'premium',
            'is_premium': True,
            'daily_limit': -1,  # Безлимит
            'current_count': 0,
            'remaining': -1,
            'max_file_size': 100 * 1024 * 1024,  # 100 МБ
            'supported_formats': 'all'
        }
    else:
        # Бесплатная подписка - с ограничениями
        today_count = await get_daily_conversion_count(user_id)

        limits = {
            'plan_type': 'free',
            'is_premium': False,
            'daily_limit': 5,
            'current_count': today_count,
            'remaining': max(0, 5 - today_count),
            'max_file_size': 20 * 1024 * 1024,  # 20 МБ
            'supported_formats': 'basic'  # Основные форматы
        }
        return limits

async def get_daily_conversion_count(user_id: int) -> int:
    """Получает количество конвертаций пользователя за сегодня."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        async with db.execute(
            "SELECT conversion_count FROM daily_conversion_counters WHERE user_id = ? AND date = ?",
            (user_id, today),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

async def increment_conversion_count(user_id: int) -> bool:
    """Atomically checks the daily quota and increments the counter."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT plan_type, is_active FROM subscriptions WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                subscription = await cursor.fetchone()
            is_premium = bool(subscription and subscription[0] == "premium" and subscription[1])

            if not is_premium:
                async with db.execute(
                    "SELECT conversion_count FROM daily_conversion_counters WHERE user_id = ? AND date = ?",
                    (user_id, today),
                ) as cursor:
                    row = await cursor.fetchone()
                if row and int(row[0]) >= 5:
                    await db.rollback()
                    return False

            await db.execute("""
                INSERT INTO daily_conversion_counters (user_id, date, conversion_count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    conversion_count = conversion_count + 1,
                    last_reset = CURRENT_TIMESTAMP
            """, (user_id, today))
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def decrement_conversion_count(user_id: int) -> None:
    """Refund a quota unit when a reserved conversion does not complete."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "UPDATE daily_conversion_counters "
            "SET conversion_count = CASE WHEN conversion_count > 0 THEN conversion_count - 1 ELSE 0 END "
            "WHERE user_id = ? AND date = ?",
            (user_id, today),
        )
        await db.commit()

async def get_subscription_status_text(user_id: int) -> str:
    """Возвращает текст статуса подписки для отображения пользователю."""

    subscription = await get_user_subscription(user_id)

    limits = await check_user_limits(user_id)

    if limits['is_premium']:
        return f"""💎 **Премиум подписка**

✅ Безлимитные конвертации
✅ Файлы до 100 МБ
✅ Все форматы доступны
✅ Приоритетная обработка
✅ Расширенные функции"""
    else:
        status_text = f"""🔄 **Бесплатный тариф**

📊 Сегодня использовано: {limits['current_count']}/{limits['daily_limit']}
📁 Осталось: {limits['remaining']} конвертаций
📄 Максимальный размер: 20 МБ
🎯 Основные форматы доступны

💎 Для безлимитного доступа оформите подписку!"""
        return status_text

# Инициализация базы данных удалена из импорта модуля
# Это должно происходить в основном коде приложения
