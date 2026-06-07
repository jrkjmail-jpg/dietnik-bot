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
            [KeyboardButton(text="🍽 Добавить еду"), KeyboardButton(text="📊 Дневник")],
            [KeyboardButton(text="💡 Рекомендации"), KeyboardButton(text="🤖 Диетолог")],
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💳 Подписка")],
            [KeyboardButton(text="🍳 Рецепты"), KeyboardButton(text="📈 Отчёты")],
        ],
        resize_keyboard=True,
    )


def subscription_keyboard(premium_price_xtr: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ Купить Premium — {premium_price_xtr} Stars",
                    callback_data="buy_premium",
                )
            ],
            [
                InlineKeyboardButton(text="Условия", callback_data="payment_terms"),
                InlineKeyboardButton(text="Поддержка", callback_data="payment_support"),
            ],
        ]
    )


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="7 дней", callback_data="report_7"),
                InlineKeyboardButton(text="30 дней", callback_data="report_30"),
                InlineKeyboardButton(text="Весь период", callback_data="report_all"),
            ]
        ]
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
