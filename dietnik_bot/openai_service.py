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
