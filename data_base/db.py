import sqlite3
from datetime import datetime

DB_PATH = "data_base/bot_database.db"

# 🔹 Подключение
def get_connection():
    return sqlite3.connect(DB_PATH)


# 🔹 Инициализация базы
def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        created_at TEXT
    )
    """)

    # Таблица действий
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        timestamp TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    """)

    # Таблица админов
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        added_at TEXT
    )
    """)

    # Таблица супер-админа
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS super_admin (
        user_id INTEGER PRIMARY KEY,
        added_at TEXT
    )
    """)

    conn.commit()
    conn.close()


# 🔹 Пользователи
def add_user(user_id: int, username: str, first_name: str, last_name: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, first_name, last_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    conn.commit()
    conn.close()


# 🔹 Лог действий
def log_action(user_id: int, action: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_actions (user_id, action, timestamp)
        VALUES (?, ?, ?)
    """, (user_id, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


# 🔹 Админы
def add_admin(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO admins (user_id, added_at)
            VALUES (?, ?)
        """, (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def remove_admin(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone() is not None
    conn.close()
    return result


# 🔹 Супер-админ
def init_super_admin(user_id: int):
    """Создаёт супер-админа, если таблица пуста"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM super_admin")
    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO super_admin (user_id, added_at)
            VALUES (?, ?)
        """, (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def is_super_admin(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM super_admin WHERE user_id = ?", (user_id,))
    result = cursor.fetchone() is not None
    conn.close()
    return result


# 🔹 Получение логов пользователей
def get_user_logs(limit: int = 50):
    """
    Возвращает последние действия пользователей.
    :param limit: сколько последних записей вернуть
    :return: список кортежей (id, user_id, action, timestamp)
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, action, timestamp FROM user_actions ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    logs = cursor.fetchall()
    conn.close()
    return logs


async def get_logs():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT 20")
        logs = await cursor.fetchall()
        return [dict(log) for log in logs]