"""Telegram bot entry point for Dietnik."""

import asyncio
import logging
import tempfile
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from config import (
    ADMIN_IDS,
    AUTO_DB_BACKUP_INTERVAL_HOURS,
    BASIC_PRICE_RUB,
    BOT_RELEASE,
    BOT_TOKEN,
    DATA_DIR,
    DB_PATH,
    OPENAI_API_KEY,
    PAYMENT_PROVIDER_TOKEN,
    PERSISTENCE_PATH,
    PREMIUM_PRICE_RUB,
    SUPPORT_ADMIN_CHAT_ID,
    SUPPORT_AI_ENABLED,
    SUPPORT_ATTACHMENT_MAX_FILE_BYTES,
    SUPPORT_MESSAGE_MAX_CHARS,
    SUPPORT_USERNAME,
    validate_config,
)
from database import (
    activate_subscription_payment,
    get_admin_stats,
    get_all_user_ids,
    get_app_state,
    get_db_status,
    get_recent_payments,
    get_recent_meals,
    get_storage_probe_status,
    get_support_status,
    get_support_user_by_admin_message,
    get_today_stats,
    get_user,
    get_user_meals,
    get_users_page,
    get_period_stats,
    init_db,
    ensure_support_thread,
    get_recent_support_messages,
    log_support_message,
    mark_trial_used,
    remember_support_admin_message,
    reset_today,
    restore_database_file,
    set_app_state,
    set_support_status,
    save_meal,
    save_user,
    set_subscription,
)
from keyboards import (
    activity_keyboard,
    gender_keyboard,
    goal_keyboard,
    main_menu_keyboard,
    remove_keyboard,
    reports_keyboard,
    subscription_keyboard,
    support_keyboard,
    trial_keyboard,
)
from nutrition import calculate_norm, calculate_remaining
from openai_service import analyze_food_photo, ask_dietitian, ask_support_ai


router = Router()
logger = logging.getLogger(__name__)


class Onboarding(StatesGroup):
    gender = State()
    age = State()
    height = State()
    weight = State()
    activity = State()
    goal = State()


class Consultation(StatesGroup):
    question = State()


class SupportChat(StatesGroup):
    active = State()


class ManualMeal(StatesGroup):
    dish = State()
    calories = State()
    protein = State()
    fat = State()
    carbs = State()


class AdminPanel(StatesGroup):
    broadcast_text = State()
    restore_db_file = State()


MONTH_DAYS = 30
SUBSCRIPTION_OFFERS = {
    "basic": {
        "price": BASIC_PRICE_RUB,
        "payload": "dietnik_basic_30_rub",
        "title": "Dietnik Basic на 30 дней",
        "description": "Фото-учёт еды, дневник КБЖУ, цели и рекомендации.",
    },
    "premium": {
        "price": PREMIUM_PRICE_RUB,
        "payload": "dietnik_premium_30_rub",
        "title": "Dietnik Premium на 30 дней",
        "description": "Всё из Basic, AI-диетолог, рецепты и расширенные отчёты.",
    },
}

RECIPE_LIBRARY = [
    {
        "title": "Куриная тарелка с гречкой и салатом",
        "calories": 520,
        "protein": 46,
        "fat": 14,
        "carbs": 52,
        "ingredients": ["курица", "гречка", "огурец", "помидор", "зелень"],
        "steps": "Отвари гречку, обжарь или запеки курицу без лишнего масла, добавь салат из овощей.",
        "note": "Хороший вариант, когда нужно добрать белок без тяжёлого ужина.",
        "goals": {"Похудение", "Поддержание"},
        "tags": {"protein", "dinner"},
    },
    {
        "title": "Омлет с творогом и овощами",
        "calories": 360,
        "protein": 34,
        "fat": 18,
        "carbs": 14,
        "ingredients": ["яйца", "творог", "помидор", "шпинат", "сыр"],
        "steps": "Смешай яйца с творогом, добавь овощи и готовь на сковороде под крышкой.",
        "note": "Плотный белковый приём пищи, удобно на завтрак или поздний ужин.",
        "goals": {"Похудение", "Поддержание"},
        "tags": {"protein", "breakfast", "light"},
    },
    {
        "title": "Рис с тунцом и овощами",
        "calories": 610,
        "protein": 42,
        "fat": 16,
        "carbs": 74,
        "ingredients": ["рис", "тунец", "огурец", "кукуруза", "йогурт"],
        "steps": "Смешай готовый рис с тунцом и овощами, заправь йогуртом или лимонным соком.",
        "note": "Баланс белка и углеводов после активного дня или тренировки.",
        "goals": {"Поддержание", "Набор массы"},
        "tags": {"protein", "carbs", "dinner"},
    },
    {
        "title": "Творог с ягодами и орехами",
        "calories": 310,
        "protein": 28,
        "fat": 11,
        "carbs": 24,
        "ingredients": ["творог", "ягоды", "орехи", "йогурт"],
        "steps": "Смешай творог с йогуртом, добавь ягоды и немного орехов сверху.",
        "note": "Лёгкий способ добрать белок, когда калорий осталось немного.",
        "goals": {"Похудение", "Поддержание"},
        "tags": {"protein", "light", "breakfast"},
    },
    {
        "title": "Паста с индейкой",
        "calories": 720,
        "protein": 50,
        "fat": 20,
        "carbs": 86,
        "ingredients": ["паста", "индейка", "томат", "сыр", "зелень"],
        "steps": "Отвари пасту, приготовь индейку в томатном соусе и добавь немного сыра.",
        "note": "Сытный вариант для набора массы или дня с высоким расходом энергии.",
        "goals": {"Набор массы", "Поддержание"},
        "tags": {"carbs", "dinner"},
    },
    {
        "title": "Рыба с картофелем и зелёным салатом",
        "calories": 480,
        "protein": 38,
        "fat": 15,
        "carbs": 46,
        "ingredients": ["рыба", "картофель", "листовой салат", "огурец", "лимон"],
        "steps": "Запеки рыбу и картофель, собери салат и добавь лимонный сок.",
        "note": "Спокойный ужин с белком и умеренными углеводами.",
        "goals": {"Похудение", "Поддержание"},
        "tags": {"protein", "dinner", "light"},
    },
    {
        "title": "Овсянка с протеином и бананом",
        "calories": 430,
        "protein": 30,
        "fat": 10,
        "carbs": 58,
        "ingredients": ["овсянка", "протеин", "банан", "молоко", "корица"],
        "steps": "Приготовь овсянку, вмешай протеин после нагрева и добавь банан.",
        "note": "Удобный завтрак, если нужно добрать углеводы без тяжёлой еды.",
        "goals": {"Поддержание", "Набор массы"},
        "tags": {"breakfast", "carbs", "protein"},
    },
]

WELCOME_TEXT = """
👋 Привет! Я Dietnik — твой AI-помощник по питанию.

Я помогу:
📸 считать калории по фото еды
📊 вести дневной прогресс
🎯 показывать, сколько осталось до цели
💡 давать рекомендации по питанию

После настройки откроется главное меню.
""".strip()

HELP_TEXT = """
Как пользоваться:
1. Пройди настройку через /start
2. Попробуй один анализ фото бесплатно
3. Выбери Basic или Premium
4. Отправляй фото еды и следи за прогрессом

Основные кнопки:
🍽 Добавить еду — отправь фото блюда
📊 Дневник — прогресс за день
💡 Рекомендации — что улучшить сегодня
🤖 Диетолог — вопрос AI-диетологу
💳 Подписка — тарифы Basic и Premium
💬 Поддержка — задать вопрос AI или администратору

Важно:
Оценка по фото может отличаться от реальности. Для максимальной точности используй весы и проверяй порции.

Все команды: /commands
""".strip()


def _format_norm(user: dict) -> str:
    return (
        f"🔥 Калории: {user['norm_calories']} ккал\n"
        f"🥩 Белки: {user['norm_protein']} г\n"
        f"🥑 Жиры: {user['norm_fat']} г\n"
        f"🍚 Углеводы: {user['norm_carbs']} г"
    )


def _safe(value: object) -> str:
    """Escape user, OpenAI and database text for Telegram HTML parse mode."""
    return escape(str(value or ""))


def _subscription_expiry(user: dict) -> date | None:
    raw_expiry = user.get("subscription_until") or user.get("premium_until")
    if not raw_expiry:
        return None
    try:
        return datetime.fromisoformat(raw_expiry).date()
    except ValueError:
        return date.min


def _has_active_subscription(user: dict) -> bool:
    plan = user.get("subscription_plan")
    if plan not in {"basic", "premium"}:
        return False
    expiry = _subscription_expiry(user)
    return expiry is None or expiry >= date.today()


def _is_premium(user: dict) -> bool:
    return user.get("subscription_plan") == "premium" and _has_active_subscription(user)


def _trial_available(user: dict) -> bool:
    return (
        user.get("subscription_plan") == "trial"
        and not bool(user.get("trial_used"))
    )


def _subscription_name(user: dict) -> str:
    if _is_premium(user):
        return "Premium"
    if user.get("subscription_plan") == "basic" and _has_active_subscription(user):
        return "Basic"
    if _trial_available(user):
        return "Пробный доступ"
    return "Нет активной подписки"


def _progress_bar(value: int, target: int, width: int = 10) -> str:
    if target <= 0:
        return "⬜" * width
    filled = min(width, round(value / target * width))
    return "🟩" * filled + "⬜" * (width - filled)


def _percent(value: int, target: int) -> int:
    if target <= 0:
        return 0
    return min(999, round(value / target * 100))


def _format_payment_totals(totals: dict[str, int]) -> str:
    parts = []
    for currency, amount in totals.items():
        if currency == "RUB":
            parts.append(f"{amount / 100:.2f} RUB")
        else:
            parts.append(f"{amount} {currency}")
    return ", ".join(parts) or "0"


def _days_from_created_at(user: dict) -> int:
    created_at = user.get("created_at")
    if not created_at:
        return 1
    try:
        created_date = datetime.fromisoformat(created_at).date()
    except ValueError:
        return 1
    return max(1, (date.today() - created_date).days + 1)


