import os
import pypandoc
from PIL import Image
from moviepy.editor import VideoFileClip
from pydub import AudioSegment


async def convert_file(src_path: str, target_format: str) -> str:
    """
    Универсальный конвертер файлов.
    Поддерживает:
      - Текстовые: txt, docx, pdf, md
      - Изображения: jpg, png
      - Видео -> аудио: mp4 → mp3
      - Аудио: mp3, wav
    Возвращает путь к сконвертированному файлу.
    """

    os.makedirs("converted", exist_ok=True)
    ext = os.path.splitext(src_path)[1].lower()
    base_name = os.path.splitext(os.path.basename(src_path))[0]
    dst_path = f"converted/{base_name}.{target_format}"

    try:
        # ТЕКСТОВЫЕ ФОРМАТЫ
        if ext in [".txt", ".docx", ".pdf", ".md"]:
            with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            pypandoc.convert_text(
                content,
                target_format,
                format=ext.replace(".", ""),
                outputfile=dst_path,
                extra_args=["--standalone"]
            )
            return dst_path

        # ИЗОБРАЖЕНИЯ
        elif ext in [".jpg", ".jpeg", ".png"]:
            img = Image.open(src_path)
            img.save(dst_path)
            return dst_path

        # ВИДЕО → АУДИО
        elif ext in [".mp4", ".mov"] and target_format == "mp3":
            clip = VideoFileClip(src_path)
            clip.audio.write_audiofile(dst_path)
            clip.close()
            return dst_path

        # АУДИО → другой формат
        elif ext in [".mp3", ".wav"] and target_format in ["wav", "mp3"]:
            sound = AudioSegment.from_file(src_path)
            sound.export(dst_path, format=target_format)
            return dst_path

        else:
            raise ValueError(f"Невозможно конвертировать {ext} → {target_format}")

    except Exception as e:
        raise RuntimeError(f"Ошибка при конвертации: {e}")
