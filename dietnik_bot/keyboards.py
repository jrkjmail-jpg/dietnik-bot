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
            [KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True,
    )


def support_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Завершить поддержку")]],
        resize_keyboard=True,
    )


def subscription_keyboard(
    basic_price_rub: int,
    premium_price_rub: int,
    payments_enabled: bool,
    current_plan: str = "trial",
    premium_only: bool = False,
    premium_upgrade_price_rub: int | None = None,
    renewal_available: bool = False,
) -> InlineKeyboardMarkup:
    if payments_enabled:
        payment_buttons = []
        if (
            not premium_only
            and current_plan != "premium"
            and (current_plan != "basic" or renewal_available)
        ):
            basic_label = (
                f"Продлить Basic — {basic_price_rub} ₽"
                if current_plan == "basic"
                else f"🌱 Basic — {basic_price_rub} ₽"
            )
            payment_buttons.append(
                [
                    InlineKeyboardButton(
                        text=basic_label,
                        callback_data="buy_basic",
                    )
                ]
            )
        if current_plan != "premium" or renewal_available:
            premium_label = (
                f"⬆️ Повысить до Premium — {premium_upgrade_price_rub} ₽"
                if current_plan == "basic" and premium_upgrade_price_rub is not None
                else (
                    f"Продлить Premium — {premium_price_rub} ₽"
                    if current_plan == "premium"
                    else f"🌿 Premium — {premium_price_rub} ₽"
                )
            )
            payment_buttons.append(
                [
                    InlineKeyboardButton(
                        text=premium_label,
                        callback_data="buy_premium",
                    )
                ]
            )
    else:
        payment_buttons = [
            [
                InlineKeyboardButton(
                    text="Оплата настраивается",
                    callback_data="payment_unavailable",
                )
            ]
        ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            *payment_buttons,
            [
                InlineKeyboardButton(text="Условия", callback_data="payment_terms"),
                InlineKeyboardButton(text="Поддержка", callback_data="payment_support"),
            ],
        ]
    )


def trial_keyboard(trial_available: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if trial_available:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📸 Попробовать бесплатно",
                    callback_data="start_food_trial",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="💳 Выбрать подписку",
                callback_data="show_subscriptions",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
