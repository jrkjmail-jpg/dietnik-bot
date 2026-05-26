"""SQLite storage for Dietnik users and meals."""

import sqlite3
from datetime import date, datetime, timedelta
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
        _ensure_column(conn, "users", "subscription_plan", "TEXT DEFAULT 'basic'")
        _ensure_column(conn, "users", "premium_until", "TEXT")
        _ensure_column(conn, "users", "updated_at", "TEXT")

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                plan TEXT,
                amount INTEGER,
                currency TEXT,
                provider_payment_charge_id TEXT,
                telegram_payment_charge_id TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fridge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                name TEXT,
                quantity TEXT,
                expires_at TEXT,
                created_at TEXT
            )
            """
        )


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if column_name not in {column["name"] for column in columns}:
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
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
                norm_carbs = excluded.norm_carbs,
                updated_at = excluded.created_at
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


def get_recent_meals(telegram_id: int, limit: int = 5) -> list[dict]:
    """Return recent meals for the current date."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT dish_name, calories, protein, fat, carbs, created_at
            FROM meals
            WHERE telegram_id = ? AND date = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_id, date.today().isoformat(), limit),
        ).fetchall()
    return [dict(row) for row in rows]


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


def get_week_stats(telegram_id: int) -> list[dict]:
    """Return daily nutrition totals for the last seven days."""
    start_date = date.today() - timedelta(days=6)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                date,
                COALESCE(SUM(calories), 0) AS calories,
                COALESCE(SUM(protein), 0) AS protein,
                COALESCE(SUM(fat), 0) AS fat,
                COALESCE(SUM(carbs), 0) AS carbs
            FROM meals
            WHERE telegram_id = ? AND date >= ?
            GROUP BY date
            ORDER BY date
            """,
            (telegram_id, start_date.isoformat()),
        ).fetchall()
    return [dict(row) for row in rows]


def reset_today(telegram_id: int) -> None:
    """Delete today's meals for a user."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM meals WHERE telegram_id = ? AND date = ?",
            (telegram_id, date.today().isoformat()),
        )


def set_subscription(
    telegram_id: int,
    plan: str,
    premium_until: str | None = None,
) -> None:
    """Update user's subscription plan."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET subscription_plan = ?, premium_until = ?, updated_at = ?
            WHERE telegram_id = ?
            """,
            (plan, premium_until, _now(), telegram_id),
        )


def save_subscription_payment(
    telegram_id: int,
    plan: str,
    amount: int,
    currency: str,
    provider_payment_charge_id: str,
    telegram_payment_charge_id: str,
) -> None:
    """Store successful Telegram payment details."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (
                telegram_id, plan, amount, currency,
                provider_payment_charge_id, telegram_payment_charge_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                plan,
                amount,
                currency,
                provider_payment_charge_id,
                telegram_payment_charge_id,
                _now(),
            ),
        )
