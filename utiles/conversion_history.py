"""Short-lived, private source retention for conversion history and repeat actions."""
import os
import shutil
import time
import uuid
from pathlib import Path

from data_base import db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORY_ROOT = PROJECT_ROOT / "uploads" / "history"
MAX_HISTORY_BYTES_PER_USER = 300 * 1024 * 1024


def _safe_history_root(create: bool = False) -> Path | None:
    uploads = PROJECT_ROOT / "uploads"
    if uploads.is_symlink() or HISTORY_ROOT.is_symlink():
        return None
    try:
        if create:
            HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
        root = HISTORY_ROOT.resolve(strict=True)
        root.relative_to(PROJECT_ROOT)
        return root
    except (OSError, ValueError):
        return None


def _retention_hours() -> int:
    try:
        return max(1, min(168, int(os.getenv("FILE_RETENTION_HOURS", "24"))))
    except ValueError:
        return 24


def _safe_extension(source_name: str) -> str:
    extension = Path(source_name).suffix.lower()
    if extension in {".mp3", ".wav", ".ogg", ".mp4", ".mov", ".m4v", ".gif",
                     ".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md", ".zip"}:
        return extension
    return ".bin"


def resolve_retained_path(stored_path: str) -> Path | None:
    try:
        relative = Path(stored_path)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        root = _safe_history_root()
        if root is None:
            return None
        raw_candidate = PROJECT_ROOT / relative
        raw_candidate.relative_to(HISTORY_ROOT)
        current = raw_candidate
        while current != HISTORY_ROOT:
            if current.is_symlink():
                return None
            current = current.parent
        candidate = raw_candidate.resolve(strict=True)
        candidate.relative_to(root)
        if not candidate.is_file():
            return None
        return candidate
    except (OSError, ValueError):
        return None


async def retain_source(user_id: int, source_name: str, detected_type: str,
                        source_path: str) -> int | None:
    """Copy a successful input into a bounded private history area."""
    source = Path(source_path)
    if not source.is_file() or source.is_symlink():
        return None
    root = _safe_history_root(create=True)
    if root is None or user_id <= 0:
        return None
    user_dir = root / str(user_id)
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
        if user_dir.is_symlink():
            return None
        user_dir = user_dir.resolve(strict=True)
        user_dir.relative_to(root)
        existing_bytes = sum(
            path.stat().st_size for path in user_dir.iterdir()
            if path.is_file() and not path.is_symlink()
        )
        if existing_bytes + source.stat().st_size > MAX_HISTORY_BYTES_PER_USER:
            return None
        destination = user_dir / f"{uuid.uuid4().hex}{_safe_extension(source_name)}"
        shutil.copyfile(source, destination)
        if destination.is_symlink() or not destination.is_file():
            destination.unlink(missing_ok=True)
            return None
        relative_path = destination.relative_to(PROJECT_ROOT).as_posix()
        try:
            return await db.add_retained_conversion_file(
                user_id, source_name, detected_type, relative_path, _retention_hours()
            )
        except Exception:
            destination.unlink(missing_ok=True)
            return None
    except OSError:
        return None


async def cleanup_expired_files() -> int:
    """Remove expired retained inputs and abandoned download files safely."""
    expired = await db.cleanup_expired_conversion_history(retention_days=90)
    removed = 0
    for row in expired:
        path = resolve_retained_path(row["stored_path"])
        if path is not None:
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue

    downloads_path = PROJECT_ROOT / "downloads"
    try:
        downloads_root = downloads_path.resolve(strict=True)
        downloads_root.relative_to(PROJECT_ROOT)
    except (OSError, ValueError):
        downloads_root = None
    if downloads_root is not None and not downloads_path.is_symlink() and downloads_root.is_dir():
        cutoff = time.time() - 48 * 60 * 60
        for path in downloads_root.iterdir():
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    converted_base = PROJECT_ROOT / "converted"
    converted_root = converted_base / "runtime"
    if (converted_base.is_dir() and not converted_base.is_symlink()
            and converted_root.is_dir() and not converted_root.is_symlink()):
        cutoff = time.time() - 48 * 60 * 60
        for path in converted_root.iterdir():
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
                elif path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    shutil.rmtree(path)
                    removed += 1
            except OSError:
                continue
    return removed
