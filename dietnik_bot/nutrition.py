"""Nutrition calculations for user daily goals and progress."""


ACTIVITY_FACTORS = {
    "Низкая": 1.2,
    "Средняя": 1.55,
    "Высокая": 1.725,
}

GOAL_FACTORS = {
    "Похудение": 0.85,
    "Поддержание": 1.0,
    "Набор массы": 1.10,
}


def calculate_norm(
    gender: str,
    age: int,
    height: int,
    weight: float,
    activity: str,
    goal: str,
) -> dict:
    """Calculate daily calories, protein, fat, and carbs."""
    if gender == "Мужской":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    activity_factor = ACTIVITY_FACTORS[activity]
    goal_factor = GOAL_FACTORS[goal]

    calories = bmr * activity_factor * goal_factor
    protein = weight * 1.8
    fat = weight * 0.9
    carbs = (calories - protein * 4 - fat * 9) / 4

    return {
        "calories": max(0, round(calories)),
        "protein": max(0, round(protein)),
        "fat": max(0, round(fat)),
        "carbs": max(0, round(carbs)),
    }


def calculate_remaining(user: dict, stats: dict) -> dict:
    """Return how much nutrition is left until the user's daily goal."""
    return {
        "calories": max(0, user["norm_calories"] - stats["calories"]),
        "protein": max(0, user["norm_protein"] - stats["protein"]),
        "fat": max(0, user["norm_fat"] - stats["fat"]),
        "carbs": max(0, user["norm_carbs"] - stats["carbs"]),
    }
