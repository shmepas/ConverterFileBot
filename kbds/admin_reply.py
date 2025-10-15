from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# === Размер страницы для пагинации логов ===
PAGE_SIZE = 20

# === Главное меню (для возврата после выхода из админки) ===
def main_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("👤 Пользовательская часть", callback_data="user_menu"),
        InlineKeyboardButton("🛠 Админка", callback_data="admin_panel")
    )
    return kb

# === Клавиатура для обычного админа ===
def admin_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📜 Просмотр логов", callback_data="view_logs"),
        InlineKeyboardButton("⬅️ Закрыть админку", callback_data="close_admin")
    )
    return kb

# === Клавиатура супер-админа ===
def super_admin_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📜 Все логи пользователей", callback_data="view_all_logs"),
        InlineKeyboardButton("➕ Добавить админа", callback_data="add_admin"),
        InlineKeyboardButton("➖ Удалить админа", callback_data="remove_admin"),
        InlineKeyboardButton("⚙️ Настройки системы", callback_data="system_settings"),
        InlineKeyboardButton("⬅️ Закрыть админку", callback_data="close_admin")
    )
    return kb

# === Кнопки пагинации для логов ===
def logs_pagination_kb(page: int, total_pages: int):
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = []

    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"logs_page_{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"logs_page_{page+1}"))

    if buttons:
        kb.row(*buttons)

    kb.add(InlineKeyboardButton("⬅️ Выйти из логов", callback_data="exit_logs"))
    return kb

# === Клавиатура для выбора пользователя для логов ===
def users_keyboard(users: list):
    """
    users: список словарей с ключами 'user_id' и 'username'
    """
    kb = InlineKeyboardMarkup(row_width=1)
    for user in users:
        display_name = user["username"] or f"User {user['user_id']}"
        kb.add(InlineKeyboardButton(display_name, callback_data=f"showlog:{user['user_id']}"))
    return kb

# === Клавиатура для выбора пользователей при добавлении/удалении админа (с кнопкой Отмена) ===
def users_for_admin_kb(users: list[dict]):
    kb = InlineKeyboardMarkup(row_width=1)
    for user in users:
        status = "➕" if not user["is_admin"] else "➖"
        display_name = user.get("username") or f"User {user['id']}"
        kb.add(
            InlineKeyboardButton(
                text=f"{display_name} {status}",
                callback_data=f"toggle_admin:{user['id']}"
            )
        )
    # Кнопка "Отмена"
    kb.add(InlineKeyboardButton("⬅️ Отмена", callback_data="cancel_add_admin"))
    return kb
