"""SQLite storage for Dietnik users and meals."""

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(Path(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db() -> None:
    """Create required tables if they do not exist."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                gender TEXT,
                age INTEGER,
                height INTEGER,
                weight REAL,
                activity TEXT,
                goal TEXT,
                norm_calories INTEGER,
                norm_protein INTEGER,
                norm_fat INTEGER,
                norm_carbs INTEGER,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                date TEXT,
                dish_name TEXT,
                calories INTEGER,
                protein INTEGER,
                fat INTEGER,
                carbs INTEGER,
                recommendation TEXT,
                created_at TEXT
            )
            """
        )


def save_user(
    telegram_id: int,
    gender: str,
    age: int,
    height: int,
    weight: float,
    activity: str,
    goal: str,
    norm_calories: int,
    norm_protein: int,
    norm_fat: int,
    norm_carbs: int,
) -> None:
    """Create or update user profile and daily nutrition norm."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO users (
                telegram_id, gender, age, height, weight, activity, goal,
                norm_calories, norm_protein, norm_fat, norm_carbs, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                gender = excluded.gender,
                age = excluded.age,
                height = excluded.height,
                weight = excluded.weight,
                activity = excluded.activity,
                goal = excluded.goal,
                norm_calories = excluded.norm_calories,
                norm_protein = excluded.norm_protein,
                norm_fat = excluded.norm_fat,
                norm_carbs = excluded.norm_carbs
            """,
            (
                telegram_id,
                gender,
                age,
                height,
                weight,
                activity,
                goal,
                norm_calories,
                norm_protein,
                norm_fat,
                norm_carbs,
                _now(),
            ),
        )


def get_user(telegram_id: int) -> Optional[dict]:
    """Return user profile or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return dict(row) if row else None


def save_meal(
    telegram_id: int,
    dish_name: str,
    calories: int,
    protein: int,
    fat: int,
    carbs: int,
    recommendation: str,
) -> None:
    """Save a meal for today's diary."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO meals (
                telegram_id, date, dish_name, calories, protein, fat,
                carbs, recommendation, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                date.today().isoformat(),
                dish_name,
                calories,
                protein,
                fat,
                carbs,
                recommendation,
                _now(),
            ),
        )


def get_today_stats(telegram_id: int) -> dict:
    """Return summed calories and macros for the current date."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(calories), 0) AS calories,
                COALESCE(SUM(protein), 0) AS protein,
                COALESCE(SUM(fat), 0) AS fat,
                COALESCE(SUM(carbs), 0) AS carbs
            FROM meals
            WHERE telegram_id = ? AND date = ?
            """,
            (telegram_id, date.today().isoformat()),
        ).fetchone()

    return {
        "calories": int(row["calories"]),
        "protein": int(row["protein"]),
        "fat": int(row["fat"]),
        "carbs": int(row["carbs"]),
    }


def reset_today(telegram_id: int) -> None:
    """Delete today's meals for a user."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM meals WHERE telegram_id = ? AND date = ?",
            (telegram_id, date.today().isoformat()),
        )
