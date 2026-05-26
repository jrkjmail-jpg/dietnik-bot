"""Telegram bot entry point for Dietnik."""

import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from config import BOT_TOKEN, PAYMENT_PROVIDER_TOKEN, SUPPORT_USERNAME, validate_config
from database import (
    get_recent_meals,
    get_today_stats,
    get_user,
    get_week_stats,
    init_db,
    reset_today,
    save_subscription_payment,
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
    subscription_keyboard,
)
from nutrition import calculate_norm, calculate_remaining
from openai_service import analyze_food_photo, ask_dietitian


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


BASIC_PRICE_RUB = 490
PREMIUM_PRICE_RUB = 890
MONTH_DAYS = 30

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
2. Отправь фото еды
3. Бот посчитает КБЖУ
4. Следи за прогрессом за день

Основные кнопки:
🍽 Добавить еду — отправь фото блюда
📊 Дневник — прогресс за день
💡 Рекомендации — что улучшить сегодня
🤖 Диетолог — вопрос AI-диетологу
💳 Подписка — тарифы Basic и Premium

Важно:
Оценка по фото может отличаться от реальности. Для максимальной точности используй весы и проверяй порции.
""".strip()


def _format_norm(user: dict) -> str:
    return (
        f"🔥 Калории: {user['norm_calories']} ккал\n"
        f"🥩 Белки: {user['norm_protein']} г\n"
        f"🥑 Жиры: {user['norm_fat']} г\n"
        f"🍚 Углеводы: {user['norm_carbs']} г"
    )


def _is_premium(user: dict) -> bool:
    if user.get("subscription_plan") == "premium" and not user.get("premium_until"):
        return True
    premium_until = user.get("premium_until")
    if not premium_until:
        return False
    try:
        return datetime.fromisoformat(premium_until).date() >= date.today()
    except ValueError:
        return False


def _subscription_name(user: dict) -> str:
    return "Premium" if _is_premium(user) else "Basic"


def _progress_bar(value: int, target: int, width: int = 10) -> str:
    if target <= 0:
        return "⬜" * width
    filled = min(width, round(value / target * width))
    return "🟩" * filled + "⬜" * (width - filled)


def _percent(value: int, target: int) -> int:
    if target <= 0:
        return 0
    return min(999, round(value / target * 100))


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
        f"🥩 Белки: {stats['protein']} / {user['norm_protein']} г\n"
        f"🥑 Жиры: {stats['fat']} / {user['norm_fat']} г\n"
        f"🍚 Углеводы: {stats['carbs']} / {user['norm_carbs']} г\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "🎯 Осталось до цели:\n\n"
        f"🔥 Калории: {remaining['calories']} ккал\n"
        f"🥩 Белки: {remaining['protein']} г\n"
        f"🥑 Жиры: {remaining['fat']} г\n"
        f"🍚 Углеводы: {remaining['carbs']} г"
    )


def _format_dashboard(user: dict, stats: dict, first_name: str | None = None) -> str:
    remaining = calculate_remaining(user, stats)
    name = first_name or "друг"
    day_number = _days_from_created_at(user)
    recent_meals = get_recent_meals(user["telegram_id"], limit=3)
    meals_text = "\n".join(
        f"• {meal['dish_name']} — {meal['calories']} ккал" for meal in recent_meals
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
    premium_until = user.get("premium_until") if user else None
    premium_line = f"\nPremium активен до: {premium_until}" if premium_until else ""
    return (
        "💳 Подписка Dietnik\n\n"
        f"Текущий тариф: {current_plan}{premium_line}\n\n"
        f"🌱 Basic — {BASIC_PRICE_RUB} ₽/мес\n"
        "Дневник · фото-учёт · AI-анализ еды · дневная цель · рекомендации\n\n"
        f"🌿 Premium — {PREMIUM_PRICE_RUB} ₽/мес\n"
        "Всё из Basic · AI-диетолог · холодильник · рецепты под остаток КБЖУ · "
        "отчёты · прогресс тела\n\n"
        "Premium делает бота персональным ассистентом, а не просто счётчиком калорий."
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


def _premium_required_text() -> str:
    return (
        "🌿 Эта функция входит в Premium.\n\n"
        "Premium открывает холодильник, рецепты под остаток КБЖУ, отчёты и расширенного AI-диетолога.\n"
        "Открой раздел «💳 Подписка», чтобы посмотреть тарифы."
    )


async def _send_dashboard(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    stats = get_today_stats(message.from_user.id)
    await message.answer(
        _format_dashboard(user, stats, message.from_user.first_name),
        reply_markup=main_menu_keyboard(),
    )


def _parse_int(text: str) -> int | None:
    try:
        value = int(text.strip())
    except (AttributeError, ValueError):
        return None
    return value if value > 0 else None


def _parse_float(text: str) -> float | None:
    try:
        value = float(text.strip().replace(",", "."))
    except (AttributeError, ValueError):
        return None
    return value if value > 0 else None


@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext) -> None:
    logger.info("Received /start from user_id=%s", message.from_user.id)
    await state.clear()
    await message.answer(WELCOME_TEXT)
    await message.answer("Начнём настройку. Укажи пол:", reply_markup=gender_keyboard())
    await state.set_state(Onboarding.gender)
    logger.info("Sent onboarding start to user_id=%s", message.from_user.id)


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

    await message.answer(
        "✅ Настройка завершена!\n\n"
        f"🎯 Твоя цель: {data['goal']}\n"
        "📌 Дневная норма:\n\n"
        f"🔥 Калории: {norm['calories']} ккал\n"
        f"🥩 Белки: {norm['protein']} г\n"
        f"🥑 Жиры: {norm['fat']} г\n"
        f"🍚 Углеводы: {norm['carbs']} г\n\n"
        "Теперь просто отправь фото еды 🍽",
        reply_markup=main_menu_keyboard(),
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
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return

    stats = get_today_stats(message.from_user.id)
    await message.answer(_format_progress(user, stats), reply_markup=main_menu_keyboard())


@router.message(Command("reset_day"))
async def reset_day_handler(message: Message) -> None:
    reset_today(message.from_user.id)
    await message.answer("✅ Сегодняшние приёмы пищи удалены.", reply_markup=main_menu_keyboard())


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


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
        reply_markup=subscription_keyboard(bool(PAYMENT_PROVIDER_TOKEN)),
    )


@router.message(Command("recommendations"))
async def recommendations_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    stats = get_today_stats(message.from_user.id)
    await message.answer(
        _format_recommendations(user, stats),
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("reports"))
async def reports_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    if not _is_premium(user):
        await message.answer(_premium_required_text(), reply_markup=main_menu_keyboard())
        return

    week_stats = get_week_stats(message.from_user.id)
    if not week_stats:
        await message.answer("📈 За неделю пока нет записей.", reply_markup=main_menu_keyboard())
        return

    avg_calories = round(sum(day["calories"] for day in week_stats) / len(week_stats))
    avg_protein = round(sum(day["protein"] for day in week_stats) / len(week_stats))
    await message.answer(
        "📈 Отчёт за неделю\n\n"
        f"Средние калории: {avg_calories} ккал/день\n"
        f"Средний белок: {avg_protein} г/день\n"
        f"Дней с записями: {len(week_stats)} из 7\n\n"
        "Следующий шаг: удерживать дневник минимум 5 дней подряд.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("fridge"))
async def fridge_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    if not _is_premium(user):
        await message.answer(_premium_required_text(), reply_markup=main_menu_keyboard())
        return
    await message.answer(
        "🧊 Холодильник\n\n"
        "Раздел готовится: здесь будут продукты, сроки годности и рецепты из того, что уже есть дома.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("recipes"))
async def recipes_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return
    if not _is_premium(user):
        await message.answer(_premium_required_text(), reply_markup=main_menu_keyboard())
        return

    stats = get_today_stats(message.from_user.id)
    remaining = calculate_remaining(user, stats)
    if remaining["calories"] < 250:
        idea = "Творог 2–5% с ягодами или белковый омлет без масла."
    elif remaining["protein"] > 35:
        idea = "Куриная грудка/рыба + крупа + большой салат."
    elif remaining["carbs"] > 60:
        idea = "Гречка с индейкой, паста с тунцом или рис с овощами."
    else:
        idea = "Тёплый салат с яйцом, овощами и небольшим количеством авокадо."

    await message.answer(
        "🍳 Рецепты под остаток КБЖУ\n\n"
        f"Осталось: 🔥 {remaining['calories']} ккал · 🥩 {remaining['protein']} г · "
        f"🥑 {remaining['fat']} г · 🍚 {remaining['carbs']} г\n\n"
        f"Идея блюда: {idea}\n\n"
        "Следующий этап развития: база рецептов, продукты из холодильника и процент совпадения с целью.",
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
            reply_markup=main_menu_keyboard(),
        )
        return
    await message.answer("🤖 Напиши вопрос диетологу одним сообщением.")
    await state.set_state(Consultation.question)


@router.message(Consultation.question)
async def dietitian_question_handler(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Сначала пройди настройку через /start.")
        return
    await message.answer("Думаю над ответом...")
    stats = get_today_stats(message.from_user.id)
    answer = await ask_dietitian(message.text or "", user, stats)
    await state.clear()
    await message.answer(f"🤖 Диетолог\n\n{answer}", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.in_({"buy_basic", "buy_premium"}))
async def buy_subscription_handler(callback: CallbackQuery, bot: Bot) -> None:
    plan = "premium" if callback.data == "buy_premium" else "basic"
    amount_rub = PREMIUM_PRICE_RUB if plan == "premium" else BASIC_PRICE_RUB
    title = "Dietnik Premium на 30 дней" if plan == "premium" else "Dietnik Basic на 30 дней"

    if not PAYMENT_PROVIDER_TOKEN:
        await callback.message.answer(
            "Оплата ещё не подключена на сервере.\n\n"
            f"Добавь PAYMENT_PROVIDER_TOKEN в Bothost или напиши в поддержку: {SUPPORT_USERNAME}",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title,
        description="Подписка открывает функции Dietnik на 30 дней.",
        payload=f"dietnik_{plan}_30",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=title, amount=amount_rub * 100)],
        start_parameter=f"dietnik-{plan}",
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payment = message.successful_payment
    plan = "premium" if "premium" in payment.invoice_payload else "basic"
    premium_until = None
    if plan == "premium":
        premium_until = (datetime.now() + timedelta(days=MONTH_DAYS)).date().isoformat()

    set_subscription(message.from_user.id, plan, premium_until)
    save_subscription_payment(
        telegram_id=message.from_user.id,
        plan=plan,
        amount=payment.total_amount,
        currency=payment.currency,
        provider_payment_charge_id=payment.provider_payment_charge_id,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
    )
    await message.answer(
        "✅ Оплата прошла успешно!\n\n"
        f"Тариф {_subscription_name(get_user(message.from_user.id) or {})} активирован.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text.casefold().in_({"start", "старт"}))
async def plain_start_handler(message: Message, state: FSMContext) -> None:
    """Help users who type start without the slash."""
    await start_handler(message, state)


@router.message(
    F.text.in_(
        {
            "🍽 Добавить еду",
            "📊 Дневник",
            "💡 Рекомендации",
            "🤖 Диетолог",
            "👤 Профиль",
            "💳 Подписка",
            "🧊 Холодильник",
            "🍳 Рецепты",
            "📈 Отчёты",
        }
    )
)
async def menu_button_handler(message: Message, state: FSMContext) -> None:
    if message.text == "🍽 Добавить еду":
        await message.answer("Пришли фото блюда, и я посчитаю КБЖУ.", reply_markup=main_menu_keyboard())
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
    elif message.text == "🧊 Холодильник":
        await fridge_handler(message)
    elif message.text == "🍳 Рецепты":
        await recipes_handler(message)
    elif message.text == "📈 Отчёты":
        await reports_handler(message)


@router.message(F.photo)
async def photo_handler(message: Message, bot: Bot) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return

    await message.answer("📸 Анализирую фото еды...")

    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    except Exception:
        await message.answer("Не получилось получить фото. Попробуй отправить его ещё раз.")
        return

    result = await analyze_food_photo(file_url)
    if not result:
        await message.answer("Не получилось точно распознать блюдо. Попробуй отправить фото ещё раз.")
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
        f"🍽 Блюдо: {result['dish']}\n"
        f"🔥 Калории: {result['calories']} ккал\n"
        f"🥩 Белки: {result['protein']} г\n"
        f"🥑 Жиры: {result['fat']} г\n"
        f"🍚 Углеводы: {result['carbs']} г\n"
        f"💡 Рекомендация: {result['recommendation']}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{progress}\n\n"
        "Продолжай в том же духе 💪",
        reply_markup=main_menu_keyboard(),
    )


@router.message()
async def fallback_handler(message: Message) -> None:
    await message.answer(
        "Отправь фото еды или выбери действие в меню.",
        reply_markup=main_menu_keyboard(),
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    validate_config()
    init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted, starting polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
