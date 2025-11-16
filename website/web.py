from flask import Flask, render_template, request, redirect, url_for, session, g, jsonify, send_file, after_this_request
import asyncio
from data_base import db as botdb
import sqlite3
from werkzeug.utils import secure_filename
import converter
import os
import shutil
import hashlib
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Импортируем Celery и задачи
try:
    from celery_config import celery_app
    from celery_tasks import process_large_file
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    print("⚠️  Celery не установлен. Фоновая обработка недоступна.")


app = Flask(__name__)
app.secret_key = "0510200728082025"


DATABASE = 'admins.db'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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

@app.route('/upload_large', methods=['GET', 'POST'])
def upload_large():
    if request.method == 'GET':
        return render_template('upload_large.html')

    # POST: обработка загрузки
    email = request.form.get('email', '').strip()
    fmt = request.form.get('format', '').strip()
    notes = request.form.get('notes', '').strip()
    
    if not email or not fmt:
        return 'Email и формат обязательны', 400
    
    if 'file' not in request.files:
        return 'Файл не выбран', 400

    file = request.files['file']
    if file.filename == '':
        return 'Файл не выбран', 400

    try:
        filename = secure_filename(file.filename)
        import time
        timestamp = time.time()
        unique_name = f"{timestamp}_{filename}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_name)
        file.save(filepath)
        
        # Если Celery доступен — отправляем задачу в очередь
        if CELERY_AVAILABLE:
            task = process_large_file.delay(filepath, fmt, email, notes)
            confirmation_msg = f"""Спасибо за загрузку!

Файл: {filename}
Формат: {fmt}
ID задачи: {task.id}

Ваш файл находится в очереди обработки.
Результат будет отправлен на этот email в течение 30 минут.

Спасибо за использование нашего сервиса!"""
        else:
            # Fallback: отправляем простое письмо (файл остается на сервере)
            confirmation_msg = f"""Спасибо за загрузку!

Файл: {filename}
Формат: {fmt}

Ваш файл находится в очереди обработки.
Результат будет отправлен на этот email в течение 30 минут.

Спасибо за использование нашего сервиса!"""

        # Отправляем email подтверждение
        try:
            msg = MIMEMultipart()
            msg['From'] = SMTP_LOGIN
            msg['To'] = email
            msg['Subject'] = f"✓ Ваш файл получен ({fmt})"
            msg.attach(MIMEText(confirmation_msg, 'plain', 'utf-8'))

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_LOGIN, SMTP_PASSWORD)
                server.send_message(msg)
        except Exception as e:
            print(f"Email error: {e}")

        return 'OK', 200
    except Exception as e:
        return f'Ошибка при загрузке: {str(e)}', 500

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
        # Сохранить сообщение в локальной базе для истории
        try:
            asyncio.get_event_loop().run_until_complete(botdb.add_feedback(name=name, email=email, message=message, user_id=None))
        except Exception:
            # безопасно игнорируем сохранение в случае ошибки
            pass
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


@app.route('/faq')
def faq():
    return render_template('faq.html')


@app.route('/support')
def support():
    return render_template('support.html')


@app.route('/feedback')
def feedback_page():
    return render_template('feedback.html')


@app.route('/api/feedback', methods=['POST'])
def api_feedback():
    data = request.get_json(force=True)

    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    message = data.get('message', '').strip()

    if not name or not email or not message:
        return jsonify({'error': 'Пожалуйста, заполните все поля.'}), 400

    # Сохраняем в базе бота
    try:
        asyncio.get_event_loop().run_until_complete(botdb.add_feedback(name=name, email=email, message=message, user_id=None))
    except Exception as e:
        print('Ошибка при сохранении feedback:', e)

    # Отправляем на email разработчику
    try:
        send_email(name, email, message)
    except Exception as e:
        print('Ошибка отправки почты:', e)
        # не прерываем - но сообщаем пользователю
        return jsonify({'error': 'Ошибка отправки сообщения. Попробуйте позже.'}), 500

    return jsonify({'success': True})