def _format_progress(user: dict, stats: dict) -> str:
    remaining = calculate_remaining(user, stats)
    return (
        "📊 Твой прогресс за сегодня:\n\n"
        f"🔥 Калории: {stats['calories']} / {user['norm_calories']} ккал\n"
        f"{_progress_bar(stats['calories'], user['norm_calories'])} "
        f"{_percent(stats['calories'], user['norm_calories'])}%\n\n"
        f"🥩 Белки: {stats['protein']} / {user['norm_protein']} г\n"
        f"{_progress_bar(stats['protein'], user['norm_protein'])} "
        f"{_percent(stats['protein'], user['norm_protein'])}%\n\n"
        f"🥑 Жиры: {stats['fat']} / {user['norm_fat']} г\n"
        f"{_progress_bar(stats['fat'], user['norm_fat'])} "
        f"{_percent(stats['fat'], user['norm_fat'])}%\n\n"
        f"🍚 Углеводы: {stats['carbs']} / {user['norm_carbs']} г\n"
        f"{_progress_bar(stats['carbs'], user['norm_carbs'])} "
        f"{_percent(stats['carbs'], user['norm_carbs'])}%\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "🎯 Осталось до цели:\n\n"
        f"🔥 Калории: {remaining['calories']} ккал\n"
        f"🥩 Белки: {remaining['protein']} г\n"
        f"🥑 Жиры: {remaining['fat']} г\n"
        f"🍚 Углеводы: {remaining['carbs']} г"
    )


def _format_dashboard(user: dict, stats: dict, first_name: str | None = None) -> str:
    remaining = calculate_remaining(user, stats)
    name = _safe(first_name or "друг")
    day_number = _days_from_created_at(user)
    recent_meals = get_recent_meals(user["telegram_id"], limit=3)
    meals_text = "\n".join(
        f"• {_safe(meal['dish_name'])} — {meal['calories']} ккал" for meal in recent_meals
    )
    if not meals_text:
        meals_text = "Ты ещё ничего не записал 🙈"

    return (
        "🥗 Главное меню\n\n"
        f"Привет, {name}! 👋\n\n"
        f"📍 Траектория: день {day_number}\n"
        f"🎯 Цель: {user['goal']}\n"
        f"⚖️ Вес: {user['weight']} кг\n"
        f"💳 Тариф: {_subscription_name(user)}\n\n"
        f"🔥 Калории: {stats['calories']} / {user['norm_calories']} ккал\n"
        f"{_progress_bar(stats['calories'], user['norm_calories'])} "
        f"{_percent(stats['calories'], user['norm_calories'])}%\n\n"
        f"🥩 Белки: {stats['protein']} / {user['norm_protein']} г\n"
        f"{_progress_bar(stats['protein'], user['norm_protein'])} "
        f"{_percent(stats['protein'], user['norm_protein'])}%\n\n"
        f"🥑 Жиры: {stats['fat']} / {user['norm_fat']} г\n"
        f"{_progress_bar(stats['fat'], user['norm_fat'])} "
        f"{_percent(stats['fat'], user['norm_fat'])}%\n\n"
        f"🍚 Углеводы: {stats['carbs']} / {user['norm_carbs']} г\n"
        f"{_progress_bar(stats['carbs'], user['norm_carbs'])} "
        f"{_percent(stats['carbs'], user['norm_carbs'])}%\n\n"
        "🎯 Осталось:\n"
        f"🔥 {remaining['calories']} ккал · 🥩 {remaining['protein']} г · "
        f"🥑 {remaining['fat']} г · 🍚 {remaining['carbs']} г\n\n"
        "🍽 Сегодня:\n"
        f"{meals_text}\n\n"
        "Что делаем?"
    )


def _format_subscription(user: dict | None) -> str:
    current_plan = _subscription_name(user) if user else "не выбран"
    expiry = _subscription_expiry(user) if user else None
    expiry_line = (
        f"\nДоступ активен до: {expiry.isoformat()}"
        if user and _has_active_subscription(user) and expiry
        else ""
    )
    return (
        "💳 Подписка Dietnik\n\n"
        f"Текущий тариф: {current_plan}{expiry_line}\n\n"
        f"🌱 Basic — {BASIC_PRICE_RUB} ₽ / 30 дней\n"
        "Дневник · фото-учёт · AI-анализ еды · дневная цель · рекомендации\n\n"
        f"🌿 Premium — {PREMIUM_PRICE_RUB} ₽ / 30 дней\n"
        "Всё из Basic · AI-диетолог · рецепты под остаток КБЖУ · "
        "отчёты за 7, 30 дней и весь период\n\n"
        "Premium делает бота персональным ассистентом, а не просто счётчиком калорий."
    )


def _subscription_markup(user: dict) -> InlineKeyboardMarkup:
    current_plan = (
        (user.get("subscription_plan") or "trial")
        if _has_active_subscription(user)
        else "trial"
    )
    return subscription_keyboard(
        BASIC_PRICE_RUB,
        PREMIUM_PRICE_RUB,
        bool(PAYMENT_PROVIDER_TOKEN),
        current_plan=current_plan,
    )


def _locked_text(user: dict) -> str:
    if _trial_available(user):
        return (
            "Сначала попробуй анализ еды бесплатно: нажми кнопку и отправь одно фото блюда.\n\n"
            "После пробного анализа выбери Basic или Premium."
        )
    return (
        "🔒 Для этой функции нужна активная подписка.\n\n"
        f"🌱 Basic — {BASIC_PRICE_RUB} ₽: фото еды, дневник, КБЖУ и рекомендации.\n"
        f"🌿 Premium — {PREMIUM_PRICE_RUB} ₽: всё из Basic, AI-диетолог, "
        "рецепты и отчёты."
    )


async def _require_subscription(message: Message, user: dict | None = None) -> bool:
    user = user or get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return False
    if _has_active_subscription(user):
        return True
    if _trial_available(user):
        await message.answer(
            _locked_text(user),
            reply_markup=trial_keyboard(True),
        )
    else:
        await message.answer(
            _locked_text(user),
            reply_markup=_subscription_markup(user),
        )
    return False


def _format_report(
    user: dict,
    rows: list[dict],
    label: str,
    expected_days: int | None = None,
) -> str:
    if not rows:
        return f"📈 Отчёт: {label}\n\nЗа этот период пока нет записей."

    recorded_days = len(rows)
    meals_count = sum(int(day["meals_count"]) for day in rows)
    first_day = date.fromisoformat(rows[0]["date"])
    last_day = date.fromisoformat(rows[-1]["date"])
    calendar_days = expected_days or max(1, (last_day - first_day).days + 1)
    avg_calories = round(sum(day["calories"] for day in rows) / recorded_days)
    avg_protein = round(sum(day["protein"] for day in rows) / recorded_days)
    avg_fat = round(sum(day["fat"] for day in rows) / recorded_days)
    avg_carbs = round(sum(day["carbs"] for day in rows) / recorded_days)

    calorie_target_days = sum(
        user["norm_calories"] * 0.85 <= day["calories"] <= user["norm_calories"] * 1.15
        for day in rows
    )
    protein_target_days = sum(
        day["protein"] >= user["norm_protein"] * 0.9 for day in rows
    )
    logging_rate = round(recorded_days / calendar_days * 100)

    if avg_calories > user["norm_calories"] * 1.15:
        conclusion = "Средняя калорийность выше цели. Проверь размер порций и калорийные добавки."
    elif avg_calories < user["norm_calories"] * 0.75:
        conclusion = "Средняя калорийность заметно ниже цели. Не урезай рацион слишком резко."
    elif avg_protein < user["norm_protein"] * 0.9:
        conclusion = "Калории близки к цели, но стоит добавить белок в основные приёмы пищи."
    else:
        conclusion = "Рацион в целом близок к цели. Сохраняй регулярность записей."

    return (
        f"📈 Отчёт: {label}\n"
        f"Период записей: {first_day.strftime('%d.%m.%Y')} — {last_day.strftime('%d.%m.%Y')}\n\n"
        f"🍽 Приёмов пищи: {meals_count}\n"
        f"🗓 Дней с записями: {recorded_days}\n"
        f"✍️ Регулярность дневника: {logging_rate}%\n\n"
        "Среднее за день с записями:\n"
        f"🔥 {avg_calories} / {user['norm_calories']} ккал\n"
        f"🥩 {avg_protein} / {user['norm_protein']} г\n"
        f"🥑 {avg_fat} / {user['norm_fat']} г\n"
        f"🍚 {avg_carbs} / {user['norm_carbs']} г\n\n"
        f"🎯 Калории в коридоре цели: {calorie_target_days} из {recorded_days} дней\n"
        f"💪 Белок не ниже 90% цели: {protein_target_days} из {recorded_days} дней\n\n"
        f"Вывод: {conclusion}"
    )


def _format_recommendations(user: dict, stats: dict) -> str:
    remaining = calculate_remaining(user, stats)
    tips = []
    if stats["calories"] == 0:
        tips.append("Начни с первого фото еды — так дневник будет честным.")
    if remaining["protein"] > user["norm_protein"] * 0.35:
        tips.append("Добавь белок: рыба, яйца, творог, курица, бобовые или тофу.")
    if stats["fat"] > user["norm_fat"]:
        tips.append("Жиры уже выше цели — следующий приём лучше сделать легче.")
    if remaining["calories"] < user["norm_calories"] * 0.15 and remaining["protein"] > 20:
        tips.append("Калорий осталось мало, но белок не добран — подойдёт творог или нежирная рыба.")
    if not tips:
        tips.append("День идёт ровно. Держи белок в каждом приёме пищи и не забывай воду.")

    return "💡 Рекомендации на сегодня\n\n" + "\n".join(f"• {tip}" for tip in tips)


def _command_args_text(text: str | None, command_name: str) -> str:
    if not text:
        return ""
    first, _, rest = text.strip().partition(" ")
    normalized = first.split("@", 1)[0].lstrip("/").casefold()
    if normalized == command_name:
        return rest.strip()
    return ""


def _recipe_score(recipe: dict, remaining: dict, goal: str, mode: str = "") -> int:
    calories_left = max(remaining["calories"], 1)
    calorie_score = max(0, 38 - round(abs(recipe["calories"] - calories_left) / calories_left * 38))

    protein_left = max(remaining["protein"], 0)
    if protein_left:
        protein_score = max(0, 24 - round(abs(recipe["protein"] - protein_left) / max(protein_left, 1) * 24))
    else:
        protein_score = 12 if recipe["protein"] <= 30 else 4

    fat_left = max(remaining["fat"], 0)
    fat_score = 14 if recipe["fat"] <= fat_left or fat_left == 0 else max(0, 14 - round((recipe["fat"] - fat_left) / max(fat_left, 1) * 14))

    carbs_left = max(remaining["carbs"], 0)
    carbs_score = 14 if recipe["carbs"] <= carbs_left or carbs_left == 0 else max(0, 14 - round((recipe["carbs"] - carbs_left) / max(carbs_left, 1) * 14))

    goal_score = 10 if goal in recipe["goals"] else 0
    mode_score = 8 if mode and mode in recipe.get("tags", set()) else 0
    return min(100, calorie_score + protein_score + fat_score + carbs_score + goal_score + mode_score)


