import sqlite3
import hashlib

DATABASE = 'admins.db'

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_admin(username, password):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    password_hash = hash_password(password)
    try:
        cur.execute('INSERT INTO admins (username, password_hash) VALUES (?, ?)', (username, password_hash))
        conn.commit()
        print(f"Админ '{username}' создан.")
    except sqlite3.IntegrityError:
        print(f"Пользователь '{username}' уже существует.")
    conn.close()

if __name__ == '__main__':
    username = input("Введите логин администратора: ")
    password = input("Введите пароль администратора: ")
    create_admin(username, password)
