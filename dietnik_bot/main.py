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
    BOT_TOKEN,
    DATA_DIR,
    DB_PATH,
    OPENAI_API_KEY,
    PAYMENT_PROVIDER_TOKEN,
    PERSISTENCE_PATH,
    SUPPORT_USERNAME,
    validate_config,
)
from database import (
    get_admin_stats,
    get_all_user_ids,
    get_app_state,
    get_db_status,
    get_recent_payments,
    get_recent_meals,
    get_storage_probe_status,
    get_today_stats,
    get_user,
    get_user_meals,
    get_users_page,
    get_week_stats,
    init_db,
    reset_today,
    restore_database_file,
    set_app_state,
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


class AdminPanel(StatesGroup):
    broadcast_text = State()
    restore_db_file = State()


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

Все команды: /commands
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
        "/admin_grant_premium <telegram_id> [дней] — выдать Premium\n"
        "/admin_revoke_premium <telegram_id> — снять Premium\n"
        "/admin_reset_day <telegram_id> — очистить дневник за сегодня\n"
        "/admin_payments [кол-во] — последние платежи\n"
        "/admin_message <telegram_id> <текст> — написать пользователю\n"
        "/admin_broadcast — рассылка всем пользователям\n"
        "/admin_cancel — отменить админ-действие\n"
        "/admin_health — диагностика конфига и БД\n\n"
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
        "/today — прогресс за сегодня\n"
        "/profile — профиль и дневная норма\n"
        "/recommendations — рекомендации на сегодня\n"
        "/subscription — тарифы и подписка\n"
        "/help — как пользоваться\n"
        "/commands — список команд\n\n"
        "Premium:\n"
        "/dietitian — AI-диетолог\n"
        "/fridge — холодильник\n"
        "/recipes — рецепты под остаток КБЖУ\n"
        "/reports — недельные отчёты\n\n"
        "Сервисные:\n"
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
            "/admin_grant_premium <telegram_id> [дней] — выдать Premium\n"
            "/admin_revoke_premium <telegram_id> — снять Premium\n"
            "/admin_reset_day <telegram_id> — очистить дневник пользователя\n"
            "/admin_payments [кол-во] — последние платежи\n"
            "/admin_message <telegram_id> <текст> — написать пользователю\n"
            "/admin_broadcast — рассылка всем пользователям\n"
            "/admin_cancel — отменить админ-действие\n"
            "/admin_health — диагностика\n"
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
async def successful_payment_handler(message: Message, bot: Bot) -> None:
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
        f"Оплата: {'подключена' if PAYMENT_PROVIDER_TOKEN else 'не подключена'}\n"
        f"DATA_DIR: {DATA_DIR}\n"
        f"DB_PATH: {DB_PATH}\n"
        f"PERSISTENCE_PATH: {PERSISTENCE_PATH}\n"
        f"Пользователей в БД: {stats['users_count']}\n"
        f"Приёмов пищи в БД: {stats['meals_count']}"
    )


@router.message(Command("dbstatus"))
async def dbstatus_handler(message: Message) -> None:
    if not _is_admin(message):
        await _deny_admin(message)
        return

    status = get_db_status()
    size_kb = round(status["db_size"] / 1024, 2)
    payments_rub = status["payments_amount"] // 100
    await message.answer(
        "🗄 Статус базы\n\n"
        f"Путь базы: {status['db_path']}\n"
        f"Файл существует: {'да' if status['db_exists'] else 'нет'}\n"
        f"Размер: {status['db_size']} байт ({size_kb} KB)\n"
        f"Пользователей: {status['users_count']}\n"
        f"Платежей/заказов: {status['payments_count']}\n"
        f"Общая сумма покупок: {payments_rub} ₽\n"
        f"Premium пользователей: {status['premium_users']}\n"
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
    payments_rub = stats["payments_amount"] // 100
    await message.answer(
        "📊 Статистика Dietnik\n\n"
        f"Пользователи: {stats['users_count']}\n"
        f"Premium: {stats['premium_users']}\n"
        f"Активны сегодня: {stats['active_today']}\n"
        f"Приёмов пищи сегодня: {stats['meals_today']}\n"
        f"Приёмов пищи всего: {stats['meals_count']}\n"
        f"Платежей: {stats['payments_count']}\n"
        f"Выручка: {payments_rub} ₽"
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
        f"Premium до: {user.get('premium_until') or '-'}\n"
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


@router.message(Command("admin_revoke_premium"))
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

    set_subscription(telegram_id, "basic", None)
    await message.answer(f"✅ Пользователь {telegram_id} переведён на Basic.")
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
        amount_rub = int(payment["amount"]) // 100
        lines.append(
            f"<code>{payment['telegram_id']}</code> · {payment['plan']} · "
            f"{amount_rub} {payment['currency']} · {payment['created_at']}"
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

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted, starting polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