def _recipe_mode_from_args(args: str) -> str:
    aliases = {
        "белок": "protein",
        "protein": "protein",
        "легко": "light",
        "light": "light",
        "ужин": "dinner",
        "dinner": "dinner",
        "завтрак": "breakfast",
        "breakfast": "breakfast",
        "углеводы": "carbs",
        "carbs": "carbs",
    }
    return aliases.get(args.casefold().strip(), "")


def _recipe_mode_label(mode: str) -> str:
    labels = {
        "protein": "больше белка",
        "light": "лёгкое блюдо",
        "dinner": "ужин",
        "breakfast": "завтрак",
        "carbs": "углеводы",
    }
    return labels.get(mode, mode)


def _format_recipe_suggestions(user: dict, remaining: dict, mode: str = "") -> str:
    scored = []
    for recipe in RECIPE_LIBRARY:
        score = _recipe_score(recipe, remaining, user["goal"], mode)
        scored.append((score, recipe))
    scored.sort(key=lambda item: item[0], reverse=True)

    lines = [
        "🍳 Рецепты под остаток КБЖУ\n",
        "Источник: внутренняя база Dietnik. Подбор идёт по твоему дневному остатку и цели.\n",
        f"Осталось: 🔥 {remaining['calories']} ккал · 🥩 {remaining['protein']} г · "
        f"🥑 {remaining['fat']} г · 🍚 {remaining['carbs']} г\n",
    ]

    if mode:
        lines.append(f"Фильтр: {_recipe_mode_label(mode)}.")
    lines.append("Можно выбрать: /recipes белок, /recipes легко, /recipes ужин, /recipes завтрак.")

    for score, recipe in scored[:3]:
        lines.append(
            "\n"
            f"{score}% · <b>{_safe(recipe['title'])}</b>\n"
            f"🔥 {recipe['calories']} ккал · 🥩 {recipe['protein']} г · "
            f"🥑 {recipe['fat']} г · 🍚 {recipe['carbs']} г\n"
            f"Состав: {_safe(', '.join(recipe['ingredients']))}\n"
            f"Как приготовить: {_safe(recipe['steps'])}\n"
            f"{_safe(recipe['note'])}"
        )

    return "\n".join(lines)


def _premium_required_text() -> str:
    return (
        "🌿 Эта функция входит в Premium.\n\n"
        "Premium открывает рецепты под остаток КБЖУ, отчёты и расширенного AI-диетолога.\n"
        f"Стоимость: {PREMIUM_PRICE_RUB} ₽ на 30 дней."
    )


def _support_needs_admin_by_keywords(text: str) -> bool:
    lowered = text.casefold()
    keywords = {
        "оплатил",
        "оплатила",
        "деньги списали",
        "списались деньги",
        "не активировалась",
        "не активировался",
        "возврат",
        "вернуть деньги",
        "чек",
        "ошибка оплаты",
        "не работает оплата",
        "удалить мои данные",
        "удалить аккаунт",
        "администратор",
        "оператор",
        "живой человек",
        "позовите человека",
    }
    return any(keyword in lowered for keyword in keywords)


def _format_support_admin_text(
    message: Message,
    user: dict | None,
    support_text: str,
    reason: str,
    ai_answer: str | None = None,
) -> str:
    username = f"@{message.from_user.username}" if message.from_user.username else "без username"
    plan = _subscription_name(user) if user else "анкета не завершена"
    text = (
        "💬 Обращение в поддержку Dietnik\n\n"
        f"Пользователь: {message.from_user.id} ({username})\n"
        f"Тариф: {plan}\n"
        f"Причина передачи: {reason or 'нужна проверка администратора'}\n\n"
        f"Сообщение пользователя:\n{support_text}\n\n"
    )
    if ai_answer:
        text += f"Ответ AI пользователю:\n{ai_answer}\n\n"
    return (
        text
        + "Как ответить:\n"
        "1. Нажми «Ответить» на это сообщение и напиши текст.\n"
        f"2. Или используй: /reply {message.from_user.id} текст ответа"
    )


async def _notify_support_admins(
    bot: Bot,
    message: Message,
    support_text: str,
    reason: str,
    ai_answer: str | None = None,
) -> bool:
    user = get_user(message.from_user.id)
    admin_text = _format_support_admin_text(
        message,
        user,
        support_text,
        reason,
        ai_answer,
    )
    recipients = (
        [SUPPORT_ADMIN_CHAT_ID]
        if SUPPORT_ADMIN_CHAT_ID
        else sorted(ADMIN_IDS)
    )
    sent_any = False
    for chat_id in recipients:
        try:
            sent = await bot.send_message(chat_id, admin_text, parse_mode=None)
            remember_support_admin_message(
                sent.chat.id,
                sent.message_id,
                message.from_user.id,
            )
            sent_any = True
        except Exception as exc:
            logger.warning("Support escalation failed for chat_id=%s: %s", chat_id, exc)
    return sent_any


async def _send_support_reply(
    bot: Bot,
    telegram_id: int,
    text: str,
) -> bool:
    try:
        await bot.send_message(
            telegram_id,
            f"💬 Ответ поддержки\n\n{text}",
            reply_markup=support_keyboard(),
            parse_mode=None,
        )
        log_support_message(telegram_id, "admin", text)
        set_support_status(telegram_id, "admin")
        return True
    except Exception as exc:
        logger.warning("Support reply failed for user_id=%s: %s", telegram_id, exc)
        return False


async def _begin_support(
    message: Message,
    state: FSMContext,
    bot: Bot,
    first_message: str | None = None,
) -> None:
    await state.clear()
    await state.set_state(SupportChat.active)
    ensure_support_thread(message.from_user.id)
    if first_message:
        await _process_support_text(message, state, bot, first_message)
        return
    await message.answer(
        "💬 Поддержка Dietnik\n\n"
        "Опиши проблему одним сообщением. Сначала попробую помочь я. "
        "Если потребуется проверить оплату, базу или логи, я передам обращение администратору.\n\n"
        "Можно также отправить скриншот или PDF-чек.",
        reply_markup=support_keyboard(),
        parse_mode=None,
    )


async def _process_support_text(
    message: Message,
    state: FSMContext,
    bot: Bot,
    support_text: str,
) -> None:
    support_text = support_text.strip()
    if not support_text:
        await message.answer(
            "Напиши вопрос текстом, и я постараюсь помочь.",
            reply_markup=support_keyboard(),
        )
        return
    if len(support_text) > SUPPORT_MESSAGE_MAX_CHARS:
        await message.answer(
            f"Сообщение слишком длинное. Сократи его до {SUPPORT_MESSAGE_MAX_CHARS} символов "
            "или приложи скриншот.",
            reply_markup=support_keyboard(),
        )
        return

    user_id = message.from_user.id
    existing_status = get_support_status(user_id)
    log_support_message(user_id, "user", support_text)
    if existing_status == "admin":
        sent = await _notify_support_admins(
            bot,
            message,
            support_text,
            "дополнение к открытому обращению",
        )
        await message.answer(
            "Добавил сообщение к обращению. Администратор увидит его и ответит здесь."
            if sent
            else f"Не получилось передать дополнение. Напиши {SUPPORT_USERNAME}.",
            reply_markup=support_keyboard(),
            parse_mode=None,
        )
        return

    keyword_escalation = _support_needs_admin_by_keywords(support_text)

    if not SUPPORT_AI_ENABLED:
        set_support_status(user_id, "admin")
        sent = await _notify_support_admins(
            bot,
            message,
            support_text,
            "AI-поддержка отключена",
        )
        await message.answer(
            "Передал вопрос администратору. Он ответит здесь в чате."
            if sent
            else f"Не получилось передать обращение автоматически. Напиши {SUPPORT_USERNAME}.",
            reply_markup=support_keyboard(),
            parse_mode=None,
        )
        return

    try:
        result = await ask_support_ai(
            support_text,
            get_user(user_id),
            get_recent_support_messages(user_id),
            keyword_escalation,
        )
    except Exception as exc:
        logger.exception("Support AI failed for user_id=%s", user_id)
        set_support_status(user_id, "admin")
        sent = await _notify_support_admins(
            bot,
            message,
            support_text,
            f"ошибка AI: {exc}",
        )
        await message.answer(
            "Я не смог уверенно ответить автоматически, поэтому передал вопрос администратору."
            if sent
            else f"AI-поддержка временно недоступна. Напиши {SUPPORT_USERNAME}.",
            reply_markup=support_keyboard(),
            parse_mode=None,
        )
        return

    answer = result["answer"]
    log_support_message(user_id, "ai", answer)
    await message.answer(answer, reply_markup=support_keyboard(), parse_mode=None)

    if result["escalate"]:
        set_support_status(user_id, "admin")
        sent = await _notify_support_admins(
            bot,
            message,
            support_text,
            result["reason"],
            ai_answer=answer,
        )
        await message.answer(
            "Я передал вопрос администратору. Он проверит детали и ответит здесь."
            if sent
            else f"Для ручной проверки напиши {SUPPORT_USERNAME}.",
            reply_markup=support_keyboard(),
            parse_mode=None,
        )
    else:
        set_support_status(user_id, "ai")


def _is_support_admin_chat(message: Message) -> bool:
    return bool(
        SUPPORT_ADMIN_CHAT_ID
        and message.chat.id == SUPPORT_ADMIN_CHAT_ID
    )


def _is_admin(message: Message | CallbackQuery) -> bool:
    user = message.from_user
    return bool(user and user.id in ADMIN_IDS)


async def _deny_admin(message: Message) -> None:
    await message.answer("⛔ Команда доступна только администратору.")


def _command_args(message: Message) -> str:
    if not message.text:
        return ""
    parts = message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _parse_user_id(value: str) -> int | None:
    value = value.strip()
    if value.startswith("@"):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _admin_help_text() -> str:
    return (
        "🛠 Админ-панель Dietnik\n\n"
        "Команды:\n"
        "/admin — это меню\n"
        "/admin_stats — статистика проекта\n"
        "/admin_users [страница] — список пользователей\n"
        "/admin_user <telegram_id> — карточка пользователя\n"
        "/admin_grant_basic <telegram_id> [дней] — выдать Basic\n"
        "/admin_grant_premium <telegram_id> [дней] — выдать Premium\n"
        "/admin_revoke_premium <telegram_id> — отключить платный доступ\n"
        "/admin_reset_day <telegram_id> — очистить дневник за сегодня\n"
        "/admin_payments [кол-во] — последние платежи\n"
        "/admin_message <telegram_id> <текст> — написать пользователю\n"
        "/admin_broadcast — рассылка всем пользователям\n"
        "/admin_cancel — отменить админ-действие\n"
        "/admin_health — диагностика конфига и БД\n"
        "/reply <telegram_id> <текст> — ответить от имени поддержки\n"
        "/supportchatid — показать ID группы поддержки\n\n"
        "Хранилище:\n"
        "/dbstatus — статус постоянной базы\n"
        "/storagecheck — проверка сохранности /app/data\n"
        "/backupdb — скачать текущую базу\n"
        "/restoredb — восстановить базу из .db файла\n\n"
        "Чтобы узнать свой ID, отправь /my_id."
    )


