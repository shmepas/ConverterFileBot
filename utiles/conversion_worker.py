"""Isolated conversion process. The parent enforces timeouts and can terminate its process tree."""
import asyncio
import json
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from converter_service import file_converter


async def main() -> int:
    if len(sys.argv) != 5:
        return 2
    source_path, target_format, user_id, result_path = sys.argv[1:]
    result_file = Path(result_path)
    payload = {"ok": False, "error": "Conversion worker failed"}
    try:
        output_path = await file_converter.convert_file_with_retry(
            source_path, target_format, max_retries=2, user_id=int(user_id)
        )
        payload = {"ok": True, "output_path": str(Path(output_path).resolve())}
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
    finally:
        result_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
