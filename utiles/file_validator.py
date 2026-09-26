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
import zipfile
from pathlib import Path
import time
from PIL import Image

try:
    import fitz
except ImportError:
    fitz = None
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

    INPUT_TYPES_BY_TARGET = {
        "MP3": {"audio", "video"},
        "MP4": {"video"},
        "GIF": {"video"},
        "PDF → PNG": {"pdf"},
        "PDF → ZIP": {"pdf"},
        "PNG → JPG": {"png"},
        "PNG → JPEG": {"png"},
        "TXT": {"pdf", "zip", "text"},
    }
    TARGET_FORMATS_BY_INPUT_TYPE = {
        "audio": ["MP3"],
        "video": ["MP3", "MP4", "GIF"],
        "pdf": ["PDF → PNG", "PDF → ZIP", "TXT"],
        "png": ["PNG → JPG", "PNG → JPEG"],
        "zip": ["TXT"],
        "text": ["TXT"],
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
        detected = self.detect_file_type(file_path)
        allowed = self.INPUT_TYPES_BY_TARGET.get(expected_format)
        if allowed is None:
            base_format = self.FORMAT_MAPPING.get(expected_format, expected_format)
            allowed = {
                "MP3": {"audio", "video"}, "MP4": {"video"}, "GIF": {"video"},
                "PDF": {"pdf"}, "PNG": {"png"}, "JPG": {"jpeg"},
                "JPEG": {"jpeg"}, "TXT": {"text", "zip", "pdf"},
            }.get(base_format, set())
        if detected in allowed:
            return True, ""
        return False, f"Фактический тип файла: {detected or 'неизвестен'}; он не поддерживается для {expected_format}"

    def detect_file_type(self, file_path: str) -> str:
        """Detect supported types from file content; never trust its name or Telegram MIME header."""
        try:
            path = Path(file_path)
            if not path.is_file() or path.is_symlink():
                return "unknown"
            with path.open("rb") as stream:
                header = stream.read(4096)

            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                return "png"
            if header.startswith(b"\xff\xd8\xff"):
                return "jpeg"
            if header.startswith((b"GIF87a", b"GIF89a")):
                return "gif"
            if header.startswith(b"%PDF-"):
                return "pdf"
            if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                return "zip"
            if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
                return "audio"
            if header.startswith(b"OggS"):
                return "audio"
            if header.startswith(b"ID3") or any(
                header[index] == 0xFF and header[index + 1] & 0xE0 == 0xE0
                for index in range(min(len(header) - 1, 64))
            ):
                return "audio"
            if len(header) >= 12 and header[4:8] == b"ftyp":
                return "video"
            if b"ftyp" in header[:64]:
                return "video"
            if header and b"\x00" not in header and path.stat().st_size <= self.SIZE_LIMITS["TXT"]:
                try:
                    header.decode("utf-8-sig")
                    return "text"
                except (UnicodeDecodeError, OSError):
                    pass
            if MAGIC_AVAILABLE:
                mime_type = magic.from_file(str(path), mime=True)
                if mime_type.startswith("audio/"):
                    return "audio"
                if mime_type.startswith("video/"):
                    return "video"
            return "unknown"
        except (OSError, ValueError):
            return "unknown"

    def canonical_extension(self, file_path: str, detected_type: str) -> str:
        """Return the converter's expected suffix based on bytes, not supplied metadata."""
        if detected_type == "audio":
            try:
                with open(file_path, "rb") as source:
                    header = source.read(16)
                if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
                    return ".wav"
                if header.startswith(b"OggS"):
                    return ".ogg"
            except OSError:
                pass
            return ".mp3"
        if detected_type == "video":
            try:
                with open(file_path, "rb") as source:
                    header = source.read(16)
                if header[4:8] == b"ftyp" and header[8:12] == b"qt  ":
                    return ".mov"
            except OSError:
                pass
            return ".mp4"
        return {
            "gif": ".gif", "png": ".png", "jpeg": ".jpg",
            "pdf": ".pdf", "zip": ".zip", "text": ".txt",
        }.get(detected_type, ".bin")

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
        """Check decoded structure and expansion limits for supported inputs."""
        try:
            if not os.path.isfile(file_path) or os.path.islink(file_path):
                return False, "Файл не найден или является ссылкой"
            if os.path.getsize(file_path) <= 0:
                return False, "Файл пустой"

            detected = self.detect_file_type(file_path)
            if detected == "unknown":
                return False, "Не удалось распознать содержимое файла"
            type_valid, type_error = self.validate_file_type(file_path, expected_format)
            if not type_valid:
                return False, type_error

            if detected in {"png", "jpeg", "gif"}:
                with Image.open(file_path) as image:
                    if image.width * image.height > 40_000_000:
                        return False, "Изображение слишком большое для безопасной обработки"
                    image.verify()
            elif detected == "pdf" and fitz is not None:
                document = fitz.open(file_path)
                try:
                    if document.page_count > 100:
                        return False, "В PDF больше 100 страниц"
                    if document.page_count == 0:
                        return False, "В PDF нет страниц"
                    total_pixels = 0
                    for page in document:
                        rect = page.rect
                        pixels_at_conversion_scale = int(rect.width * rect.height * 4)
                        if pixels_at_conversion_scale > 25_000_000:
                            return False, "Страница PDF слишком большая для безопасной обработки"
                        total_pixels += pixels_at_conversion_scale
                        if total_pixels > 100_000_000:
                            return False, "PDF слишком большой для конвертации в изображения"
                finally:
                    document.close()
            elif detected == "zip":
                with zipfile.ZipFile(file_path) as archive:
                    entries = archive.infolist()
                    if len(entries) > 1000:
                        return False, "В архиве слишком много файлов"
                    if sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                        return False, "Распакованный архив превышает лимит 100 МБ"
                    if any(item.file_size > 0 and item.file_size / max(item.compress_size, 1) > 200 for item in entries):
                        return False, "В архиве обнаружен подозрительный коэффициент сжатия"
                    if archive.testzip() is not None:
                        return False, "Архив повреждён"
            elif detected == "text" and os.path.getsize(file_path) > self.SIZE_LIMITS["TXT"]:
                return False, "Текстовый файл превышает лимит 10 МБ"
            return True, ""
        except Exception as e:
            return False, f"Ошибка при проверке целостности файла: {str(e)[:160]}"

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
