"""Create verified rolling SQLite backups without blocking the bot's event loop."""
import asyncio
from contextlib import closing
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from data_base.db import DB_PATH


BACKUP_DIR = Path(os.getenv("BOT_BACKUP_DIR", str(Path(__file__).resolve().parent / "backups")))
BACKUP_COUNT = 7


def _create_backup(source_path: str, backup_dir: Path) -> Path | None:
    source = Path(source_path)
    if not source.is_file():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = backup_dir / f"bot_database_{stamp}.sqlite3"
    fd, temp_name = tempfile.mkstemp(prefix=".database_backup_", suffix=".tmp", dir=backup_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with closing(sqlite3.connect(source)) as current, closing(sqlite3.connect(temp_path)) as backup:
            current.backup(backup)
        with closing(sqlite3.connect(temp_path)) as backup:
            result = backup.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise sqlite3.DatabaseError("Backup integrity check failed")
        os.replace(temp_path, destination)
        existing = sorted(
            backup_dir.glob("bot_database_*.sqlite3"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old_backup in existing[BACKUP_COUNT:]:
            old_backup.unlink(missing_ok=True)
        return destination
    finally:
        temp_path.unlink(missing_ok=True)


async def backup_database(source_path: str = DB_PATH, backup_dir: Path = BACKUP_DIR) -> Path | None:
    return await asyncio.to_thread(_create_backup, source_path, backup_dir)
