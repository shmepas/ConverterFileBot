"""
Конфигурационный файл для безопасного хранения настроек.
НИКОГДА не коммитьте этот файл в Git!
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask настройки
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key-here-change-in-production')
    
    # SMTP настройки
    SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_LOGIN = os.getenv('SMTP_LOGIN', 'your-email@gmail.com')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', 'your-app-password')
    
    # База данных
    DATABASE = 'admins.db'
    UPLOAD_FOLDER = 'uploads'
    
    # Дополнительные настройки
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB максимальный размер файла

# Создаем экземпляр конфигурации
config = Config()