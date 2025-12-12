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
        await db.commit()

        # Миграция для обновления существующих таблиц
        try:
            # Проверяем, есть ли старая версия таблицы daily_conversion_counters
            cursor = await db.execute("PRAGMA table_info(daily_conversion_counters)")
            columns = await cursor.fetchall()
            
            # Если user_id является PRIMARY KEY, пересоздаём таблицу
            if columns and any(col[5] == 1 for col in columns if col[1] == 'user_id'):  # col[5] - is_pk
                print("Миграция таблицы daily_conversion_counters...")
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
                await db.commit()
                print("Миграция завершена")
        except Exception as e:
            print(f"Ошибка миграции: {e}")

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
    result = False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            result = bool(row[0]) if row else False
    print(f"[ADMIN DEBUG] is_admin check for user_id: {user_id} -> {result}")
    return result

async def add_admin(user_id: int):
    print(f"[ADMIN DEBUG] add_admin called for user_id: {user_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
    print(f"[ADMIN DEBUG] add_admin completed for user_id: {user_id}")

async def remove_admin(user_id: int):
    print(f"[ADMIN DEBUG] remove_admin called for user_id: {user_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (user_id,))
        await db.commit()
    print(f"[ADMIN DEBUG] remove_admin completed for user_id: {user_id}")

async def init_super_admin(user_id: int):
    print(f"[ADMIN DEBUG] init_super_admin called for user_id: {user_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM super_admin WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            await db.execute("""
                INSERT INTO super_admin (user_id, added_at)
                VALUES (?, ?)
            """, (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await db.commit()
            print(f"[ADMIN DEBUG] super_admin added for user_id: {user_id}")
        else:
            print(f"[ADMIN DEBUG] super_admin already exists for user_id: {user_id}")

async def is_super_admin(user_id: int) -> bool:
    result = False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM super_admin WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
    is_super = result is not None
    print(f"[ADMIN DEBUG] is_super_admin check for user_id: {user_id} -> {is_super}")
    return is_super

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

async def reset_daily_counters():
    """Сбрасывает счетчики конвертаций (для cron или ручного запуска)."""
    from datetime import datetime
    yesterday = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).strftime("%Y-%m-%d")
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM daily_conversion_counters WHERE date < ?", (yesterday,))
        await db.commit()
        print(f"[RESET DEBUG] Daily counters reset for dates before {yesterday}")
        
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
    
    print(f"[SUBSCRIPTION DEBUG] Activating premium subscription for user_id: {user_id}, duration: {duration_months} months")
    
    start_date = datetime.now()
    end_date = start_date + timedelta(days=30 * duration_months)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Сначала проверяем, есть ли пользователь в базе
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user_exists = await cursor.fetchone()
            if not user_exists:
                print(f"[SUBSCRIPTION DEBUG] User {user_id} not found in users table, creating...")
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
        print(f"[SUBSCRIPTION DEBUG] Subscription activated for user_id: {user_id}")
        
        # Логируем активацию
        await db.execute("""
            INSERT INTO payment_history (user_id, amount, payment_type, plan_duration, status, admin_notes, processed_at)
            VALUES (?, 0, 'subscription', ?, 'completed', ?, ?)
        """, (user_id, f"{duration_months}_months", admin_notes, start_date.strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()
        print(f"[SUBSCRIPTION DEBUG] Payment history logged for user_id: {user_id}")

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
    print(f"[DEBUG SUBSCRIPTION] get_user_subscription called for user {user_id}")
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT plan_type, is_active, start_date, end_date FROM subscriptions WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                subscription = dict(row)
                print(f"[DEBUG SUBSCRIPTION] Found existing subscription for user {user_id}: {subscription}")
                return subscription
            else:
                print(f"[DEBUG SUBSCRIPTION] No subscription found for user {user_id}, creating free subscription")
                # Создаем бесплатную подписку по умолчанию
                await create_free_subscription(user_id)
                subscription = {
                    'plan_type': 'free',
                    'is_active': 0,
                    'start_date': None,
                    'end_date': None
                }
                print(f"[DEBUG SUBSCRIPTION] Created free subscription for user {user_id}: {subscription}")
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
    print(f"[DEBUG LIMITS] check_user_limits called for user {user_id}")
    
    subscription = await get_user_subscription(user_id)
    print(f"[DEBUG LIMITS] User subscription: {subscription}")
    
    if subscription['plan_type'] == 'premium' and subscription['is_active']:
        print(f"[DEBUG LIMITS] User {user_id} has active premium subscription")
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
        print(f"[DEBUG LIMITS] User {user_id} is free user, checking conversion count")
        # Бесплатная подписка - с ограничениями
        today_count = await get_daily_conversion_count(user_id)
        print(f"[DEBUG LIMITS] Today's conversion count for user {user_id}: {today_count}")
        
        limits = {
            'plan_type': 'free',
            'is_premium': False,
            'daily_limit': 5,
            'current_count': today_count,
            'remaining': max(0, 5 - today_count),
            'max_file_size': 20 * 1024 * 1024,  # 20 МБ
            'supported_formats': 'basic'  # Основные форматы
        }
        print(f"[DEBUG LIMITS] Final limits for user {user_id}: {limits}")
        return limits

async def get_daily_conversion_count(user_id: int) -> int:
    """Получает количество конвертаций пользователя за сегодня."""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    
    print(f"[DEBUG COUNT] get_daily_conversion_count called for user {user_id}, today: {today}")
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Сначала проверим, есть ли таблица и какие в ней данные
            print(f"[DEBUG COUNT] Checking table structure...")
            async with db.execute("PRAGMA table_info(daily_conversion_counters)") as cursor:
                columns = await cursor.fetchall()
                print(f"[DEBUG COUNT] Table columns: {columns}")
            
            # Проверим все записи для этого пользователя
            print(f"[DEBUG COUNT] Checking all records for user {user_id}")
            async with db.execute("SELECT * FROM daily_conversion_counters WHERE user_id = ?", (user_id,)) as cursor:
                all_records = await cursor.fetchall()
                print(f"[DEBUG COUNT] All records for user {user_id}: {all_records}")
            
            # Проверим записи за сегодня
            print(f"[DEBUG COUNT] Checking records for today: {today}")
            async with db.execute(
                "SELECT conversion_count FROM daily_conversion_counters WHERE user_id = ? AND date = ?",
                (user_id, today)
            ) as cursor:
                row = await cursor.fetchone()
                result = row[0] if row else 0
                print(f"[DEBUG COUNT] Found row: {row}, returning: {result}")
                return result
                
    except Exception as e:
        print(f"[DEBUG COUNT] Error in get_daily_conversion_count: {e}")
        return 0

async def increment_conversion_count(user_id: int) -> bool:
    """Увеличивает счетчик конвертаций. Возвращает False если лимит исчерпан."""
    from datetime import datetime
    
    print(f"[CONVERSION DEBUG] Checking limits for user_id: {user_id}")
    
    # Проверяем лимиты перед увеличением
    limits = await check_user_limits(user_id)
    print(f"[CONVERSION DEBUG] User limits: {limits}")
    
    if not limits['is_premium'] and limits['current_count'] >= limits['daily_limit']:
        print(f"[CONVERSION DEBUG] User {user_id} reached daily limit")
        return False  # Лимит исчерпан
    
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[CONVERSION DEBUG] Today's date: {today}")
    
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            print(f"[CONVERSION DEBUG] Attempting to increment conversion count for user {user_id} on {today}")
            
            # Сначала проверяем, есть ли запись
            async with db.execute("SELECT conversion_count FROM daily_conversion_counters WHERE user_id = ? AND date = ?", (user_id, today)) as cursor:
                existing = await cursor.fetchone()
                print(f"[CONVERSION DEBUG] Existing record: {existing}")
            
            # Используем правильный синтаксис SQLite для UPSERT
            await db.execute("""
                INSERT INTO daily_conversion_counters (user_id, date, conversion_count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    conversion_count = conversion_count + 1,
                    last_reset = CURRENT_TIMESTAMP
            """, (user_id, today))
            
            await db.commit()
            print(f"[CONVERSION DEBUG] Successfully incremented count for user_id: {user_id}")
            
        except Exception as e:
            print(f"[CONVERSION DEBUG] UPSERT failed, trying alternative approach: {e}")
            # Если UPSERT не работает, используем альтернативный подход
            try:
                # Сначала пытаемся обновить существующую запись
                result = await db.execute("""
                    UPDATE daily_conversion_counters 
                    SET conversion_count = conversion_count + 1, 
                        last_reset = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND date = ?
                """, (user_id, today))
                await db.commit()
            
                # Если ничего не обновилось, создаём новую запись
                if db.total_changes == 0:
                    print(f"[CONVERSION DEBUG] No existing record found, inserting new one")
                    await db.execute("""
                        INSERT INTO daily_conversion_counters (user_id, date, conversion_count)
                        VALUES (?, ?, 1)
                    """, (user_id, today))
                    await db.commit()
                
                print(f"[CONVERSION DEBUG] Alternative approach completed for user_id: {user_id}")
                
            except Exception as e2:
                print(f"[CONVERSION DEBUG] Alternative approach also failed: {e2}")
                return False
    
    # Проверяем результат
    final_count = await get_daily_conversion_count(user_id)
    print(f"[CONVERSION DEBUG] Final count after increment: {final_count}")
    
    return True

async def get_subscription_status_text(user_id: int) -> str:
    """Возвращает текст статуса подписки для отображения пользователю."""
    print(f"[DEBUG STATUS] get_subscription_status_text called for user {user_id}")
    
    subscription = await get_user_subscription(user_id)
    print(f"[DEBUG STATUS] User subscription: {subscription}")
    
    limits = await check_user_limits(user_id)
    print(f"[DEBUG STATUS] Final limits for status text: {limits}")
    
    if limits['is_premium']:
        print(f"[DEBUG STATUS] Returning premium status for user {user_id}")
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
        print(f"[DEBUG STATUS] Returning free status for user {user_id}: {limits['current_count']}/{limits['daily_limit']}")
        return status_text

# Инициализация базы данных удалена из импорта модуля
# Это должно происходить в основном коде приложения
