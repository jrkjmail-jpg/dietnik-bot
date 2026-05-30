"""OpenAI Vision integration for food photo analysis."""

import json
from typing import Optional

from openai import AsyncOpenAI

from config import OPENAI_API_KEY

FOOD_ANALYSIS_PROMPT = """
Ты AI-диетолог. Проанализируй фото еды.

Задачи:
- определи блюдо;
- рассчитай калории, белки, жиры и углеводы;
- дай короткую рекомендацию по питанию на русском языке.

Правила:
- НЕ используй слова "примерно", "около", "~";
- НЕ пиши диапазоны;
- верни только JSON без markdown;
- все числовые значения должны быть целыми числами.

Строгий формат ответа:
{
  "dish": "название блюда",
  "calories": 500,
  "protein": 35,
  "fat": 20,
  "carbs": 45,
  "recommendation": "короткая рекомендация"
}
""".strip()

FRIDGE_ANALYSIS_PROMPT = """
Ты AI-помощник для холодильника в боте Dietnik. Проанализируй фото продуктов, полки холодильника, продуктов на столе или чека.

Задачи:
- найди отдельные продукты;
- назови продукты коротко на русском;
- если видишь количество, упаковку или вес, запиши это в quantity;
- если видишь срок годности, запиши его в expires_at;
- если срок или количество не видны, верни пустую строку.

Правила:
- НЕ добавляй готовые блюда как один продукт, разбивай на видимые ингредиенты только если они понятны;
- НЕ придумывай срок годности;
- НЕ используй слова "примерно", "около", "~";
- верни только JSON без markdown.

Строгий формат ответа:
{
  "items": [
    {
      "name": "яйца",
      "quantity": "10 шт",
      "expires_at": "2026-06-05"
    }
  ]
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
        return {
            "dish": str(data["dish"]).strip(),
            "calories": int(data["calories"]),
            "protein": int(data["protein"]),
            "fat": int(data["fat"]),
            "carbs": int(data["carbs"]),
            "recommendation": str(data["recommendation"]).strip(),
        }
    except (TypeError, ValueError):
        return None


def _parse_fridge_json(raw_text: str) -> Optional[list[dict]]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    parsed_items = []
    seen = set()
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if len(name) < 2:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        parsed_items.append(
            {
                "name": name[:80],
                "quantity": str(item.get("quantity") or "").strip()[:60],
                "expires_at": str(item.get("expires_at") or "").strip()[:30],
            }
        )

    return parsed_items or None


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


async def analyze_fridge_photo(file_url: str) -> Optional[list[dict]]:
    """Analyze a Telegram photo and return products for user's fridge."""
    try:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": FRIDGE_ANALYSIS_PROMPT},
                        {"type": "image_url", "image_url": {"url": file_url}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = response.choices[0].message.content
        if not content:
            return None
        return _parse_fridge_json(content)
    except Exception:
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
