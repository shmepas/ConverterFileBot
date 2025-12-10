import aiosqlite
from datetime import datetime
import os
import sqlite3

DB_PATH = "data_base/bot_database.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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

async def add_user(user_id: int, username: str, first_name: str, last_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            await db.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, last_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
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
    except Exception as e:
        # Логируем ошибку в файл, но не прерываем выполнение
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/db_error.log", "a", encoding="utf-8") as f:
                f.write(f"DB Error in log_action: {e}\n")
        except Exception:
            pass

async def log_system_event(action: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pid = os.getpid()
    full_action = f"{action} | PID: {pid} | {timestamp}"
    await log_action(user_id=0, action=full_action)

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
    except Exception as e:
        # Логируем ошибку, но возвращаем пустой список
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/db_error.log", "a", encoding="utf-8") as f:
                f.write(f"DB Error in get_user_logs: {e}\n")
        except Exception:
            pass
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def remove_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (user_id,))
        await db.commit()

async def init_super_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM super_admin WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            await db.execute("""
                INSERT INTO super_admin (user_id, added_at)
                VALUES (?, ?)
            """, (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await db.commit()

async def is_super_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM super_admin WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
        return result is not None

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
    except Exception as e:
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/db_error.log", "a", encoding="utf-8") as f:
                f.write(f"DB Error in add_feedback: {e}\n")
        except Exception:
            pass
        return False

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
    except Exception as e:
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/db_error.log", "a", encoding="utf-8") as f:
                f.write(f"DB Error in get_feedbacks: {e}\n")
        except Exception:
            pass
        return []

async def delete_feedback(feedback_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM feedbacks WHERE id = ?", (feedback_id,))
        await db.commit()

async def update_feedback_status(feedback_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE feedbacks SET status = ? WHERE id = ?", (status, feedback_id))
        await db.commit()




# Инициализация базы данных удалена из импорта модуля
# Это должно происходить в основном коде приложения
