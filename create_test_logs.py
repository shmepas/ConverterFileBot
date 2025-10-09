import asyncio
from data_base import db

TEST_USER_ID = 926512611  # ID пользователя или обычного админа, для которого создаём тестовые логи
NUM_LOGS = 50              # количество тестовых логов

async def main():
    # Проверяем, есть ли пользователь, если нет — добавляем
    await db.add_user(
        user_id=TEST_USER_ID,
        username="test_user",
        first_name="Test",
        last_name="User"
    )

    # Добавляем тестовые действия
    for i in range(1, NUM_LOGS + 1):
        await db.log_action(TEST_USER_ID, f"Тестовое действие #{i}")

    print(f"Создано {NUM_LOGS} тестовых логов для пользователя {TEST_USER_ID}.")

if __name__ == "__main__":
    asyncio.run(main())
