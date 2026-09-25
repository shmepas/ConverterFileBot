"""
Модуль для валидации файлов и обеспечения безопасности

📦 ЗАВИСИМОСТИ:
   pip install python-magic

⚠️  Если python-magic не установлена, будет использована базовая валидация
    (проверка расширений файлов вместо MIME-типов).
"""
import os
import sys
import hashlib
import re
from pathlib import Path
import time
# Пытаемся импортировать python-magic, если недоступна - используем fallback
MAGIC_AVAILABLE = False
magic_error = None

try:
    import magic  # python-magic для определения типа файла
    # Проверяем, что библиотека действительно работает
    test_result = magic.from_file(__file__, mime=True)
    MAGIC_AVAILABLE = True
except ImportError as e:
    magic_error = f"ImportError: {str(e)}"
except Exception as e:
    magic_error = f"RuntimeError: {str(e)}"

class FileValidator:
    """Класс для валидации файлов"""

    # Маппинг составных форматов к базовым для валидации
    FORMAT_MAPPING = {
        "PDF → PNG": "PDF",
        "PDF → ZIP": "PDF",
        "PNG → JPG": "PNG",
        "PNG → JPEG": "PNG"
    }

    # Разрешенные MIME-типы для каждого формата
    ALLOWED_MIME_TYPES = {
        "MP3": ["audio/mpeg", "audio/wav", "audio/ogg", "audio/x-wav"],
        "MP4": ["video/mp4", "video/quicktime"],
        "GIF": ["image/gif"],
        "PNG": ["image/png"],
        "JPG": ["image/jpeg"],
        "JPEG": ["image/jpeg"],
        "PDF": ["application/pdf"],
        "TXT": ["text/plain", "text/markdown"]
    }

    # Ограничения размера для разных форматов (в байтах)
    SIZE_LIMITS = {
        "MP3": 50 * 1024 * 1024,      # 50 МБ
        "MP4": 500 * 1024 * 1024,     # 500 МБ
        "GIF": 100 * 1024 * 1024,     # 100 МБ
        "PNG": 20 * 1024 * 1024,      # 20 МБ
        "JPG": 20 * 1024 * 1024,      # 20 МБ
        "JPEG": 20 * 1024 * 1024,     # 20 МБ
        "PDF": 100 * 1024 * 1024,     # 100 МБ
        "TXT": 10 * 1024 * 1024,      # 10 МБ
        "DEFAULT": 20 * 1024 * 1024   # 20 МБ по умолчанию
    }

    # Запрещенные расширения (потенциально опасные)
    DANGEROUS_EXTENSIONS = [
        '.exe', '.bat', '.cmd', '.com', '.pif', '.scr', '.vbs', '.js', '.jar',
        '.php', '.asp', '.jsp', '.pl', '.py', '.rb', '.sh', '.ps1',
        '.dll', '.so', '.dylib', '.sys', '.drv'
    ]

    def __init__(self):
        self.temp_dir = os.path.join(os.path.dirname(__file__), "..", "downloads")
        os.makedirs(self.temp_dir, exist_ok=True)

    def validate_filename(self, filename: str) -> bool:
        """Проверяет безопасность имени файла"""
        if not filename:
            return False

        # Проверяем на path traversal
        if ".." in filename or filename.startswith("."):
            return False

        # Проверяем на запрещенные символы
        if re.search(r'[<>:"/\\|?*]', filename):
            return False

        # Проверяем длину имени файла
        if len(filename) > 255:
            return False

        # Проверяем запрещенные расширения
        file_ext = Path(filename).suffix.lower()
        if file_ext in self.DANGEROUS_EXTENSIONS:
            return False

        return True

    def get_safe_filename(self, original_filename: str, user_id: int = None) -> str:
        """Создает безопасное имя файла"""
        import time

        if not self.validate_filename(original_filename):
            # Создаем безопасное имя
            base_name = "uploaded_file"
            timestamp = int(time.time())
            return f"{base_name}_{timestamp}"

        # Убираем потенциально опасные символы
        safe_name = re.sub(r'[^\w\-_.]', '_', original_filename)

        # Добавляем timestamp для уникальности
        if user_id:
            timestamp = int(time.time())
            name, ext = os.path.splitext(safe_name)
            safe_name = f"{name}_{user_id}_{timestamp}{ext}"

        return safe_name

    def validate_file_type(self, file_path: str, expected_format: str) -> tuple[bool, str]:
        """
        Проверяет, что файл действительно того типа, который ожидается

        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            # Определяем базовый формат для валидации
            base_format = self.FORMAT_MAPPING.get(expected_format, expected_format)

            if not MAGIC_AVAILABLE:
                # Fallback: используем расширение файла для базовой проверки
                file_ext = Path(file_path).suffix.lower().lstrip('.')
                expected_ext = base_format.lower()

                # Простая проверка по расширению
                if base_format in ["MP3", "MP4", "GIF", "PNG", "JPG", "JPEG", "PDF", "TXT"]:
                    allowed_extensions = {
                        "MP3": ["mp3", "wav", "ogg"],
                        "MP4": ["mp4", "mov", "avi"],
                        "GIF": ["gif"],
                        "PNG": ["png"],
                        "JPG": ["jpg", "jpeg"],
                        "JPEG": ["jpg", "jpeg"],
                        "PDF": ["pdf"],
                        "TXT": ["txt", "md", "log"]
                    }

                    allowed_exts = allowed_extensions.get(base_format, [])
                    if file_ext not in allowed_exts:
                        return False, f"Неожиданное расширение файла: .{file_ext}. Ожидается одно из: {', '.join(allowed_exts)}"
                else:
                    return False, "python-magic не установлена, невозможно проверить тип файла"

                return True, ""

            # Получаем MIME-тип файла
            mime_type = magic.from_file(file_path, mime=True)

            # Проверяем, что MIME-тип разрешен для базового формата
            allowed_types = self.ALLOWED_MIME_TYPES.get(base_format, [])

            if mime_type in allowed_types:
                # MIME-тип соответствует ожидаемому формату
                return True, ""

            # Если MIME-тип не соответствует, проверяем расширение файла
            file_ext = Path(file_path).suffix.lower().lstrip('.')

            # Маппинг расширений к базовым форматам
            extension_to_format = {
                "mp3": "MP3", "wav": "MP3", "ogg": "MP3",
                "mp4": "MP4", "mov": "MP4", "avi": "MP4",
                "gif": "GIF",
                "png": "PNG",
                "jpg": "JPG", "jpeg": "JPEG",
                "pdf": "PDF",
                "txt": "TXT", "md": "TXT", "log": "TXT"
            }

            detected_format = extension_to_format.get(file_ext)

            if detected_format == base_format:
                # Расширение соответствует ожидаемому формату
                return True, ""

            # Для изображений дополнительно проверяем магические числа
            if base_format in ["PNG", "JPG", "JPEG"]:
                try:
                    with open(file_path, 'rb') as f:
                        header = f.read(8)

                    if base_format == "PNG" and header.startswith(b'\x89PNG\r\n\x1a\n'):
                        return True, ""
                    elif base_format in ["JPG", "JPEG"] and (header.startswith(b'\xff\xd8') or header.startswith(b'\xff\xe0') or header.startswith(b'\xff\xe1')):
                        return True, ""
                except:
                    pass

            # Если ни MIME-тип, ни расширение, ни магические числа не совпадают
            return False, f"Неожиданный тип файла: {mime_type}. Ожидается один из: {', '.join(allowed_types)}"

        except Exception as e:
            return False, f"Ошибка при определении типа файла: {str(e)}"

    def validate_file_size(self, file_path: str, target_format: str) -> tuple[bool, str]:
        """
        Проверяет размер файла

        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            file_size = os.path.getsize(file_path)
            size_limit = self.SIZE_LIMITS.get(target_format, self.SIZE_LIMITS["DEFAULT"])

            if file_size > size_limit:
                size_mb = file_size / (1024 * 1024)
                limit_mb = size_limit / (1024 * 1024)
                return False, f"Файл слишком большой: {size_mb:.1f} МБ. Лимит для {target_format}: {limit_mb:.1f} МБ"

            return True, ""

        except Exception as e:
            return False, f"Ошибка при проверке размера файла: {str(e)}"

    def validate_file_integrity(self, file_path: str, expected_format: str) -> tuple[bool, str]:
        """
        Проверяет целостность файла (базовая проверка)

        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            # Определяем базовый формат для валидации
            base_format = self.FORMAT_MAPPING.get(expected_format, expected_format)

            # Проверяем, что файл не пустой
            if os.path.getsize(file_path) == 0:
                return False, "Файл пустой"

            # Проверяем специфичные для форматов проблемы
            if base_format in ["MP3", "MP4"]:
                # Проверяем, что файл не поврежден (базовая проверка)
                with open(file_path, 'rb') as f:
                    header = f.read(16)
                    if not header:
                        return False, "Файл поврежден (не удалось прочитать заголовок)"

                    # Проверяем магические числа для медиафайлов
                    if base_format == "MP3":
                        ext = Path(file_path).suffix.lower()
                        valid_audio = (
                            header.startswith((b'ID3', b'\xff\xfb', b'\xff\xfa', b'\xff\xf3', b'\xff\xf2'))
                            or (ext == ".wav" and header.startswith(b'RIFF') and b'WAVE' in header)
                            or (ext == ".ogg" and header.startswith(b'OggS'))
                        )
                        if not valid_audio:
                            return False, "Не похоже на MP3 файл"

                    elif base_format == "MP4":
                        # MP4 файлы начинаются с ftyp
                        if len(header) < 8 or header[4:8] != b'ftyp':
                            return False, "Не похоже на MP4 файл"

            elif base_format == "PDF":
                # PDF файлы должны начинаться с %PDF
                with open(file_path, 'rb') as f:
                    header = f.read(4)
                    if not header.startswith(b'%PDF'):
                        return False, "Не похоже на PDF файл"

            elif base_format in ["PNG", "JPG", "JPEG"]:
                # Проверяем заголовки изображений (менее строгая проверка)
                try:
                    with open(file_path, 'rb') as f:
                        header = f.read(8)

                    if base_format == "PNG":
                        # Для PNG проверяем начало заголовка (первые 4 байта)
                        if not header.startswith(b'\x89PNG\r\n\x1a\n'):
                            return False, "Не похоже на PNG файл"

                    elif base_format in ["JPG", "JPEG"]:
                        # Для JPEG проверяем первые 2 байта
                        if len(header) >= 2 and header[:2] == b'\xff\xd8':
                            pass  # JPEG файл прошел проверку
                        else:
                            return False, "Не похоже на JPEG файл"
                except:
                    return False, "Не удалось проверить заголовок изображения"

            elif base_format == "GIF":
                with open(file_path, 'rb') as f:
                    header = f.read(6)
                if header not in (b'GIF87a', b'GIF89a'):
                    return False, "Не похоже на GIF файл"

            return True, ""

        except Exception as e:
            return False, f"Ошибка при проверке целостности файла: {str(e)}"

    def calculate_file_hash(self, file_path: str) -> str:
        """Вычисляет хеш файла для дедупликации"""
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return ""

    def is_duplicate_file(self, file_path: str, existing_hashes: set) -> bool:
        """Проверяет, является ли файл дубликатом"""
        try:
            file_hash = self.calculate_file_hash(file_path)
            return file_hash in existing_hashes
        except Exception:
            return False

    def full_validation(self, file_path: str, target_format: str, user_id: int = None) -> tuple[bool, str, dict]:
        """
        Полная валидация файла

        Returns:
            tuple: (is_valid, error_message, validation_info)
        """
        validation_info = {
            "file_size": 0,
            "mime_type": "",
            "file_hash": "",
            "validation_time": 0
        }

        import time
        start_time = time.time()

        try:
            # Проверяем размер файла
            is_size_valid, size_error = self.validate_file_size(file_path, target_format)
            if not is_size_valid:
                return False, size_error, validation_info

            validation_info["file_size"] = os.path.getsize(file_path)

            # Проверяем тип файла
            is_type_valid, type_error = self.validate_file_type(file_path, target_format)
            if not is_type_valid:
                return False, type_error, validation_info

            # Проверяем целостность файла
            is_integrity_valid, integrity_error = self.validate_file_integrity(file_path, target_format)
            if not is_integrity_valid:
                return False, integrity_error, validation_info

            # Получаем информацию о файле
            try:
                if MAGIC_AVAILABLE:
                    validation_info["mime_type"] = magic.from_file(file_path, mime=True)
                else:
                    validation_info["mime_type"] = f"unknown (python-magic not available)"
                validation_info["file_hash"] = self.calculate_file_hash(file_path)
            except:
                pass

            validation_info["validation_time"] = time.time() - start_time

            return True, "", validation_info

        except Exception as e:
            return False, f"Общая ошибка валидации: {str(e)}", validation_info

def check_dependencies():
    """Проверяет доступность необходимых зависимостей и предлагает установку"""
    print("🔍 Диагностика зависимостей:")
    print(f"   Python: {sys.version}")
    print(f"   python-magic доступна: {MAGIC_AVAILABLE}")

    if magic_error:
        print(f"   Ошибка python-magic: {magic_error}")

    # Проверяем, установлена ли библиотека
    try:
        import subprocess
        result = subprocess.run(['pip', 'show', 'python-magic'],
                              capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   pip show python-magic: ✅ Найдена в pip")
            # Показываем версию и расположение
            for line in result.stdout.split('\n'):
                if line.startswith('Version:') or line.startswith('Location:'):
                    print(f"   {line}")
        else:
            print(f"   pip show python-magic: ❌ Не найдена в pip")
    except Exception as e:
        print(f"   pip show error: {str(e)}")

    # Проверяем системные библиотеки (для Linux)

    # Специальные инструкции для разных ОС
    import platform
    if platform.system() == 'Linux':
        try:
            import subprocess
            result = subprocess.run(['ldconfig', '-p'], capture_output=True, text=True)
            if 'libmagic' in result.stdout:
                print(f"   libmagic: ✅ Найдена в системе")
            else:
                print(f"   libmagic: ❌ Не найдена в системе")
                print("   💡 Установите: sudo apt-get install libmagic1")
        except:
            pass

    missing_deps = []
    if not MAGIC_AVAILABLE:
        missing_deps.append("python-magic")

    if missing_deps:
        print("\n⚠️ Отсутствуют зависимости для полной функциональности:")
        for dep in missing_deps:
            print(f"   📦 {dep}")
        print("\n💡 Для установки выполните:")
        print("   pip install python-magic")
        if platform.system() == 'Linux':
            print("   sudo apt-get install libmagic1")
        print("\n⚠️ Будет использована базовая валидация (менее точная)")
        return False
    if platform.system() == 'Windows':
        print("💡 Для Windows попробуйте:")
        print("   pip install python-magic-bin")
        print("   или")
        print("   pip uninstall python-magic && pip install python-magic")
    elif platform.system() == 'Linux':
        print("💡 Для Linux попробуйте:")
        print("   sudo apt-get install libmagic1")
        print("   или")
        print("   pip install --upgrade python-magic")
    else:
        print("\n✅ Все зависимости установлены")
        print("💡 Попробуйте:")
        print("   pip install --upgrade python-magic")
        print("   или установите системные зависимости libmagic")

# Глобальный экземпляр валидатора
file_validator = FileValidator()
