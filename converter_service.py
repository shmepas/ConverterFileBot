"""
Модуль для конвертации файлов различных форматов.
Вынесен из handlers для устранения дублирования кода.
"""
import os
import tempfile
import shutil
import zipfile
import traceback
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from PIL import Image
from moviepy.editor import VideoFileClip
from PyPDF2 import PdfReader

from utils import convert_audio_ffmpeg_async, compress_video_ffmpeg_async, convert_video_to_gif_ffmpeg_async


class FileConverter:
    """Класс для конвертации файлов различных форматов."""
    
    SUPPORTED_FORMATS = {
        "MP3", "MP4", "GIF", "PDF → PNG", "PDF → ZIP", 
        "PNG → JPG", "PNG → JPEG", "TXT"
    }
    
    def __init__(self):
        self.temp_dir = tempfile.gettempdir()
    
    def get_output_filename(self, input_filename: str, target_format: str) -> str:
        """Генерирует имя выходного файла."""
        base_name = os.path.splitext(os.path.basename(input_filename))[0]
        extension = target_format.split('→')[-1].strip().lower()
        return f"{base_name}.{extension}"
    
    def convert_to_mp3(self, input_path: str, output_path: str) -> str:
        """Конвертирует файл в MP3."""
        if input_path.lower().endswith((".mp3", ".wav", ".ogg")):
            # Аудио файл - используем ffmpeg
            import asyncio
            asyncio.run(convert_audio_ffmpeg_async(input_path, output_path))
        else:
            # Видео файл - извлекаем аудио
            clip = VideoFileClip(input_path)
            clip.audio.write_audiofile(output_path, verbose=False, logger=None)
            clip.close()
        
        return output_path
    
    def convert_to_mp4(self, input_path: str, output_path: str) -> str:
        """Конвертирует файл в MP4 (сжимает видео)."""
        try:
            import asyncio
            asyncio.run(compress_video_ffmpeg_async(
                input_path, output_path, 
                crf=30, max_width=640, audio_bitrate="64k", preset="fast"
            ))
        except Exception:
            # Fallback к moviepy
            clip = VideoFileClip(input_path)
            clip.write_videofile(
                output_path, codec="libx264", bitrate="600k", 
                fps=24, audio=True, verbose=False, logger=None
            )
            clip.close()
        
        return output_path
    
    def convert_to_gif(self, input_path: str, output_path: str) -> str:
        """Конвертирует видео в GIF."""
        try:
            import asyncio
            asyncio.run(convert_video_to_gif_ffmpeg_async(
                input_path, output_path, width=480, fps=12
            ))
        except Exception:
            # Fallback к moviepy
            clip = VideoFileClip(input_path)
            clip.write_gif(output_path, fps=12)
            clip.close()
        
        return output_path
    
    def convert_pdf_to_png(self, input_path: str, output_dir: str = None) -> list:
        """Конвертирует PDF в PNG изображения."""
        if fitz is None:
            raise ImportError("PyMuPDF не установлен: pip install PyMuPDF")
        
        if output_dir is None:
            output_dir = os.path.join(self.temp_dir, "pdf_images")
        
        os.makedirs(output_dir, exist_ok=True)
        
        doc = fitz.open(input_path)
        image_paths = []
        
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_path = os.path.join(output_dir, f"page_{i + 1}.png")
            pix.save(img_path)
            image_paths.append(img_path)
        
        doc.close()
        return image_paths
    
    def convert_pdf_to_zip(self, input_path: str, output_path: str = None) -> str:
        """Конвертирует PDF в ZIP архив с изображениями страниц."""
        if output_path is None:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(self.temp_dir, f"{base_name}.zip")
        
        temp_images = []
        try:
            doc = fitz.open(input_path)
            try:
                with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
                    for i, page in enumerate(doc):
                        try:
                            # Создаем изображение с оптимизированными настройками
                            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))  # Уменьшаем разрешение для экономии памяти
                            img_name = f"page_{i + 1}.png"
                            img_path = os.path.join(self.temp_dir, img_name)
                            temp_images.append(img_path)
                            
                            pix.save(img_path)
                            zipf.write(img_path, img_name)
                            
                            # Очищаем память после каждой страницы
                            del pix
                            
                        except Exception as e:
                            # Пропускаем проблемные страницы
                            continue
            finally:
                doc.close()
                
        except Exception as e:
            # В случае ошибки удаляем ZIP файл если он был создан
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
            raise e
        
        finally:
            # Очищаем временные изображения
            for img_path in temp_images:
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                except OSError:
                    pass

        return output_path

    def convert_png_to_jpg(self, input_path: str, target_ext: str = "jpg") -> str:
        """Конвертирует PNG в JPG/JPEG."""
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(self.temp_dir, f"{base_name}.{target_ext}")
        
        img = Image.open(input_path)
        
        # Конвертируем RGBA в RGB если нужно
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
    
    def convert_to_txt(self, input_path: str, output_path: str = None) -> str:
        """Конвертирует различные форматы в TXT."""
        if output_path is None:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(self.temp_dir, f"{base_name}.txt")
        
        ext = os.path.splitext(input_path)[1].lower()
        
        try:
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
                                    # Пропускаем поврежденные файлы в архиве
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
        
        except Exception as e:
            # Если произошла ошибка, пробуем удалить частично созданный файл
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
            raise e
        
        return output_path
    
    def convert_file(self, input_path: str, target_format: str) -> str:
        """Универсальный метод конвертации файлов."""
        if target_format not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Неподдерживаемый формат: {target_format}")
        
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
                zip_path = os.path.join(self.temp_dir, "pdf_pages.zip")
                with zipfile.ZipFile(zip_path, "w") as zipf:
                    for img in image_paths:
                        zipf.write(img, os.path.basename(img))
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
    
    def cleanup_files(self, *file_paths: str):
        """Улучшенная очистка временных файлов с подробным логированием."""
        import glob
        cleaned_count = 0
        error_count = 0
        
        # Очищаем указанные файлы
        for file_path in file_paths:
            if not file_path:
                continue
                
            try:
                if os.path.exists(file_path):
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        cleaned_count += 1
                    elif os.path.isdir(file_path):
                        # Рекурсивно удаляем директорию со всем содержимым
                        shutil.rmtree(file_path, ignore_errors=True)
                        cleaned_count += 1
            except (OSError, PermissionError) as e:
                error_count += 1
                print(f"Warning: Could not delete {file_path}: {e}")
            except Exception as e:
                error_count += 1
                print(f"Unexpected error deleting {file_path}: {e}")
        
        # Дополнительная очистка временной директории
        try:
            if os.path.exists(self.temp_dir):
                # Удаляем старые файлы (старше 1 часа)
                import time
                current_time = time.time()
                
                # Очищаем старые файлы
                for filename in os.listdir(self.temp_dir):
                    file_path = os.path.join(self.temp_dir, filename)
                    if os.path.isfile(file_path):
                        try:
                            file_age = current_time - os.path.getmtime(file_path)
                            if file_age > 3600:  # 1 час
                                os.remove(file_path)
                                cleaned_count += 1
                        except OSError:
                            pass
                    elif os.path.isdir(file_path):
                        try:
                            # Удаляем старые директории (старше 1 часа)
                            dir_age = current_time - os.path.getmtime(file_path)
                            if dir_age > 3600:
                                shutil.rmtree(file_path, ignore_errors=True)
                                cleaned_count += 1
                        except OSError:
                            pass
                            
        except Exception as e:
            print(f"Error during additional cleanup: {e}")
            error_count += 1
        
        # Логируем результат очистки
        if cleaned_count > 0 or error_count > 0:
            print(f"Cleanup: {cleaned_count} files cleaned, {error_count} errors")
        
        return cleaned_count, error_count


# Глобальный экземпляр конвертера
file_converter = FileConverter()