def _commands_text(is_admin: bool = False) -> str:
    text = (
        "📚 Команды Dietnik\n\n"
        "Основные:\n"
        "/start — пройти настройку заново\n"
        "/menu — главное меню\n"
        "/manual_food — добавить еду вручную\n"
        "/today — прогресс за сегодня\n"
        "/profile — профиль и дневная норма\n"
        "/recommendations — рекомендации на сегодня\n"
        "/subscription — тарифы и подписка\n"
        "/terms — условия подписки\n"
        "/paysupport — помощь с оплатой\n"
        "/support — открыть поддержку\n"
        "/version — версия запущенного бота\n"
        "/help — как пользоваться\n"
        "/commands — список команд\n\n"
        "Premium:\n"
        "/dietitian — AI-диетолог\n"
        "/recipes — рецепты под остаток КБЖУ\n"
        "/reports — отчёт за 7 дней\n"
        "/reports 30 — отчёт за 30 дней\n"
        "/reports all — отчёт за весь период\n\n"
        "Сервисные:\n"
        "/cancel — отменить текущий режим\n"
        "/reset_day — очистить сегодняшний дневник\n"
        "/my_id — узнать свой Telegram ID"
    )
    if is_admin:
        text += (
            "\n\nАдмин:\n"
            "/admin — меню админки\n"
            "/admin_stats — статистика проекта\n"
            "/admin_users [страница] — список пользователей\n"
            "/admin_user <telegram_id> — карточка пользователя\n"
            "/admin_grant_basic <telegram_id> [дней] — выдать Basic\n"
            "/admin_grant_premium <telegram_id> [дней] — выдать Premium\n"
            "/admin_revoke_premium <telegram_id> — отключить платный доступ\n"
            "/admin_reset_day <telegram_id> — очистить дневник пользователя\n"
            "/admin_payments [кол-во] — последние платежи\n"
            "/admin_message <telegram_id> <текст> — написать пользователю\n"
            "/admin_broadcast — рассылка всем пользователям\n"
            "/admin_cancel — отменить админ-действие\n"
            "/admin_health — диагностика\n"
            "/reply <telegram_id> <текст> — ответить пользователю\n"
            "/supportchatid — ID группы поддержки\n"
            "/dbstatus — статус постоянной базы\n"
            "/storagecheck — проверка /app/data\n"
            "/backupdb — скачать SQLite-базу\n"
            "/restoredb — восстановить SQLite-базу"
        )
    return text


def _normalize_command_text(text: str | None) -> str:
    if not text:
        return ""
    command = text.casefold().strip()
    command = command.split(maxsplit=1)[0] if " " in command and not command.startswith("/ ") else command
    command = command.replace(" ", "")
    command = command.split("@", 1)[0]
    return command.lstrip("/")


def _is_commands_request(text: str | None) -> bool:
    return _normalize_command_text(text) in {
        "commands",
        "command",
        "comands",
        "comand",
        "cmds",
        "cmnds",
        "команды",
        "команд",
        "список",
        "списоккоманд",
    }


def _storage_admin_command(text: str | None) -> str:
    command = _normalize_command_text(text)
    aliases = {
        "dbstatus": "dbstatus",
        "db_status": "dbstatus",
        "storagecheck": "storagecheck",
        "storage_check": "storagecheck",
        "backupdb": "backupdb",
        "backup_db": "backupdb",
        "restoredb": "restoredb",
        "restore_db": "restoredb",
    }
    return aliases.get(command, "")


def _is_support_chat_id_request(text: str | None) -> bool:
    return _normalize_command_text(text) in {
        "supportchatid",
        "support_chat_id",
        "supportid",
    }


