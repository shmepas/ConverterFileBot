from flask import Flask, render_template, request, redirect, url_for, session, g, jsonify
import sqlite3
import hashlib
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


app = Flask(__name__)
app.secret_key = "0510200728082025"


DATABASE = 'admins.db'

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_LOGIN = "nikilavasilew@gmail.com"
SMTP_PASSWORD = "123219102025clAPE051007"

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def check_admin_login(username, password):
    db = get_db()
    cur = db.cursor()
    cur.execute('SELECT password_hash FROM admins WHERE username = ?', (username,))
    row = cur.fetchone()
    if row and row[0] == hash_password(password):
        return True
    return False

def is_logged_in():
    return session.get('admin_logged_in')

@app.context_processor
def inject_admin():
    return dict(admin_logged_in=is_logged_in())

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if is_logged_in():
        return redirect(url_for('admin_panel'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if check_admin_login(username, password):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            return redirect(url_for('admin_panel'))
        else:
            error = 'Неверный логин или пароль'

    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/admin')
def admin_panel():
    if not is_logged_in():
        return redirect(url_for('admin_login'))
    return render_template('admin_panel.html', username=session.get('admin_username'))

@app.route('/api/contact', methods=['POST'])
def api_contact():
    data = request.get_json(force=True)

    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    message = data.get('message', '').strip()

    if not name or not email or not message:
        return jsonify({'error': 'Пожалуйста, заполните все поля.'}), 400

    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return jsonify({'error': 'Некорректный email.'}), 400

    try:
        send_email(name, email, message)
    except Exception as e:
        print("Ошибка отправки почты:", e)
        return jsonify({'error': 'Ошибка отправки сообщения. Попробуйте позже.'}), 500

    return jsonify({'success': True})

def send_email(name, sender_email, message_text):
    msg = MIMEMultipart()
    msg['From'] = SMTP_LOGIN
    msg['To'] = SMTP_LOGIN
    msg['Subject'] = f"Новое сообщение с сайта от {name}"

    body = f"Имя: {name}\nEmail: {sender_email}\n\nСообщение:\n{message_text}"
    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    server.starttls()
    server.login(SMTP_LOGIN, SMTP_PASSWORD)
    server.send_message(msg)
    server.quit()

@app.route('/admin/list')
def admin_list():
    if not is_logged_in():
        return redirect(url_for('admin_login'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, username FROM admins")
    admins = cur.fetchall()
    return render_template('admin_list.html', admins=admins, current_username=session.get('admin_username'))

@app.route('/admin/add', methods=['GET', 'POST'])
def admin_add():
    if not is_logged_in():
        return redirect(url_for('admin_login'))

    error = None
    success = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        password_confirm = request.form.get('password_confirm', '').strip()

        if not username or not password or not password_confirm:
            error = "Пожалуйста, заполните все поля."
        elif password != password_confirm:
            error = "Пароли не совпадают."
        else:
            db = get_db()
            cur = db.cursor()
            try:
                cur.execute("INSERT INTO admins (username, password_hash) VALUES (?, ?)",
                            (username, hash_password(password)))
                db.commit()
                success = f"Администратор {username} успешно добавлен."
            except sqlite3.IntegrityError:
                error = "Пользователь с таким именем уже существует."

    return render_template('admin_add.html', error=error, success=success)

@app.route('/admin/delete/<int:admin_id>', methods=['POST'])
def admin_delete(admin_id):
    if not is_logged_in():
        return redirect(url_for('admin_login'))

    current_username = session.get('admin_username')
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT username FROM admins WHERE id = ?", (admin_id,))
    row = cur.fetchone()
    if not row:
        return "Администратор не найден.", 404

    username_to_delete = row[0]

    if username_to_delete == current_username:
        return "Нельзя удалить самого себя!", 400

    cur.execute("DELETE FROM admins WHERE id = ?", (admin_id,))
    db.commit()
    return redirect(url_for('admin_list'))

@app.route('/api/check_bot')
def api_check_bot():
    # Возвращаем пустой или нейтральный ответ, чтобы не было ошибки
    return jsonify({'status': 'online'})

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=8080)