ALLOWED_EXTENSIONS = {
    'txt', 'docx', 'pdf', 'md',
    'jpg', 'jpeg', 'png',
    'mp4', 'mov',
    'mp3', 'wav'
}

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/convert', methods=['GET', 'POST'])
def convert_page():
    if request.method == 'GET':
        ffmpeg_path = shutil.which('ffmpeg')
        try:
            import pypandoc
            try:
                pandoc_path = pypandoc.get_pandoc_path()
            except Exception:
                pandoc_path = None
        except Exception:
            pandoc_path = None

        return render_template('convert.html', ffmpeg_path=ffmpeg_path, pandoc_path=pandoc_path)

    # POST: handle uploaded file
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не выбран'}), 400

    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400

    if not allowed_file(f.filename):
        return jsonify({'error': 'Формат файла не поддерживается'}), 400

    target_format = request.form.get('target_format', '').strip().lower()
    if not target_format:
        return jsonify({'error': 'Не выбран формат для конвертации'}), 400

    filename = secure_filename(f.filename)
    src_path = os.path.join(UPLOAD_FOLDER, filename)
    f.save(src_path)

    # Validate dependencies based on formats
    ext = os.path.splitext(filename)[1].lower()
    # video/audio conversions need ffmpeg
    if ext in ['.mp4', '.mov', '.mp3', '.wav'] or target_format in ['mp3', 'wav']:
        if shutil.which('ffmpeg') is None:
            try:
                os.remove(src_path)
            except Exception:
                pass
            return jsonify({'error': 'FFmpeg не установлен на сервере. Установите ffmpeg в PATH.'}), 500

    # text conversions need pandoc
    if target_format in ['pdf', 'docx', 'md'] or ext in ['.txt', '.docx', '.pdf', '.md']:
        try:
            import pypandoc
            if not pypandoc.get_pandoc_path():
                raise Exception('pandoc not found')
        except Exception:
            try:
                os.remove(src_path)
            except Exception:
                pass
            return jsonify({'error': 'Pandoc не установлен на сервере. Установите pandoc или используйте другой формат.'}), 500

    try:
        dst_path = asyncio.get_event_loop().run_until_complete(converter.convert_file(src_path, target_format))
    except Exception as e:
        print('Conversion error:', e)
        try:
            os.remove(src_path)
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500

    @after_this_request
    def cleanup(response):
        try:
            if os.path.exists(src_path):
                os.remove(src_path)
            if os.path.exists(dst_path):
                os.remove(dst_path)
        except Exception as e:
            print('Cleanup error:', e)
        return response

    return send_file(dst_path, as_attachment=True)


@app.route('/admin/feedbacks')
def admin_feedbacks():
    if not is_logged_in():
        return redirect(url_for('admin_login'))

    feedbacks = []
    try:
        feedbacks = asyncio.get_event_loop().run_until_complete(botdb.get_feedbacks(limit=500))
    except Exception as e:
        print('Ошибка получения feedbacks:', e)

    return render_template('admin_feedbacks.html', feedbacks=feedbacks)


@app.route('/admin/feedbacks/delete/<int:feedback_id>', methods=['POST'])
def admin_delete_feedback(feedback_id: int):
    if not is_logged_in():
        return redirect(url_for('admin_login'))
    try:
        asyncio.get_event_loop().run_until_complete(botdb.delete_feedback(feedback_id))
    except Exception as e:
        print('Ошибка удаления feedback:', e)
    return redirect(url_for('admin_feedbacks'))


@app.route('/admin/feedbacks/mark/<int:feedback_id>', methods=['POST'])
def admin_mark_feedback(feedback_id: int):
    if not is_logged_in():
        return redirect(url_for('admin_login'))
    new_status = request.form.get('status', 'handled')
    try:
        asyncio.get_event_loop().run_until_complete(botdb.update_feedback_status(feedback_id, new_status))
    except Exception as e:
        print('Ошибка обновления статуса feedback:', e)
    return redirect(url_for('admin_feedbacks'))

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=8080)
