from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .reply import get_main_menu_kb

# === Размер страницы для пагинации логов ===
PAGE_SIZE = 20

# === Главное меню (для возврата после выхода из админки) ===
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Пользовательская часть", callback_data="user_menu")
    kb.button(text="🛠 Админка", callback_data="admin_panel")
    return kb.adjust(1).as_markup()

# === Клавиатура для обычного админа ===
def admin_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📜 Просмотр логов", callback_data="view_logs")
    kb.button(text="⬅️ Закрыть админку", callback_data="close_admin")
    return kb.adjust(1).as_markup()

# === Клавиатура супер-админа ===
def super_admin_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📜 Все логи пользователей", callback_data="view_all_logs")
    kb.button(text="➕ Добавить админа", callback_data="add_admin")
    kb.button(text="➖ Удалить админа", callback_data="remove_admin")
    kb.button(text="⚙️ Настройки системы", callback_data="system_settings")
    kb.button(text="⬅️ Закрыть админку", callback_data="close_admin")
    return kb.adjust(2).as_markup()

# === Кнопки пагинации для логов ===
def logs_pagination_kb(page: int, total_pages: int):
    kb = InlineKeyboardBuilder()
    buttons = []

    if page > 1:
        buttons.append(("⬅️ Назад", f"logs_page_{page-1}"))
    if page < total_pages:
        buttons.append(("Вперёд ➡️", f"logs_page_{page+1}"))

    if buttons:
        for text, callback_data in buttons:
            kb.button(text=text, callback_data=callback_data)

    kb.button(text="⬅️ Выйти из логов", callback_data="exit_logs")
    return kb.adjust(2).as_markup()

# === Клавиатура для выбора пользователя для логов ===
def users_keyboard(users: list):
    """
    users: список словарей с ключами 'user_id' и 'username'
    """
    kb = InlineKeyboardBuilder()
    for user in users:
        display_name = user["username"] or f"User {user['user_id']}"
        kb.button(text=display_name, callback_data=f"showlog:{user['user_id']}")
    return kb.adjust(1).as_markup()

# === Клавиатура для выбора пользователей при добавлении/удалении админа (с кнопкой Отмена) ===
def users_for_admin_kb(users: list[dict]):
    kb = InlineKeyboardBuilder()
    for user in users:
        status = "➕" if not user["is_admin"] else "➖"
        display_name = user.get("username") or f"User {user['id']}"
        kb.button(text=f"{display_name} {status}", callback_data=f"toggle_admin:{user['id']}")
    # Кнопка "Отмена"
    kb.button(text="⬅️ Отмена", callback_data="cancel_add_admin")
    return kb.adjust(1).as_markup()


admin_router = Router()

@admin_router.callback_query(F.data == "close_admin")
async def close_admin_panel(callback: CallbackQuery):
    user_id = callback.from_user.id
    kb = await get_main_menu_kb(user_id)
    await callback.message.edit_text("Админка закрыта. Возврат в главное меню.", reply_markup=kb)
    await callback.answer()