async def _send_dashboard(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    if not _has_active_subscription(user):
        trial_available = _trial_available(user)
        text = (
            "🎁 Твой пробный анализ готов к запуску\n\n"
            "Нажми «Попробовать бесплатно» и отправь фото блюда. "
            "Dietnik распознает еду и покажет калорийность.\n\n"
            "Пробный анализ доступен один раз и не добавляется в дневник."
            if trial_available
            else _locked_text(user)
        )
        await message.answer(
            text,
            reply_markup=(
                trial_keyboard(True)
                if trial_available
                else _subscription_markup(user)
            ),
        )
        return

    stats = get_today_stats(message.from_user.id)
    await message.answer(
        _format_dashboard(user, stats, message.from_user.first_name),
        reply_markup=main_menu_keyboard(),
    )


async def _send_db_backup(bot: Bot, chat_id: int, caption: str) -> bool:
    db_path = Path(DB_PATH)
    if not db_path.exists():
        await bot.send_message(chat_id, f"База не найдена: {db_path}")
        return False
    await bot.send_document(
        chat_id=chat_id,
        document=FSInputFile(db_path),
        caption=caption,
    )
    return True


async def _maybe_auto_backup_db(bot: Bot, reason: str) -> None:
    if AUTO_DB_BACKUP_INTERVAL_HOURS <= 0 or not ADMIN_IDS:
        return

    now = datetime.now()
    last_raw = get_app_state("last_auto_db_backup_at")
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if now - last < timedelta(hours=AUTO_DB_BACKUP_INTERVAL_HOURS):
                return
        except ValueError:
            pass

    for admin_id in ADMIN_IDS:
        try:
            await _send_db_backup(
                bot,
                admin_id,
                f"Автобэкап базы Dietnik\nПричина: {reason}\nВремя: {now.isoformat(timespec='seconds')}",
            )
        except Exception as exc:
            logger.warning("Auto DB backup failed for admin_id=%s: %s", admin_id, exc)

    set_app_state("last_auto_db_backup_at", now.isoformat(timespec="seconds"))


def _parse_int(text: str) -> int | None:
    try:
        value = int(text.strip())
    except (AttributeError, ValueError):
        return None
    return value if value > 0 else None


def _parse_non_negative_int(text: str) -> int | None:
    try:
        value = int(text.strip())
    except (AttributeError, ValueError):
        return None
    return value if value >= 0 else None


def _parse_float(text: str) -> float | None:
    try:
        value = float(text.strip().replace(",", "."))
    except (AttributeError, ValueError):
        return None
    return value if value > 0 else None


@router.message(lambda message: _is_commands_request(message.text))
async def commands_handler(message: Message) -> None:
    """Send command list as a regular chat message."""
    logger.info("Received commands request from user_id=%s text=%r", message.from_user.id, message.text)
    await message.answer(
        _commands_text(_is_admin(message)),
        reply_markup=main_menu_keyboard(),
        parse_mode=None,
    )


@router.message(lambda message: bool(_storage_admin_command(message.text)))
async def storage_admin_router(message: Message, state: FSMContext, bot: Bot) -> None:
    """Handle storage admin commands before FSM state handlers can intercept them."""
    command = _storage_admin_command(message.text)
    logger.info(
        "Received storage admin command=%s from user_id=%s text=%r",
        command,
        message.from_user.id,
        message.text,
    )
    if command == "dbstatus":
        await dbstatus_handler(message)
    elif command == "storagecheck":
        await storagecheck_handler(message)
    elif command == "backupdb":
        await backupdb_handler(message, bot)
    elif command == "restoredb":
        await restoredb_handler(message, state)


@router.message(lambda message: _is_support_chat_id_request(message.text))
async def support_chat_id_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return
    await message.answer(
        "💬 ID этого чата для поддержки:\n"
        f"<code>{message.chat.id}</code>\n\n"
        "Добавь в BotHost:\n"
        f"<code>SUPPORT_ADMIN_CHAT_ID={message.chat.id}</code>\n"
        "Затем сделай редеплой."
    )


@router.message(Command("version"))
async def version_handler(message: Message) -> None:
    await message.answer(
        f"Сборка Dietnik: <code>{BOT_RELEASE}</code>\n"
        f"Чат поддержки: <code>{SUPPORT_ADMIN_CHAT_ID or 'не настроен'}</code>"
    )


@router.message(Command("reply"))
async def support_reply_command_handler(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return
    args = _command_args(message).split(maxsplit=1)
    telegram_id = _parse_user_id(args[0]) if args else None
    reply_text = args[1].strip() if len(args) > 1 else ""
    if not telegram_id or not reply_text:
        await message.answer(
            "Формат: /reply <telegram_id> <текст ответа>",
            parse_mode=None,
        )
        return
    sent = await _send_support_reply(bot, telegram_id, reply_text)
    await message.answer(
        "✅ Ответ отправлен пользователю."
        if sent
        else "⚠️ Не получилось отправить ответ пользователю."
    )


@router.message(
    lambda message: (
        _is_support_admin_chat(message)
        and not (message.text or "").startswith("/")
    )
)
async def support_group_reply_handler(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        return
    if not message.reply_to_message:
        await message.answer(
            "Нажми «Ответить» именно на обращение пользователя "
            "или используй /reply user_id текст.",
            parse_mode=None,
        )
        return
    telegram_id = get_support_user_by_admin_message(
        message.chat.id,
        message.reply_to_message.message_id,
    )
    if not telegram_id:
        await message.answer(
            "Не нашёл пользователя для этого сообщения. "
            "Ответь на исходное обращение или используй /reply.",
            parse_mode=None,
        )
        return
    reply_text = (message.text or "").strip()
    if not reply_text:
        await message.answer("Пока поддерживаются текстовые ответы администратора.")
        return
    sent = await _send_support_reply(bot, telegram_id, reply_text)
    await message.answer(
        "✅ Ответ отправлен пользователю."
        if sent
        else "⚠️ Не получилось отправить ответ."
    )


@router.message(Command("support"))
async def support_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    await _begin_support(
        message,
        state,
        bot,
        first_message=_command_args(message) or None,
    )


@router.message(
    SupportChat.active,
    F.text.in_({"✅ Завершить поддержку", "Завершить поддержку"}),
)
async def close_support_handler(message: Message, state: FSMContext) -> None:
    set_support_status(message.from_user.id, "closed")
    await state.clear()
    await message.answer(
        "✅ Диалог с поддержкой завершён. Если понадобится помощь, нажми «💬 Поддержка».",
        reply_markup=main_menu_keyboard(),
    )


@router.message(SupportChat.active, Command("close_support"))
async def close_support_command_handler(message: Message, state: FSMContext) -> None:
    await close_support_handler(message, state)


@router.message(SupportChat.active, F.photo | F.document)
async def support_attachment_handler(message: Message, bot: Bot) -> None:
    file_size = 0
    allowed = False
    attachment_name = "скриншот"
    if message.photo:
        file_size = message.photo[-1].file_size or 0
        allowed = True
    elif message.document:
        file_size = message.document.file_size or 0
        file_name = (message.document.file_name or "").casefold()
        mime_type = (message.document.mime_type or "").casefold()
        allowed = (
            mime_type.startswith(("image/", "text/"))
            or mime_type == "application/pdf"
            or file_name.endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt"))
        )
        attachment_name = message.document.file_name or "документ"

    if not allowed:
        await message.answer(
            "Можно отправить фото, скриншот, PDF-чек или текстовый файл.",
            reply_markup=support_keyboard(),
        )
        return
    if file_size > SUPPORT_ATTACHMENT_MAX_FILE_BYTES:
        max_mb = round(SUPPORT_ATTACHMENT_MAX_FILE_BYTES / 1024 / 1024)
        await message.answer(
            f"Файл слишком большой. Максимальный размер: {max_mb} МБ.",
            reply_markup=support_keyboard(),
        )
        return

    note = f"[вложение: {attachment_name}] {(message.caption or '').strip()}".strip()
    log_support_message(message.from_user.id, "user", note)
    set_support_status(message.from_user.id, "admin")

    recipients = [SUPPORT_ADMIN_CHAT_ID] if SUPPORT_ADMIN_CHAT_ID else sorted(ADMIN_IDS)
    sent_any = False
    for chat_id in recipients:
        try:
            forwarded = await bot.forward_message(
                chat_id,
                message.chat.id,
                message.message_id,
            )
            remember_support_admin_message(
                forwarded.chat.id,
                forwarded.message_id,
                message.from_user.id,
            )
            info = await bot.send_message(
                chat_id,
                "📎 Вложение в поддержку Dietnik\n\n"
                f"Пользователь: {message.from_user.id}"
                f"{' (@' + message.from_user.username + ')' if message.from_user.username else ''}\n"
                f"Комментарий: {message.caption or 'без подписи'}\n\n"
                "Ответь reply на это сообщение или на вложение.",
                parse_mode=None,
            )
            remember_support_admin_message(
                info.chat.id,
                info.message_id,
                message.from_user.id,
            )
            sent_any = True
        except Exception as exc:
            logger.warning("Support attachment forwarding failed: %s", exc)

    await message.answer(
        "Скриншот передан администратору. Он ответит здесь."
        if sent_any
        else f"Не получилось передать файл автоматически. Напиши {SUPPORT_USERNAME}.",
        reply_markup=support_keyboard(),
        parse_mode=None,
    )


@router.message(SupportChat.active, F.text & ~F.text.startswith("/"))
async def support_text_handler(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    await _process_support_text(message, state, bot, message.text or "")


@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext) -> None:
    logger.info("Received /start from user_id=%s", message.from_user.id)
    await state.clear()
    await message.answer(WELCOME_TEXT)
    await message.answer("Начнём настройку. Укажи пол:", reply_markup=gender_keyboard())
    await state.set_state(Onboarding.gender)
    logger.info("Sent onboarding start to user_id=%s", message.from_user.id)


@router.message(Command("cancel", "отмена"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    if get_user(message.from_user.id):
        await message.answer("Ок, текущий режим отменён.", reply_markup=main_menu_keyboard())
        await _send_dashboard(message)
    else:
        await message.answer("Ок, отменил. Чтобы начать настройку, отправь /start.")


@router.message(F.text.casefold().in_({"cancel", "отмена"}))
async def plain_cancel_handler(message: Message, state: FSMContext) -> None:
    await cancel_handler(message, state)


@router.message(Onboarding.gender)
async def gender_handler(message: Message, state: FSMContext) -> None:
    if message.text not in {"Мужской", "Женский"}:
        await message.answer("Выбери пол кнопкой ниже.", reply_markup=gender_keyboard())
        return

    await state.update_data(gender=message.text)
    await message.answer("Сколько тебе лет?", reply_markup=remove_keyboard())
    await state.set_state(Onboarding.age)


@router.message(Onboarding.age)
async def age_handler(message: Message, state: FSMContext) -> None:
    age = _parse_int(message.text)
    if not age or age < 10 or age > 120:
        await message.answer("Введи возраст числом, например: 29")
        return

    await state.update_data(age=age)
    await message.answer("Укажи рост в сантиметрах, например: 175")
    await state.set_state(Onboarding.height)


@router.message(Onboarding.height)
async def height_handler(message: Message, state: FSMContext) -> None:
    height = _parse_int(message.text)
    if not height or height < 80 or height > 250:
        await message.answer("Введи рост в сантиметрах, например: 175")
        return

    await state.update_data(height=height)
    await message.answer("Укажи вес в кг, например: 72.5")
    await state.set_state(Onboarding.weight)


@router.message(Onboarding.weight)
async def weight_handler(message: Message, state: FSMContext) -> None:
    weight = _parse_float(message.text)
    if not weight or weight < 25 or weight > 300:
        await message.answer("Введи вес в кг, например: 72.5")
        return

    await state.update_data(weight=weight)
    await message.answer("Какая у тебя активность?", reply_markup=activity_keyboard())
    await state.set_state(Onboarding.activity)


@router.message(Onboarding.activity)
async def activity_handler(message: Message, state: FSMContext) -> None:
    if message.text not in {"Низкая", "Средняя", "Высокая"}:
        await message.answer("Выбери активность кнопкой ниже.", reply_markup=activity_keyboard())
        return

    await state.update_data(activity=message.text)
    await message.answer("Какая цель?", reply_markup=goal_keyboard())
    await state.set_state(Onboarding.goal)


@router.message(Onboarding.goal)
async def goal_handler(message: Message, state: FSMContext) -> None:
    if message.text not in {"Похудение", "Поддержание", "Набор массы"}:
        await message.answer("Выбери цель кнопкой ниже.", reply_markup=goal_keyboard())
        return

    data = await state.get_data()
    data["goal"] = message.text
    norm = calculate_norm(
        gender=data["gender"],
        age=data["age"],
        height=data["height"],
        weight=data["weight"],
        activity=data["activity"],
        goal=data["goal"],
    )

    save_user(
        telegram_id=message.from_user.id,
        gender=data["gender"],
        age=data["age"],
        height=data["height"],
        weight=data["weight"],
        activity=data["activity"],
        goal=data["goal"],
        norm_calories=norm["calories"],
        norm_protein=norm["protein"],
        norm_fat=norm["fat"],
        norm_carbs=norm["carbs"],
    )
    await state.clear()

    saved_user = get_user(message.from_user.id) or {}
    has_access = _has_active_subscription(saved_user)
    await message.answer(
        "✅ Настройка завершена!\n\n"
        f"🎯 Твоя цель: {data['goal']}\n"
        "📌 Дневная норма:\n\n"
        f"🔥 Калории: {norm['calories']} ккал\n"
        f"🥩 Белки: {norm['protein']} г\n"
        f"🥑 Жиры: {norm['fat']} г\n"
        f"🍚 Углеводы: {norm['carbs']} г\n\n"
        + (
            "Теперь просто отправь фото еды 🍽"
            if has_access
            else "Теперь попробуй Dietnik бесплатно: нажми кнопку ниже и отправь одно фото блюда."
        ),
        reply_markup=main_menu_keyboard() if has_access else trial_keyboard(True),
    )
    await _send_dashboard(message)


@router.message(Command("profile"))
async def profile_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return

    await message.answer(
        "👤 Твой профиль\n\n"
        f"🎯 Цель: {user['goal']}\n"
        f"Возраст: {user['age']}\n"
        f"Рост: {user['height']} см\n"
        f"Вес: {user['weight']} кг\n"
        f"Активность: {user['activity']}\n\n"
        f"💳 Тариф: {_subscription_name(user)}\n\n"
        "📌 Дневная норма:\n"
        f"{_format_norm(user)}",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("today"))
async def today_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not await _require_subscription(message, user):
        return

    stats = get_today_stats(message.from_user.id)
    await message.answer(_format_progress(user, stats), reply_markup=main_menu_keyboard())


@router.message(Command("reset_day"))
async def reset_day_handler(message: Message) -> None:
    if not await _require_subscription(message):
        return
    reset_today(message.from_user.id)
    await message.answer("✅ Сегодняшние приёмы пищи удалены.", reply_markup=main_menu_keyboard())


@router.message(Command("manual_food", "add_manual"))
async def manual_food_handler(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if not await _require_subscription(message, user):
        return

    await state.set_state(ManualMeal.dish)
    await message.answer(
        "✍️ Добавим приём пищи вручную.\n\n"
        "Напиши название блюда, например: гречка с курицей.\n"
        "Отмена: /cancel",
        reply_markup=main_menu_keyboard(),
    )


@router.message(
    F.text.casefold().in_(
        {
            "добавить вручную",
            "вручную",
            "ручной ввод",
            "manual_food",
            "add_manual",
        }
    )
)
async def plain_manual_food_handler(message: Message, state: FSMContext) -> None:
    await manual_food_handler(message, state)


@router.message(ManualMeal.dish)
async def manual_meal_dish_handler(message: Message, state: FSMContext) -> None:
    dish = (message.text or "").strip()
    if len(dish) < 2:
        await message.answer("Напиши название блюда, например: омлет с овощами.")
        return

    await state.update_data(dish=dish[:100])
    await state.set_state(ManualMeal.calories)
    await message.answer("Сколько калорий? Введи число, например: 450")


@router.message(ManualMeal.calories)
async def manual_meal_calories_handler(message: Message, state: FSMContext) -> None:
    calories = _parse_int(message.text or "")
    if not calories or calories > 5000:
        await message.answer("Введи калории числом, например: 450")
        return

    await state.update_data(calories=calories)
    await state.set_state(ManualMeal.protein)
    await message.answer("Сколько белков в граммах? Например: 35")


@router.message(ManualMeal.protein)
async def manual_meal_protein_handler(message: Message, state: FSMContext) -> None:
    protein = _parse_non_negative_int(message.text or "")
    if protein is None or protein > 500:
        await message.answer("Введи белки числом в граммах, например: 35")
        return

    await state.update_data(protein=protein)
    await state.set_state(ManualMeal.fat)
    await message.answer("Сколько жиров в граммах? Например: 18")


@router.message(ManualMeal.fat)
async def manual_meal_fat_handler(message: Message, state: FSMContext) -> None:
    fat = _parse_non_negative_int(message.text or "")
    if fat is None or fat > 500:
        await message.answer("Введи жиры числом в граммах, например: 18")
        return

    await state.update_data(fat=fat)
    await state.set_state(ManualMeal.carbs)
    await message.answer("Сколько углеводов в граммах? Например: 55")


@router.message(ManualMeal.carbs)
async def manual_meal_carbs_handler(message: Message, state: FSMContext) -> None:
    carbs = _parse_non_negative_int(message.text or "")
    if carbs is None or carbs > 1000:
        await message.answer("Введи углеводы числом в граммах, например: 55")
        return

    data = await state.get_data()
    await state.clear()
    user = get_user(message.from_user.id)
    if not user or not _has_active_subscription(user):
        if user:
            await message.answer(
                _locked_text(user),
                reply_markup=_subscription_markup(user),
            )
        else:
            await message.answer("Сначала пройди настройку через /start.")
        return

    recommendation = "Добавлено вручную. Для максимальной точности сверяй порцию с весами."
    save_meal(
        telegram_id=message.from_user.id,
        dish_name=data["dish"],
        calories=data["calories"],
        protein=data["protein"],
        fat=data["fat"],
        carbs=carbs,
        recommendation=recommendation,
    )

    stats = get_today_stats(message.from_user.id)
    progress = _format_progress(user, stats)
    await message.answer(
        "✅ Приём пищи добавлен вручную\n\n"
        f"🍽 Блюдо: {_safe(data['dish'])}\n"
        f"🔥 Калории: {data['calories']} ккал\n"
        f"🥩 Белки: {data['protein']} г\n"
        f"🥑 Жиры: {data['fat']} г\n"
        f"🍚 Углеводы: {carbs} г\n"
        f"💡 Рекомендация: {_safe(recommendation)}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{progress}",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@router.message(Command("my_id", "myid", "id", "admin_id"))
async def my_id_handler(message: Message) -> None:
    await message.answer(
        f"Твой Telegram ID:\n<code>{message.from_user.id}</code>\n\n"
        "Добавь его в Bothost в переменную ADMIN_IDS, чтобы открыть админ-команды."
    )


@router.message(Command("menu"))
async def menu_handler(message: Message) -> None:
    await _send_dashboard(message)


@router.message(Command("subscription"))
async def subscription_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    await message.answer(
        _format_subscription(user),
        reply_markup=_subscription_markup(user),
    )


@router.message(Command("terms"))
async def terms_handler(message: Message) -> None:
    await message.answer(
        "📄 Условия подписки\n\n"
        f"Basic: {BASIC_PRICE_RUB} ₽ за 30 дней.\n"
        f"Premium: {PREMIUM_PRICE_RUB} ₽ за 30 дней.\n\n"
        "Basic открывает фото-учёт, дневник КБЖУ и рекомендации.\n"
        "Premium дополнительно открывает AI-диетолога, рецепты и отчёты.\n"
        "После оплаты доступ активируется автоматически.\n\n"
        "Платёж является разовым и не продлевается автоматически.\n\n"
        "Dietnik помогает вести дневник питания, но не заменяет врача. "
        "Расчёты по фото являются оценкой и зависят от размера порции.\n\n"
        f"Вопросы по оплате: /paysupport или {SUPPORT_USERNAME}",
        parse_mode=None,
    )


@router.message(Command("paysupport"))
async def payment_support_handler(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    await _begin_support(message, state, bot)
    await message.answer(
        "Если вопрос об оплате, напиши дату, сумму и что именно произошло. "
        "При необходимости приложи квитанцию ЮKassa.",
        reply_markup=support_keyboard(),
        parse_mode=None,
    )


@router.message(Command("recommendations"))
async def recommendations_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not await _require_subscription(message, user):
        return
    stats = get_today_stats(message.from_user.id)
    await message.answer(
        _format_recommendations(user, stats),
        reply_markup=main_menu_keyboard(),
    )


async def _send_report(
    message: Message,
    period: str,
    telegram_id: int | None = None,
) -> None:
    user_id = telegram_id or message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    if not _is_premium(user):
        await message.answer(_premium_required_text(), reply_markup=_subscription_markup(user))
        return

    periods = {
        "7": (7, "последние 7 дней"),
        "30": (30, "последние 30 дней"),
        "all": (None, "весь период"),
    }
    days, label = periods.get(period, periods["7"])
    rows = get_period_stats(user_id, days)
    await message.answer(
        _format_report(user, rows, label, expected_days=days),
        reply_markup=reports_keyboard(),
    )


@router.message(Command("reports"))
async def reports_handler(message: Message) -> None:
    arg = _command_args(message).casefold()
    aliases = {
        "неделя": "7",
        "week": "7",
        "7": "7",
        "месяц": "30",
        "month": "30",
        "30": "30",
        "все": "all",
        "весь": "all",
        "all": "all",
    }
    await _send_report(message, aliases.get(arg, "7"))


@router.callback_query(F.data.in_({"report_7", "report_30", "report_all"}))
async def report_period_callback(callback: CallbackQuery) -> None:
    period = callback.data.removeprefix("report_")
    await _send_report(callback.message, period, callback.from_user.id)
    await callback.answer()


@router.message(Command("recipes"))
async def recipes_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    if not _is_premium(user):
        await message.answer(_premium_required_text(), reply_markup=_subscription_markup(user))
        return

    stats = get_today_stats(message.from_user.id)
    remaining = calculate_remaining(user, stats)
    mode = _recipe_mode_from_args(_command_args(message))
    await message.answer(
        _format_recipe_suggestions(user, remaining, mode),
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("dietitian"))
async def dietitian_handler(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    if not _is_premium(user):
        await message.answer(
            "🤖 AI-диетолог входит в Premium.\n\n"
            "В Basic доступны фото-учёт, дневник и короткие рекомендации.",
            reply_markup=_subscription_markup(user),
        )
        return
    await message.answer("🤖 Напиши вопрос диетологу одним сообщением.")
    await state.set_state(Consultation.question)


@router.message(Consultation.question)
async def dietitian_question_handler(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if not user or not _is_premium(user):
        await state.clear()
        if user:
            await message.answer(
                _premium_required_text(),
                reply_markup=_subscription_markup(user),
            )
        else:
            await message.answer("Сначала пройди настройку через /start.")
        return
    await message.answer("Думаю над ответом...")
    stats = get_today_stats(message.from_user.id)
    answer = await ask_dietitian(message.text or "", user, stats)
    await state.clear()
    await message.answer(f"🤖 Диетолог\n\n{answer}", reply_markup=main_menu_keyboard(), parse_mode=None)


@router.callback_query(F.data == "payment_terms")
async def payment_terms_callback(callback: CallbackQuery) -> None:
    await terms_handler(callback.message)
    await callback.answer()


@router.callback_query(F.data == "payment_support")
async def payment_support_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await state.set_state(SupportChat.active)
    ensure_support_thread(callback.from_user.id)
    await callback.message.answer(
        "💬 Поддержка Dietnik\n\n"
        "Опиши, что произошло с оплатой. Укажи дату и сумму. "
        "Можно приложить скриншот или PDF-квитанцию.",
        reply_markup=support_keyboard(),
        parse_mode=None,
    )
    await callback.answer()


@router.callback_query(F.data == "start_food_trial")
async def start_food_trial_callback(callback: CallbackQuery) -> None:
    user = get_user(callback.from_user.id)
    if not user:
        await callback.message.answer("Сначала пройди анкету через /start.")
    elif _trial_available(user):
        await callback.message.answer(
            "📸 Пришли одно фото блюда.\n\n"
            "Снимай сверху или под небольшим углом, при хорошем свете. "
            "Я покажу, что на фото и сколько в блюде калорий.",
            parse_mode=None,
        )
    elif _has_active_subscription(user):
        await callback.message.answer(
            "У тебя уже есть активная подписка. Просто отправь фото еды."
        )
    else:
        await callback.message.answer(
            "Пробный анализ уже использован. Выбери подписку, чтобы продолжить.",
            reply_markup=_subscription_markup(user),
        )
    await callback.answer()


@router.callback_query(F.data == "show_subscriptions")
async def show_subscriptions_callback(callback: CallbackQuery) -> None:
    user = get_user(callback.from_user.id)
    if not user:
        await callback.message.answer("Сначала пройди анкету через /start.")
    else:
        await callback.message.answer(
            _format_subscription(user),
            reply_markup=_subscription_markup(user),
        )
    await callback.answer()


@router.callback_query(F.data == "payment_unavailable")
async def payment_unavailable_callback(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "Оплата через ЮKassa ещё не подключена на сервере.\n\n"
        "Администратору нужно добавить PAYMENT_PROVIDER_TOKEN в BotHost и сделать редеплой.",
        parse_mode=None,
    )
    await callback.answer()


@router.callback_query(F.data.in_({"buy_basic", "buy_premium"}))
async def buy_subscription_handler(callback: CallbackQuery, bot: Bot) -> None:
    if not PAYMENT_PROVIDER_TOKEN:
        await payment_unavailable_callback(callback)
        return

    plan = callback.data.removeprefix("buy_")
    offer = SUBSCRIPTION_OFFERS[plan]
    user = get_user(callback.from_user.id)
    if plan == "basic" and user and _is_premium(user):
        await callback.answer("У тебя уже активен Premium", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=offer["title"],
        description=offer["description"],
        payload=offer["payload"],
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=[
            LabeledPrice(
                label=offer["title"],
                amount=offer["price"] * 100,
            )
        ],
        start_parameter=f"dietnik-{plan}-30",
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    offer = next(
        (
            value
            for value in SUBSCRIPTION_OFFERS.values()
            if value["payload"] == pre_checkout_query.invoice_payload
        ),
        None,
    )
    valid = bool(
        offer
        and pre_checkout_query.currency == "RUB"
        and pre_checkout_query.total_amount == offer["price"] * 100
    )
    if valid:
        await pre_checkout_query.answer(ok=True)
        return
    await pre_checkout_query.answer(
        ok=False,
        error_message="Счёт устарел или содержит неверную сумму. Открой раздел «Подписка» ещё раз.",
    )


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, bot: Bot) -> None:
    payment = message.successful_payment
    plan_and_offer = next(
        (
            (plan, offer)
            for plan, offer in SUBSCRIPTION_OFFERS.items()
            if offer["payload"] == payment.invoice_payload
        ),
        None,
    )
    if (
        not plan_and_offer
        or payment.currency != "RUB"
        or payment.total_amount != plan_and_offer[1]["price"] * 100
    ):
        logger.error(
            "Rejected unexpected successful payment user_id=%s payload=%r currency=%s amount=%s",
            message.from_user.id,
            payment.invoice_payload,
            payment.currency,
            payment.total_amount,
        )
        await message.answer(
            f"Платёж получен, но его параметры не совпали с тарифом. Напиши /paysupport. "
            f"ID операции: {payment.telegram_payment_charge_id}",
            parse_mode=None,
        )
        return

    plan, offer = plan_and_offer
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start, затем напиши /paysupport.")
        return

    start_date = date.today()
    current_until = user.get("subscription_until") or user.get("premium_until")
    if current_until:
        try:
            parsed_until = date.fromisoformat(current_until)
            if parsed_until > start_date:
                start_date = parsed_until
        except ValueError:
            pass
    subscription_until = (start_date + timedelta(days=MONTH_DAYS)).isoformat()

    inserted = activate_subscription_payment(
        telegram_id=message.from_user.id,
        plan=plan,
        amount=payment.total_amount,
        currency=payment.currency,
        provider_payment_charge_id=payment.provider_payment_charge_id or "",
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
        invoice_payload=payment.invoice_payload,
        subscription_until=subscription_until,
        is_recurring=bool(getattr(payment, "is_recurring", False)),
    )
    if not inserted:
        await message.answer(
            "✅ Этот платёж уже обработан. Подписка остаётся активной.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.answer(
        "✅ Оплата прошла успешно!\n\n"
        f"{offer['title']} активирован до {subscription_until}.\n"
        "Спасибо, что поддерживаешь Dietnik.",
        reply_markup=main_menu_keyboard(),
    )
    await _maybe_auto_backup_db(bot, "successful_payment")


@router.message(Command("admin", "admin_help"))
async def admin_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return
    await message.answer(_admin_help_text(), parse_mode=None)


@router.message(Command("admin_health"))
async def admin_health_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    stats = get_admin_stats()
    await message.answer(
        "🩺 Диагностика\n\n"
        f"ADMIN_IDS настроены: {'да' if ADMIN_IDS else 'нет'}\n"
        f"OpenAI ключ: {'есть' if OPENAI_API_KEY else 'нет'}\n"
        f"Оплата ЮKassa: {'подключена' if PAYMENT_PROVIDER_TOKEN else 'не подключена'}\n"
        f"Цена Basic: {BASIC_PRICE_RUB} RUB\n"
        f"Цена Premium: {PREMIUM_PRICE_RUB} RUB\n"
        f"AI-поддержка: {'включена' if SUPPORT_AI_ENABLED else 'выключена'}\n"
        f"Чат поддержки: {SUPPORT_ADMIN_CHAT_ID or 'не задан'}\n"
        f"DATA_DIR: {DATA_DIR}\n"
        f"DB_PATH: {DB_PATH}\n"
        f"PERSISTENCE_PATH: {PERSISTENCE_PATH}\n"
        f"Пользователей в БД: {stats['users_count']}\n"
        f"Открытых обращений: {stats['open_support_threads']}\n"
        f"Ждут администратора: {stats['escalated_support_threads']}\n"
        f"Приёмов пищи в БД: {stats['meals_count']}"
    )


@router.message(Command("dbstatus"))
async def dbstatus_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    status = get_db_status()
    size_kb = round(status["db_size"] / 1024, 2)
    payment_totals = _format_payment_totals(status["payment_totals"])
    await message.answer(
        "🗄 Статус базы\n\n"
        f"Путь базы: {status['db_path']}\n"
        f"Файл существует: {'да' if status['db_exists'] else 'нет'}\n"
        f"Размер: {status['db_size']} байт ({size_kb} KB)\n"
        f"Пользователей: {status['users_count']}\n"
        f"Платежей/заказов: {status['payments_count']}\n"
        f"Куплено единиц по валютам: {payment_totals}\n"
        f"Пробный режим: {status['trial_users']}\n"
        f"Basic пользователей: {status['basic_users']}\n"
        f"Premium пользователей: {status['premium_users']}\n"
        f"Открытых обращений: {status['open_support_threads']}\n"
        f"Ждут администратора: {status['escalated_support_threads']}\n"
        f"База лежит в /app/data: {'да' if status['in_app_data'] else 'нет'}\n"
        f"База лежит в DATA_DIR: {'да' if status['in_data_dir'] else 'нет'}\n"
        f"DB_PATH задан явно: {'да' if status['db_path_explicit'] else 'нет'}\n"
        f"DATA_DIR: {DATA_DIR}\n"
        f"PERSISTENCE_PATH: {PERSISTENCE_PATH}",
        parse_mode=None,
    )


@router.message(Command("storagecheck"))
async def storagecheck_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    status = get_storage_probe_status()
    if status["created_db_token"]:
        verdict = "Создан новый DB-токен. Запусти команду ещё раз после redeploy."
    elif not status["file_exists_before"]:
        verdict = "Файл проверки отсутствовал и был создан заново. Проверь после redeploy."
    elif not status["file_matches"]:
        verdict = "Токен в файле отличался от токена в БД. Хранилище надо проверить."
    else:
        verdict = "OK: DB-токен и файл совпадают."

    await message.answer(
        "🧪 Проверка постоянного хранилища\n\n"
        f"Вердикт: {verdict}\n"
        f"DB token: {status['db_token']}\n"
        f"File token: {status['file_token'] or '-'}\n"
        f"Файл был на месте: {'да' if status['file_exists_before'] else 'нет'}\n"
        f"Путь файла: {status['probe_path']}\n\n"
        "После redeploy команда должна показать тот же DB token и совпадение файла.",
        parse_mode=None,
    )


@router.message(Command("backupdb"))
async def backupdb_handler(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    await _send_db_backup(
        bot,
        message.chat.id,
        f"Ручной бэкап базы Dietnik\nDB_PATH: {DB_PATH}\nВремя: {datetime.now().isoformat(timespec='seconds')}",
    )


@router.message(Command("restoredb"))
async def restoredb_handler(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    await state.set_state(AdminPanel.restore_db_file)
    await message.answer(
        "♻️ Пришли SQLite-файл .db одним документом.\n\n"
        "Перед заменой текущая база будет сохранена в /app/data/backups.\n"
        "Отмена: /admin_cancel",
        parse_mode=None,
    )


@router.message(Command("admin_stats"))
async def admin_stats_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    stats = get_admin_stats()
    payment_totals = _format_payment_totals(stats["payment_totals"])
    await message.answer(
        "📊 Статистика Dietnik\n\n"
        f"Пользователи: {stats['users_count']}\n"
        f"Пробный режим: {stats['trial_users']}\n"
        f"Basic: {stats['basic_users']}\n"
        f"Premium: {stats['premium_users']}\n"
        f"Открытых обращений: {stats['open_support_threads']}\n"
        f"Ждут администратора: {stats['escalated_support_threads']}\n"
        f"Активны сегодня: {stats['active_today']}\n"
        f"Приёмов пищи сегодня: {stats['meals_today']}\n"
        f"Приёмов пищи всего: {stats['meals_count']}\n"
        f"Платежей: {stats['payments_count']}\n"
        f"Оплаты: {payment_totals}"
    )


@router.message(Command("admin_users"))
async def admin_users_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    page = _parse_user_id(_command_args(message)) or 1
    page = max(1, page)
    limit = 10
    users = get_users_page(limit=limit, offset=(page - 1) * limit)
    if not users:
        await message.answer("Пользователи не найдены.")
        return

    lines = [f"👥 Пользователи, страница {page}\n"]
    for user in users:
        plan = user.get("subscription_plan") or "basic"
        lines.append(
            f"<code>{user['telegram_id']}</code> · {plan} · "
            f"{user.get('goal') or 'без цели'} · {user.get('created_at') or '-'}"
        )
    lines.append("\nКарточка: /admin_user telegram_id")
    await message.answer("\n".join(lines))


@router.message(Command("admin_user"))
async def admin_user_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    telegram_id = _parse_user_id(_command_args(message))
    if not telegram_id:
        await message.answer("Формат: /admin_user <telegram_id>", parse_mode=None)
        return

    user = get_user(telegram_id)
    if not user:
        await message.answer("Пользователь не найден.")
        return

    stats = get_today_stats(telegram_id)
    meals = get_user_meals(telegram_id, limit=5)
    meals_text = "\n".join(
        f"• {meal['date']} · {escape(meal['dish_name'])} · {meal['calories']} ккал"
        for meal in meals
    )
    if not meals_text:
        meals_text = "Нет записей."

    await message.answer(
        "👤 Карточка пользователя\n\n"
        f"ID: <code>{telegram_id}</code>\n"
        f"Тариф: {_subscription_name(user)}\n"
        f"Подписка до: {user.get('subscription_until') or user.get('premium_until') or '-'}\n"
        f"Пробный анализ использован: {'да' if user.get('trial_used') else 'нет'}\n"
        f"Цель: {user['goal']}\n"
        f"Возраст: {user['age']}\n"
        f"Рост: {user['height']} см\n"
        f"Вес: {user['weight']} кг\n"
        f"Активность: {user['activity']}\n\n"
        f"Сегодня: {stats['calories']} ккал · Б {stats['protein']} · "
        f"Ж {stats['fat']} · У {stats['carbs']}\n\n"
        f"Последние записи:\n{meals_text}"
    )


@router.message(Command("admin_grant_premium"))
async def admin_grant_premium_handler(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    args = _command_args(message).split()
    telegram_id = _parse_user_id(args[0]) if args else None
    days = _parse_user_id(args[1]) if len(args) > 1 else MONTH_DAYS
    if not telegram_id or not days:
        await message.answer(
            "Формат: /admin_grant_premium <telegram_id> [дней]",
            parse_mode=None,
        )
        return
    if not get_user(telegram_id):
        await message.answer("Пользователь не найден. Он должен сначала пройти /start.")
        return

    premium_until = (datetime.now() + timedelta(days=days)).date().isoformat()
    set_subscription(telegram_id, "premium", premium_until)
    await message.answer(f"✅ Premium выдан пользователю {telegram_id} до {premium_until}.")
    await _maybe_auto_backup_db(bot, "admin_grant_premium")


@router.message(Command("admin_grant_basic"))
async def admin_grant_basic_handler(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    args = _command_args(message).split()
    telegram_id = _parse_user_id(args[0]) if args else None
    days = _parse_user_id(args[1]) if len(args) > 1 else MONTH_DAYS
    if not telegram_id or not days:
        await message.answer(
            "Формат: /admin_grant_basic <telegram_id> [дней]",
            parse_mode=None,
        )
        return
    if not get_user(telegram_id):
        await message.answer("Пользователь не найден. Он должен сначала пройти /start.")
        return

    subscription_until = (datetime.now() + timedelta(days=days)).date().isoformat()
    set_subscription(telegram_id, "basic", subscription_until)
    await message.answer(
        f"✅ Basic выдан пользователю {telegram_id} до {subscription_until}."
    )
    await _maybe_auto_backup_db(bot, "admin_grant_basic")


@router.message(Command("admin_revoke_premium", "admin_revoke_subscription"))
async def admin_revoke_premium_handler(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    telegram_id = _parse_user_id(_command_args(message))
    if not telegram_id:
        await message.answer(
            "Формат: /admin_revoke_premium <telegram_id>",
            parse_mode=None,
        )
        return
    if not get_user(telegram_id):
        await message.answer("Пользователь не найден.")
        return

    set_subscription(telegram_id, "trial", None)
    mark_trial_used(telegram_id)
    await message.answer(f"✅ Платный доступ пользователя {telegram_id} отключён.")
    await _maybe_auto_backup_db(bot, "admin_revoke_premium")


@router.message(Command("admin_reset_day"))
async def admin_reset_day_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    telegram_id = _parse_user_id(_command_args(message))
    if not telegram_id:
        await message.answer("Формат: /admin_reset_day <telegram_id>", parse_mode=None)
        return
    reset_today(telegram_id)
    await message.answer(f"✅ Сегодняшний дневник пользователя {telegram_id} очищен.")


@router.message(Command("admin_payments"))
async def admin_payments_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    limit = _parse_user_id(_command_args(message)) or 10
    limit = min(max(limit, 1), 50)
    payments = get_recent_payments(limit)
    if not payments:
        await message.answer("Платежей пока нет.")
        return

    lines = ["💳 Последние платежи\n"]
    for payment in payments:
        amount = (
            int(payment["amount"]) // 100
            if payment["currency"] == "RUB"
            else int(payment["amount"])
        )
        lines.append(
            f"<code>{payment['telegram_id']}</code> · {payment['plan']} · "
            f"{amount} {payment['currency']} · {payment['created_at']}\n"
            f"<code>{payment['telegram_payment_charge_id'] or '-'}</code>"
        )
    await message.answer("\n".join(lines))


@router.message(Command("admin_message"))
async def admin_message_handler(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    args = _command_args(message).split(maxsplit=1)
    telegram_id = _parse_user_id(args[0]) if args else None
    text = args[1].strip() if len(args) > 1 else ""
    if not telegram_id or not text:
        await message.answer(
            "Формат: /admin_message <telegram_id> <текст>",
            parse_mode=None,
        )
        return

    try:
        await bot.send_message(telegram_id, text, parse_mode=None)
    except Exception as exc:
        await message.answer(f"Не получилось отправить сообщение: {exc}")
        return
    await message.answer(f"✅ Сообщение отправлено пользователю {telegram_id}.")


@router.message(Command("admin_broadcast"))
async def admin_broadcast_handler(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    await state.set_state(AdminPanel.broadcast_text)
    await message.answer(
        "📣 Пришли текст рассылки одним сообщением.\n\n"
        "Отмена: /admin_cancel"
    )


@router.message(Command("admin_cancel"))
async def admin_cancel_handler(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return
    await state.clear()
    await message.answer("Админ-действие отменено.")


@router.message(AdminPanel.restore_db_file, F.document)
async def restoredb_file_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    document = message.document
    if not document:
        await message.answer("Пришли базу как документ .db или /admin_cancel.")
        return

    filename = document.file_name or ""
    if not filename.endswith((".db", ".sqlite", ".sqlite3")):
        await message.answer("Файл должен быть SQLite-базой: .db, .sqlite или .sqlite3.")
        return

    temp_path = Path(tempfile.gettempdir()) / f"dietnik_restore_{message.from_user.id}_{document.file_unique_id}.db"
    try:
        file = await bot.get_file(document.file_id)
        await bot.download_file(file.file_path, destination=temp_path)
        backup_path = restore_database_file(temp_path)
    except Exception as exc:
        await message.answer(f"Не получилось восстановить базу: {exc}", parse_mode=None)
        return
    finally:
        if temp_path.exists():
            temp_path.unlink()

    await state.clear()
    await message.answer(
        "✅ База восстановлена.\n\n"
        f"Старая база сохранена как: {backup_path}\n"
        f"Текущая база: {DB_PATH}",
        parse_mode=None,
    )


@router.message(AdminPanel.restore_db_file)
async def restoredb_waiting_file_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return
    await message.answer("Пришли SQLite-файл как документ или отправь /admin_cancel.")


@router.message(AdminPanel.broadcast_text)
async def admin_broadcast_text_handler(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    broadcast_text = message.text or ""
    if len(broadcast_text) < 2:
        await message.answer("Текст слишком короткий. Пришли нормальное сообщение или /admin_cancel.")
        return

    await state.update_data(broadcast_text=broadcast_text)
    users_count = len(get_all_user_ids())
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="admin_broadcast_confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast_cancel"),
            ]
        ]
    )
    await message.answer(
        "Предпросмотр рассылки:\n\n"
        f"{escape(broadcast_text)}\n\n"
        f"Получателей: {users_count}",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.in_({"admin_broadcast_confirm", "admin_broadcast_cancel"}))
async def admin_broadcast_callback_handler(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    if not _is_admin(callback):
        await callback.answer("Недоступно", show_alert=True)
        return

    if callback.data == "admin_broadcast_cancel":
        await state.clear()
        await callback.message.answer("Рассылка отменена.")
        await callback.answer()
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.message.answer("Текст рассылки не найден. Запусти /admin_broadcast заново.")
        await callback.answer()
        return

    sent = 0
    failed = 0
    for telegram_id in get_all_user_ids():
        try:
            await bot.send_message(telegram_id, broadcast_text, parse_mode=None)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await state.clear()
    await callback.message.answer(
        "✅ Рассылка завершена.\n\n"
        f"Отправлено: {sent}\n"
        f"Ошибок: {failed}"
    )
    await callback.answer()


@router.message(F.text.casefold().in_({"start", "/start", "старт", "/старт"}))
async def plain_start_handler(message: Message, state: FSMContext) -> None:
    """Help users who type start without the slash."""
    await start_handler(message, state)


@router.message(
    F.text.casefold().in_(
        {
            "my_id",
            "/my_id",
            "myid",
            "/myid",
            "id",
            "/id",
            "admin_id",
            "/admin_id",
        }
    )
)
async def plain_my_id_handler(message: Message) -> None:
    """Help users who type ID commands without the slash."""
    await my_id_handler(message)


@router.message(
    F.text.in_(
        {
            "🍽 Добавить еду",
            "📊 Дневник",
            "💡 Рекомендации",
            "🤖 Диетолог",
            "👤 Профиль",
            "💳 Подписка",
            "🍳 Рецепты",
            "📈 Отчёты",
            "💬 Поддержка",
        }
    )
)
async def menu_button_handler(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    if message.text == "🍽 Добавить еду":
        user = get_user(message.from_user.id)
        if not await _require_subscription(message, user):
            return
        await message.answer(
            "Пришли фото блюда, и я посчитаю КБЖУ.\n\n"
            "Если уже знаешь КБЖУ, напиши «добавить вручную» или /manual_food.",
            reply_markup=main_menu_keyboard(),
        )
    elif message.text == "📊 Дневник":
        await today_handler(message)
    elif message.text == "💡 Рекомендации":
        await recommendations_handler(message)
    elif message.text == "🤖 Диетолог":
        await dietitian_handler(message, state)
    elif message.text == "👤 Профиль":
        await profile_handler(message)
    elif message.text == "💳 Подписка":
        await subscription_handler(message)
    elif message.text == "🍳 Рецепты":
        await recipes_handler(message)
    elif message.text == "📈 Отчёты":
        await reports_handler(message)
    elif message.text == "💬 Поддержка":
        await _begin_support(message, state, bot)


@router.message(F.photo)
async def photo_handler(message: Message, bot: Bot) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return

    is_trial = _trial_available(user)
    if not is_trial and not _has_active_subscription(user):
        await message.answer(
            _locked_text(user),
            reply_markup=_subscription_markup(user),
        )
        return

    await message.answer(
        "📸 Пробую распознать блюдо..." if is_trial else "📸 Анализирую фото еды..."
    )

    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    except Exception:
        await message.answer("Не получилось получить фото. Попробуй отправить его ещё раз.")
        return

    result = await analyze_food_photo(file_url)
    if not result:
        await message.answer(
            "Не получилось точно распознать блюдо.\n\n"
            "Попробуй отправить фото ещё раз.",
            reply_markup=trial_keyboard(True) if is_trial else main_menu_keyboard(),
        )
        return
    if not result.get("is_food", True):
        await message.answer(
            "На фото не вижу еды.\n\n"
            "Сфотографируй блюдо ближе, при хорошем свете и без лишних предметов в кадре.",
            reply_markup=trial_keyboard(True) if is_trial else main_menu_keyboard(),
        )
        return

    if is_trial:
        if not mark_trial_used(message.from_user.id):
            latest_user = get_user(message.from_user.id) or user
            await message.answer(
                "Пробный анализ уже использован. Выбери подписку, чтобы продолжить.",
                reply_markup=_subscription_markup(latest_user),
            )
            return
        trial_user = get_user(message.from_user.id) or user
        await message.answer(
            "✨ Вот как работает Dietnik\n\n"
            f"🍽 На фото: {_safe(result['dish'])}\n"
            f"🔥 Калорийность: {result['calories']} ккал\n\n"
            "Это был бесплатный пробный анализ. Он не добавлен в дневник.\n\n"
            f"🌱 Basic — {BASIC_PRICE_RUB} ₽ / 30 дней\n"
            "Анализ фото · полный КБЖУ · дневник · дневная цель · рекомендации\n\n"
            f"🌿 Premium — {PREMIUM_PRICE_RUB} ₽ / 30 дней\n"
            "Всё из Basic · AI-диетолог · рецепты · отчёты за 7, 30 дней и весь период",
            reply_markup=_subscription_markup(trial_user),
        )
        return

    save_meal(
        telegram_id=message.from_user.id,
        dish_name=result["dish"],
        calories=result["calories"],
        protein=result["protein"],
        fat=result["fat"],
        carbs=result["carbs"],
        recommendation=result["recommendation"],
    )
    stats = get_today_stats(message.from_user.id)
    progress = _format_progress(user, stats)

    await message.answer(
        "✅ Приём пищи добавлен\n\n"
        f"🍽 Блюдо: {_safe(result['dish'])}\n"
        f"🔥 Калории: {result['calories']} ккал\n"
        f"🥩 Белки: {result['protein']} г\n"
        f"🥑 Жиры: {result['fat']} г\n"
        f"🍚 Углеводы: {result['carbs']} г\n"
        f"💡 Рекомендация: {_safe(result['recommendation'])}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{progress}\n\n"
        "Продолжай в том же духе 💪",
        reply_markup=main_menu_keyboard(),
    )


@router.message()
async def fallback_handler(message: Message) -> None:
    if message.chat.type != "private":
        return
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Я пока не знаю такую команду.\n\n"
            f"{_commands_text(_is_admin(message))}",
            reply_markup=main_menu_keyboard(),
            parse_mode=None,
        )
        return

    await message.answer(
        "Отправь фото еды или выбери действие в меню.",
        reply_markup=main_menu_keyboard(),
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    validate_config()
    init_db()
    logger.info(
        "Starting Dietnik release=%s support_chat_id=%s",
        BOT_RELEASE,
        SUPPORT_ADMIN_CHAT_ID,
    )

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted, starting polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
