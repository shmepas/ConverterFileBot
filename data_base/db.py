import aiosqlite
from datetime import datetime
import os

DB_PATH = "data_base/bot_database.db"

# ==============================
# Инициализация базы
# ==============================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей с полем is_admin
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
        # Таблица действий пользователей (логи)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            timestamp TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """)
        # Таблица супер-админов
        await db.execute("""
        CREATE TABLE IF NOT EXISTS super_admin (
            user_id INTEGER PRIMARY KEY,
            added_at TEXT
        )
        """)
        await db.commit()

# ==============================
# Пользователи
# ==============================
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

# ==============================
# Логирование действий
# ==============================
async def log_action(user_id: int, action: str, username: str = None, first_name: str = None, last_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        # Добавляем пользователя, если его нет
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if not exists and (username or first_name or last_name):
            await db.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, last_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

        # Логируем действие
        await db.execute("""
            INSERT INTO user_actions (user_id, action, timestamp)
            VALUES (?, ?, ?)
        """, (user_id, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()

# ==============================
# Логирование системных событий (запуск/остановка/перезапуск)
# ==============================
async def log_system_event(action: str):
    """
    Логирует события системы (user_id=0), добавляет PID и timestamp.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pid = os.getpid()
    full_action = f"{action} | PID: {pid} | {timestamp}"
    await log_action(user_id=0, action=full_action)

# ==============================
# Получение логов
# ==============================
async def get_user_logs(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, action, timestamp FROM user_actions ORDER BY id DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

# ==============================
# Админы через поле is_admin в users
# ==============================
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

# ==============================
# Супер-админы
# ==============================
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

# ==============================
# Получение всех пользователей
# ==============================
async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, username, first_name, last_name, is_admin FROM users ORDER BY user_id") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

# ==============================
# Автоинициализация базы при импорте
# ==============================
import asyncio
asyncio.get_event_loop().run_until_complete(init_db())
