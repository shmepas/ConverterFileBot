import aiosqlite
from datetime import datetime

DB_PATH = "data_base/bot_database.db"

# ==============================
# Инициализация базы
# ==============================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TEXT
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
        # Таблица админов
        await db.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_at TEXT
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
# Логирование действий (минимальные корректировки)
# ==============================
async def log_action(user_id: int, action: str, username: str = None, first_name: str = None, last_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        # Добавляем пользователя в таблицу users, если его ещё нет
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if not exists:
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
# Админы
# ==============================
async def add_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            await db.execute("""
                INSERT INTO admins (user_id, added_at)
                VALUES (?, ?)
            """, (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await db.commit()

async def remove_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
        return result is not None

# ==============================
# Супер-админы
# ==============================
async def init_super_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM super_admin") as cursor:
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
# Автоинициализация базы при импорте
# ==============================
import asyncio
asyncio.get_event_loop().run_until_complete(init_db())