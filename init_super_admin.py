import asyncio
import os
from data_base.db import init_super_admin

try:
    SUPER_ADMIN_ID = int(os.environ["SUPER_ADMIN_ID"])
except (KeyError, ValueError) as exc:
    raise RuntimeError("Set SUPER_ADMIN_ID to the Telegram user ID before running this script") from exc

async def main():
    await init_super_admin(SUPER_ADMIN_ID)
    print(f"✅ Супер-админ {SUPER_ADMIN_ID} добавлен!")

asyncio.run(main())
