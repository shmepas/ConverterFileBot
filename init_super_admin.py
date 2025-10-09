import asyncio
from data_base.db import init_super_admin

SUPER_ADMIN_ID = 1121684677  # <-- твой Telegram ID

async def main():
    await init_super_admin(SUPER_ADMIN_ID)
    print(f"✅ Супер-админ {SUPER_ADMIN_ID} добавлен!")

asyncio.run(main())
