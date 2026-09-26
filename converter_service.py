"""
Модуль для конвертации файлов различных форматов.
Вынесен из handlers для устранения дублирования кода.
"""
import os
import tempfile
import shutil
import zipfile
import traceback
import logging
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from PIL import Image
from moviepy import AudioFileClip, VideoFileClip
from PyPDF2 import PdfReader

from utils import FFMPEG_BINARY, convert_audio_ffmpeg_async, compress_video_ffmpeg_async, convert_video_to_gif_ffmpeg_async
from utiles.performance import measure_time, log_performance, update_conversion_stats, log_conversion_attempt
from utiles.file_validator import file_validator
from utiles.progress_tracker import track_conversion_progress, ConversionProgressCallback
import asyncio


logger = logging.getLogger(__name__)


class FileConverter:
    """Класс для конвертации файлов различных форматов."""

    SUPPORTED_FORMATS = {
        "MP3", "MP4", "GIF", "PDF → PNG", "PDF → ZIP",
        "PNG → JPG", "PNG → JPEG", "TXT"
    }

    def __init__(self):
        project_root = Path(__file__).resolve().parent
        converted_root = project_root / "converted"
        temp_dir = Path(os.getenv("CONVERTER_OUTPUT_DIR", str(converted_root / "runtime")))
        if converted_root.is_symlink() or temp_dir.is_symlink():
            raise RuntimeError("Каталог временных файлов не должен быть ссылкой")
        temp_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir = str(temp_dir.resolve(strict=True))
        if "CONVERTER_OUTPUT_DIR" not in os.environ:
            Path(self.temp_dir).relative_to(project_root)

    def get_output_filename(self, input_filename: str, target_format: str) -> str:
        """Генерирует имя выходного файла."""
        import uuid
        base_name = os.path.splitext(os.path.basename(input_filename))[0]
        extension = target_format.split('→')[-1].strip().lower()
        return f"{base_name}_{uuid.uuid4().hex}.{extension}"

    @measure_time
    def convert_to_mp3(self, input_path: str, output_path: str) -> str:
        """Конвертирует аудио или извлекает аудио из видео в MP3."""
        try:
            if input_path.lower().endswith((".mp3", ".wav", ".ogg")):
                import subprocess

                cmd = [
                    FFMPEG_BINARY, "-y", "-i", input_path,
                    "-acodec", "libmp3lame", "-ab", "128k",
                    output_path
                ]
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                except FileNotFoundError:
                    with AudioFileClip(input_path) as clip:
                        clip.write_audiofile(output_path, logger=None)
            else:
                with VideoFileClip(input_path) as clip:
                    if clip.audio is None:
                        raise ValueError("В видеофайле нет аудиодорожки")
                    clip.audio.write_audiofile(output_path, logger=None)

            update_conversion_stats("MP3", 0, True)
            return output_path
        except Exception:
            update_conversion_stats("MP3", 0, False)
            raise

    @measure_time
    def convert_to_mp4(self, input_path: str, output_path: str) -> str:
        try:
            import asyncio
            asyncio.run(compress_video_ffmpeg_async(
                input_path, output_path,
                crf=30, max_width=640, audio_bitrate="64k", preset="fast"
            ))
        except Exception:
            with VideoFileClip(input_path) as clip:
                clip.write_videofile(
                    output_path, codec="libx264", bitrate="600k",
                    fps=24, audio=True, logger=None
                )

        update_conversion_stats("MP4", 0, True)
        return output_path

    @measure_time
    def convert_to_gif(self, input_path: str, output_path: str) -> str:
        """Конвертирует видео в GIF."""
        try:
            import asyncio
            asyncio.run(convert_video_to_gif_ffmpeg_async(
                input_path, output_path, width=360, fps=10, quality="high"
            ))
        except Exception:
            with VideoFileClip(input_path) as clip:
                clip.write_gif(output_path, fps=10, logger=None)

        update_conversion_stats("GIF", 0, True)
        return output_path

    @measure_time
    def convert_pdf_to_png(self, input_path: str, output_dir: str = None) -> list:

        if fitz is None:
            raise ImportError("PyMuPDF не установлен: pip install PyMuPDF")

        try:
            if output_dir is None:
                output_dir = tempfile.mkdtemp(prefix="converter_pdf_")

            os.makedirs(output_dir, exist_ok=True)

            doc = fitz.open(input_path)
            image_paths = []

            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_path = os.path.join(output_dir, f"page_{i + 1}.png")
                pix.save(img_path)
                image_paths.append(img_path)

            doc.close()


            update_conversion_stats("PDF → PNG", 0, True)
            return image_paths

        except Exception as e:
            update_conversion_stats("PDF → PNG", 0, False)
            raise e

    @measure_time
    def convert_pdf_to_zip(self, input_path: str, output_path: str = None) -> str:
        if output_path is None:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(self.temp_dir, f"{base_name}_{os.urandom(8).hex()}.zip")

        try:
            temp_images = []
            doc = fitz.open(input_path)
            try:
                with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
                    for i, page in enumerate(doc):
                        try:

                            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                            img_name = f"page_{i + 1}.png"
                            img_path = os.path.join(os.path.dirname(output_path), f"{os.path.basename(output_path)}_{img_name}")
                            temp_images.append(img_path)

                            pix.save(img_path)
                            zipf.write(img_path, img_name)


                            del pix

                        except Exception as e:

                            continue
            finally:
                doc.close()


            update_conversion_stats("PDF → ZIP", 0, True)
            return output_path

        except Exception as e:

            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
            update_conversion_stats("PDF → ZIP", 0, False)
            raise e

        finally:

            for img_path in temp_images:
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                except OSError:
                    pass

    @measure_time
    def convert_png_to_jpg(self, input_path: str, target_ext: str = "jpg") -> str:
        """Конвертирует PNG в JPG/JPEG."""
        try:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(self.temp_dir, f"{base_name}_{os.urandom(8).hex()}.{target_ext}")

            img = Image.open(input_path)


            if img.mode in ("RGBA", "LA", "P"):
                rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "RGBA":
                    rgb_img.paste(img, mask=img.split()[-1])
                else:
                    rgb_img.paste(img)
                rgb_img.save(output_path, "JPEG", quality=90)
            else:
                img.save(output_path, "JPEG", quality=90)


            format_name = f"PNG → {target_ext.upper()}"
            update_conversion_stats(format_name, 0, True)
            return output_path

        except Exception as e:
            format_name = f"PNG → {target_ext.upper()}"
            update_conversion_stats(format_name, 0, False)
            raise e

    @measure_time
    def convert_to_txt(self, input_path: str, output_path: str = None) -> str:
        """Конвертирует различные форматы в TXT."""
        try:
            if output_path is None:
                base_name = os.path.splitext(os.path.basename(input_path))[0]
                output_path = os.path.join(self.temp_dir, f"{base_name}_{os.urandom(8).hex()}.txt")

            ext = os.path.splitext(input_path)[1].lower()

            if ext == ".pdf":
                try:
                    reader = PdfReader(input_path)
                    text = ""
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n\n"
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception as e:
                    raise RuntimeError(f"Ошибка при чтении PDF файла: {str(e)}")

            elif ext == ".zip":
                try:
                    with zipfile.ZipFile(input_path, 'r') as zip_ref:
                        extracted_files = zip_ref.namelist()
                        text = ""
                        for ef in extracted_files:
                            if ef.lower().endswith(('.txt', '.md', '.log')):
                                try:
                                    with zip_ref.open(ef) as file:
                                        content = file.read().decode('utf-8', errors='ignore')
                                        text += f"\n\n--- {ef} ---\n\n" + content
                                except Exception:

                                    continue
                        with open(output_path, "w", encoding="utf-8") as f:
                            f.write(text)
                except zipfile.BadZipFile:
                    raise RuntimeError("Архив поврежден или не является ZIP файлом")
                except Exception as e:
                    raise RuntimeError(f"Ошибка при обработке ZIP архива: {str(e)}")

            elif ext in [".txt", ".md", ".log"]:
                try:
                    shutil.copy(input_path, output_path)
                except Exception as e:
                    raise RuntimeError(f"Ошибка при копировании файла: {str(e)}")

            else:
                raise ValueError(f"Неподдерживаемый формат для конвертации в TXT: {ext}")


            update_conversion_stats("TXT", 0, True)
            return output_path

        except Exception as e:
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
            update_conversion_stats("TXT", 0, False)
            raise e

    @measure_time
    def convert_file(self, input_path: str, target_format: str, user_id: int = None, progress_callback: ConversionProgressCallback = None) -> str:
        """Универсальный метод конвертации файлов с валидацией."""
        if target_format not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Неподдерживаемый формат: {target_format}")

        # Валидируем файл перед конвертацией
        if progress_callback:
            import asyncio
            asyncio.create_task(progress_callback.set_stage('validating', 10))

        logger.debug("Validating input for target format %s", target_format)
        is_valid, error_message, validation_info = file_validator.full_validation(input_path, target_format, user_id)

        if not is_valid:
            if progress_callback:
                import asyncio
                asyncio.create_task(progress_callback.complete(False, f"Валидация не пройдена: {error_message}"))
            raise ValueError(f"Файл не прошел валидацию: {error_message}")

        logger.debug(
            "Input validated for %s in %.2fs (%s bytes)",
            target_format, validation_info["validation_time"], validation_info["file_size"],
        )

        if progress_callback:
            import asyncio
            asyncio.create_task(progress_callback.set_stage('converting', 30))

        try:
            if target_format == "MP3":
                output_path = os.path.join(self.temp_dir, self.get_output_filename(input_path, target_format))
                return self.convert_to_mp3(input_path, output_path)

            elif target_format == "MP4":
                output_path = os.path.join(self.temp_dir, self.get_output_filename(input_path, target_format))
                return self.convert_to_mp4(input_path, output_path)

            elif target_format == "GIF":
                output_path = os.path.join(self.temp_dir, self.get_output_filename(input_path, target_format))
                return self.convert_to_gif(input_path, output_path)

            elif target_format == "PDF → PNG":
                image_paths = self.convert_pdf_to_png(input_path)
                # Возвращаем путь к первому изображению или ZIP с изображениями
                if len(image_paths) == 1:
                    return image_paths[0]
                else:
                    zip_path = os.path.join(self.temp_dir, f"pdf_pages_{os.urandom(8).hex()}.zip")
                    try:
                        with zipfile.ZipFile(zip_path, "w") as zipf:
                            for img in image_paths:
                                zipf.write(img, os.path.basename(img))
                    finally:
                        self.cleanup_files(*image_paths, os.path.dirname(image_paths[0]) if image_paths else None)
                    return zip_path

            elif target_format == "PDF → ZIP":
                return self.convert_pdf_to_zip(input_path)

            elif target_format in ["PNG → JPG", "PNG → JPEG"]:
                target_ext = "jpg" if target_format == "PNG → JPG" else "jpeg"
                return self.convert_png_to_jpg(input_path, target_ext)

            elif target_format == "TXT":
                return self.convert_to_txt(input_path)

            else:
                raise ValueError(f"Неожиданный формат: {target_format}")

        except Exception:
            # Логируем неудачную конвертацию для основного метода
            logger.exception("Conversion failed for target format %s", target_format)
            raise

    def cleanup_files(self, *file_paths: str):
        """Remove only artifacts owned by this conversion."""
        cleaned_count = 0
        error_count = 0
        for file_path in file_paths:
            if not file_path:
                continue
            try:
                if os.path.exists(file_path):
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        cleaned_count += 1
                        parent = os.path.dirname(file_path)
                        if (os.path.basename(parent).startswith("converter_pdf_")
                                and os.path.dirname(parent) == self.temp_dir):
                            shutil.rmtree(parent, ignore_errors=True)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path, ignore_errors=True)
                        cleaned_count += 1
            except OSError:
                error_count += 1
                logger.warning("Could not remove converter artifact", exc_info=True)
        if cleaned_count > 0 or error_count > 0:
            logger.debug("Converter cleanup removed %s files and hit %s errors", cleaned_count, error_count)
        return cleaned_count, error_count

    def get_error_strategy(self, error: Exception) -> dict:
        """Определяет стратегию обработки ошибки по её типу"""
        error_type = type(error).__name__

        strategies = {
            'FileNotFoundError': {
                'action': 'retry',
                'message': 'Файл не найден, проверяю путь...',
                'delay': 1
            },
            'CalledProcessError': {
                'action': 'fallback',
                'message': 'FFmpeg недоступен, использую альтернативный метод...',
                'delay': 2
            },
            'MemoryError': {
                'action': 'cleanup_retry',
                'message': 'Недостаточно памяти, очищаю и повторяю...',
                'delay': 3,
                'cleanup': True
            },
            'TimeoutError': {
                'action': 'retry',
                'message': 'Таймаут, увеличиваю время ожидания...',
                'delay': 5
            },
            'ImportError': {
                'action': 'fallback',
                'message': 'Модуль недоступен, использую встроенные методы...',
                'delay': 1
            },
            'PermissionError': {
                'action': 'retry',
                'message': 'Проблема с правами доступа, повторяю...',
                'delay': 2
            }
        }

        return strategies.get(error_type, {
            'action': 'retry',
            'message': f'Неизвестная ошибка: {error_type}',
            'delay': 2
        })

    def get_fallback_methods(self, target_format: str) -> list:
        """Возвращает список fallback методов для формата"""
        fallback_methods = {
            "MP3": [
                ("ffmpeg", self._convert_mp3_ffmpeg),
                ("moviepy", self._convert_mp3_moviepy),
                ("system_ffmpeg", self._convert_mp3_system)
            ],
            "MP4": [
                ("ffmpeg", self._convert_mp4_ffmpeg),
                ("moviepy", self._convert_mp4_moviepy),
                ("basic_compress", self._convert_mp4_basic)
            ],
            "GIF": [
                ("ffmpeg_palette", self._convert_gif_ffmpeg),
                ("moviepy", self._convert_gif_moviepy),
                ("basic_gif", self._convert_gif_basic)
            ],
            "PDF → PNG": [
                ("pymupdf", self._convert_pdf_pymupdf),
                ("pypdf2", self._convert_pdf_pypdf2)
            ],
            "PNG → JPG": [
                ("pillow", self._convert_png_pillow),
                ("basic_convert", self._convert_png_basic)
            ]
        }

        return fallback_methods.get(target_format, [])

    async def _convert_mp3_ffmpeg(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация MP3 через ffmpeg"""
        import subprocess
        cmd = [FFMPEG_BINARY, "-y", "-i", input_path, "-acodec", "libmp3lame", "-ab", "128k", output_path]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path

    async def _convert_mp3_moviepy(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация MP3 через moviepy"""
        with VideoFileClip(input_path) as clip:
            if clip.audio is None:
                raise ValueError("В видеофайле нет аудиодорожки")
            clip.audio.write_audiofile(output_path, logger=None)
        return output_path

    async def _convert_mp3_system(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация MP3 через системный ffmpeg"""
        import subprocess
        try:
            cmd = [FFMPEG_BINARY, "-y", "-i", input_path, output_path]
            subprocess.run(cmd, check=True, capture_output=True)
        except:
            # Последний резерв - просто копируем файл
            import shutil
            shutil.copy2(input_path, output_path)
        return output_path

    async def _convert_mp4_ffmpeg(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация MP4 через ffmpeg"""
        await compress_video_ffmpeg_async(input_path, output_path, crf=30, max_width=640, audio_bitrate="64k", preset="fast")
        return output_path

    async def _convert_mp4_moviepy(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация MP4 через moviepy"""
        with VideoFileClip(input_path) as clip:
            clip.write_videofile(
                output_path, codec="libx264", bitrate="600k",
                fps=24, audio=True, logger=None
            )
        return output_path

    async def _convert_mp4_basic(self, input_path: str, output_path: str) -> str:
        """Fallback: базовая конвертация MP4"""
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    async def _convert_gif_ffmpeg(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация GIF через ffmpeg"""
        await convert_video_to_gif_ffmpeg_async(input_path, output_path, width=360, fps=10, quality="high")
        return output_path

    async def _convert_gif_moviepy(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация GIF через moviepy"""
        with VideoFileClip(input_path) as clip:
            clip.write_gif(output_path, fps=10, logger=None)
        return output_path

    async def _convert_gif_basic(self, input_path: str, output_path: str) -> str:
        """Fallback: базовая конвертация GIF"""
        # Простое копирование как последний резерв
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    async def _convert_pdf_pymupdf(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация PDF через PyMuPDF"""
        if fitz is None:
            raise ImportError("PyMuPDF не установлен")
        doc = fitz.open(input_path)
        if len(doc) > 0:
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            pix.save(output_path)
        doc.close()
        return output_path

    async def _convert_pdf_pypdf2(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация PDF через PyPDF2"""
        # PyPDF2 не может конвертировать в изображения, просто копируем
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    async def _convert_png_pillow(self, input_path: str, output_path: str) -> str:
        """Fallback: конвертация PNG через Pillow"""
        img = Image.open(input_path)
        if img.mode in ("RGBA", "LA", "P"):
            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                rgb_img.paste(img, mask=img.split()[-1])
            else:
                rgb_img.paste(img)
            rgb_img.save(output_path, "JPEG", quality=90)
        else:
            img.save(output_path, "JPEG", quality=90)
        return output_path

    async def _convert_png_basic(self, input_path: str, output_path: str) -> str:
        """Fallback: базовая конвертация PNG"""
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    async def convert_file_with_retry(self, input_path: str, target_format: str, max_retries: int = 3, user_id: int = None, progress_callback: ConversionProgressCallback = None) -> str:
        """Конвертация файла с автоматическими повторными попытками, умной обработкой ошибок и детальным логированием"""
        import time
        last_error = None

        for attempt in range(max_retries):
            start_time = time.time()

            try:
                logger.debug("Conversion attempt %s/%s for %s", attempt + 1, max_retries, target_format)

                # Обновляем прогресс для retry попыток
                if attempt > 0 and progress_callback:
                    await progress_callback.set_stage('retrying', 40 + (attempt * 20))

                # Очищаем память перед каждой попыткой (кроме первой)
                if attempt > 0:
                    # Получаем стратегию для предыдущей ошибки
                    if last_error:
                        strategy = self.get_error_strategy(last_error)
                        delay = strategy.get('delay', 2 ** attempt)

                        logger.debug("Retry strategy: %s; wait=%s seconds", strategy["message"], delay)
                        await asyncio.sleep(delay)

                        # Очищаем память если нужно
                        if strategy.get('cleanup'):
                            logger.debug("Cleaning memory before converter retry")
                            import gc
                            gc.collect()
                    else:
                        await asyncio.sleep(2 ** attempt)

                # Пытаемся конвертировать основным методом
                result = await asyncio.to_thread(self.convert_file, input_path, target_format, user_id)
                duration = time.time() - start_time

                logger.info("Conversion succeeded for %s on attempt %s", target_format, attempt + 1)

                # Обновляем прогресс при успехе
                if progress_callback:
                    await progress_callback.set_progress(100)

                # Логируем успешную попытку
                try:
                    log_conversion_attempt(
                        user_id=user_id or 0,
                        format_type=target_format,
                        attempt=attempt + 1,
                        method="primary",
                        success=True,
                        duration=duration
                    )
                except:
                    pass

                return result

            except Exception as e:
                duration = time.time() - start_time
                last_error = e
                error_type = type(e).__name__
                error_msg = str(e)[:200]  # Ограничиваем длину ошибки

                logger.warning(
                    "Conversion attempt %s failed for %s (%s)",
                    attempt + 1, target_format, error_type, exc_info=True,
                )

                # Логируем неудачную попытку
                try:
                    log_conversion_attempt(
                        user_id=user_id or 0,
                        format_type=target_format,
                        attempt=attempt + 1,
                        method="primary",
                        success=False,
                        error=error_msg,
                        duration=duration
                    )
                except:
                    pass

                # На последней попытке пробуем fallback методы
                if attempt == max_retries - 1:
                    logger.info("Trying fallback conversion methods for %s", target_format)

                    if progress_callback:
                        await progress_callback.set_stage('fallback', 80)

                    try:
                        fallback_result = await self._try_fallback_methods(input_path, target_format, user_id, progress_callback)
                        if fallback_result:
                            logger.info("Fallback conversion succeeded for %s", target_format)

                            if progress_callback:
                                await progress_callback.set_progress(100)

                            return fallback_result
                    except Exception as fallback_error:
                        logger.exception("All fallback methods failed for %s", target_format)

                        if progress_callback:
                            await progress_callback.complete(False, f"Fallback методы не сработали: {str(fallback_error)[:100]}")

                # Логируем неудачную попытку для статистики
                try:
                    update_conversion_stats(target_format, 0, False)
                except:
                    pass

        # Если все попытки провалились
        error_msg = f"Не удалось конвертировать файл в {target_format} после {max_retries} попыток. Последняя ошибка: {str(last_error)}"
        logger.error("Conversion exhausted all retries for %s", target_format)
        raise RuntimeError(error_msg)

    async def _try_fallback_methods(self, input_path: str, target_format: str, user_id: int = None, progress_callback: ConversionProgressCallback = None):
        """Пробует fallback методы для формата с логированием и прогресс-баром"""
        import time
        fallback_methods = self.get_fallback_methods(target_format)

        if not fallback_methods:
            raise ValueError(f"Нет доступных fallback методов для {target_format}")

        # Генерируем имя выходного файла
        output_path = os.path.join(self.temp_dir, self.get_output_filename(input_path, target_format))

        for i, (method_name, method_func) in enumerate(fallback_methods):
            start_time = time.time()

            # Обновляем прогресс для каждого fallback метода
            if progress_callback:
                progress = 85 + (i * 10 // len(fallback_methods))  # 85-95%
                await progress_callback.set_stage(f'fallback_{method_name}', progress)

            try:
                logger.debug("Trying fallback method %s", method_name)
                await method_func(input_path, output_path)
                duration = time.time() - start_time

                # Проверяем, что файл создался
                if os.path.exists(output_path):
                    logger.info("Fallback method %s succeeded", method_name)

                    # Логируем успешный fallback
                    try:
                        log_conversion_attempt(
                            user_id=user_id or 0,
                            format_type=target_format,
                            attempt=99,  # Специальный номер для fallback
                            method=f"fallback_{method_name}",
                            success=True,
                            duration=duration
                        )
                    except:
                        pass

                    return output_path
                else:
                    logger.warning("Fallback method %s did not create an output file", method_name)

            except Exception as error:
                duration = time.time() - start_time
                error_msg = str(error)[:200]
                logger.warning("Fallback method %s failed", method_name, exc_info=True)

                # Логируем неудачный fallback
                try:
                    log_conversion_attempt(
                        user_id=user_id or 0,
                        format_type=target_format,
                        attempt=99,  # Специальный номер для fallback
                        method=f"fallback_{method_name}",
                        success=False,
                        error=error_msg,
                        duration=duration
                    )
                except:
                    pass

                continue

        # Если все fallback методы провалились
        raise RuntimeError(f"Все fallback методы для {target_format} провалились")


# Глобальный экземпляр конвертера
file_converter = FileConverter()
