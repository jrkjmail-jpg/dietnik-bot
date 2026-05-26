"""Telegram bot entry point for Dietnik."""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from config import BOT_TOKEN, validate_config
from database import (
    get_today_stats,
    get_user,
    init_db,
    reset_today,
    save_meal,
    save_user,
)
from keyboards import activity_keyboard, gender_keyboard, goal_keyboard, remove_keyboard
from nutrition import calculate_norm, calculate_remaining
from openai_service import analyze_food_photo


router = Router()


class Onboarding(StatesGroup):
    gender = State()
    age = State()
    height = State()
    weight = State()
    activity = State()
    goal = State()


WELCOME_TEXT = """
👋 Привет! Я Dietnik — твой AI-помощник по питанию.

Я помогу:
📸 считать калории по фото еды
📊 вести дневной прогресс
🎯 показывать, сколько осталось до цели
💡 давать рекомендации по питанию
""".strip()

HELP_TEXT = """
Как пользоваться:
1. Пройди настройку через /start
2. Отправь фото еды
3. Бот посчитает КБЖУ
4. Следи за прогрессом за день

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
    await state.clear()
    await message.answer(WELCOME_TEXT)
    await message.answer("Начнём настройку. Укажи пол:", reply_markup=gender_keyboard())
    await state.set_state(Onboarding.gender)


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
        reply_markup=remove_keyboard(),
    )


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
        "📌 Дневная норма:\n"
        f"{_format_norm(user)}"
    )


@router.message(Command("today"))
async def today_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала пройди настройку через /start.")
        return

    stats = get_today_stats(message.from_user.id)
    await message.answer(_format_progress(user, stats))


@router.message(Command("reset_day"))
async def reset_day_handler(message: Message) -> None:
    reset_today(message.from_user.id)
    await message.answer("✅ Сегодняшние приёмы пищи удалены.")


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT)


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
        "Продолжай в том же духе 💪"
    )


@router.message()
async def fallback_handler(message: Message) -> None:
    await message.answer("Отправь фото еды или используй /help.")


async def main() -> None:
    validate_config()
    init_db()

    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
