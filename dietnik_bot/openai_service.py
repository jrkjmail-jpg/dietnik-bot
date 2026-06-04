"""OpenAI Vision integration for food photo analysis."""

import json
from typing import Optional

from openai import AsyncOpenAI

from config import OPENAI_API_KEY

FOOD_ANALYSIS_PROMPT = """
Ты AI-диетолог. Проанализируй фото еды.

Задачи:
- сначала определи, есть ли на фото еда или напиток;
- определи блюдо;
- рассчитай калории, белки, жиры и углеводы;
- дай короткую рекомендацию по питанию на русском языке.

Правила:
- если еды или напитка на фото нет, верни is_food false, нули в КБЖУ и короткую рекомендацию сфотографировать еду крупнее и при хорошем свете;
- НЕ используй слова "примерно", "около", "~";
- НЕ пиши диапазоны;
- верни только JSON без markdown;
- все числовые значения должны быть целыми числами.

Строгий формат ответа:
{
  "is_food": true,
  "dish": "название блюда",
  "calories": 500,
  "protein": 35,
  "fat": 20,
  "carbs": 45,
  "recommendation": "короткая рекомендация"
}
""".strip()


def _parse_food_json(raw_text: str) -> Optional[dict]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    required = ["dish", "calories", "protein", "fat", "carbs", "recommendation"]
    if not isinstance(data, dict) or any(key not in data for key in required):
        return None

    try:
        is_food = data.get("is_food", True)
        if isinstance(is_food, str):
            is_food = is_food.strip().casefold() in {"true", "да", "yes", "1"}
        return {
            "is_food": bool(is_food),
            "dish": str(data["dish"]).strip(),
            "calories": int(data["calories"]),
            "protein": int(data["protein"]),
            "fat": int(data["fat"]),
            "carbs": int(data["carbs"]),
            "recommendation": str(data["recommendation"]).strip(),
        }
    except (TypeError, ValueError):
        return None


async def analyze_food_photo(file_url: str) -> Optional[dict]:
    """Analyze a Telegram photo by URL and return parsed nutrition data."""
    try:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": FOOD_ANALYSIS_PROMPT},
                        {"type": "image_url", "image_url": {"url": file_url}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content
        if not content:
            return None
        return _parse_food_json(content)
    except Exception:
        # The bot should keep working even if OpenAI is unavailable or responds badly.
        return None


async def ask_dietitian(question: str, user: dict, stats: dict) -> str:
    """Return a concise nutrition answer based on user's profile and day stats."""
    try:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты Dietnik, дружелюбный AI-диетолог и повар. "
                        "Отвечай на русском, коротко, практично и без медицинских диагнозов. "
                        "Если вопрос про болезнь, беременность, лекарства или РПП, советуй обратиться к врачу."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Профиль: цель {user['goal']}, возраст {user['age']}, "
                        f"рост {user['height']} см, вес {user['weight']} кг. "
                        f"Норма: {user['norm_calories']} ккал, "
                        f"Б {user['norm_protein']} г, Ж {user['norm_fat']} г, "
                        f"У {user['norm_carbs']} г. "
                        f"Сегодня: {stats['calories']} ккал, Б {stats['protein']} г, "
                        f"Ж {stats['fat']} г, У {stats['carbs']} г.\n\n"
                        f"Вопрос пользователя: {question}"
                    ),
                },
            ],
            temperature=0.4,
            max_tokens=450,
        )
        content = response.choices[0].message.content
        if not content:
            return "Не получилось подготовить ответ. Попробуй задать вопрос чуть проще."
        return content.strip()
    except Exception:
        return "Не получилось связаться с AI-диетологом. Попробуй ещё раз чуть позже."
