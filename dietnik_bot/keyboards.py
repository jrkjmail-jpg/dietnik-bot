"""Keyboards used by Dietnik bot."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)


def gender_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Мужской"), KeyboardButton(text="Женский")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def activity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Низкая")],
            [KeyboardButton(text="Средняя")],
            [KeyboardButton(text="Высокая")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def goal_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Похудение")],
            [KeyboardButton(text="Поддержание")],
            [KeyboardButton(text="Набор массы")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🍽 Добавить еду"), KeyboardButton(text="✍️ Добавить вручную")],
            [KeyboardButton(text="📊 Дневник"), KeyboardButton(text="💡 Рекомендации")],
            [KeyboardButton(text="🤖 Диетолог"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="💳 Подписка"), KeyboardButton(text="🧊 Холодильник")],
            [KeyboardButton(text="🍳 Рецепты"), KeyboardButton(text="📈 Отчёты")],
        ],
        resize_keyboard=True,
    )


def subscription_keyboard(payments_enabled: bool) -> InlineKeyboardMarkup:
    if payments_enabled:
        keyboard = [
            [InlineKeyboardButton(text="🌱 Купить Basic — 490 ₽/мес", callback_data="buy_basic")],
            [InlineKeyboardButton(text="🌿 Купить Premium — 890 ₽/мес", callback_data="buy_premium")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton(text="Написать по оплате", url="https://t.me/bothostru")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
