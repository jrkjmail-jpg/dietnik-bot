"""SQLite storage for Dietnik users and meals."""

import secrets
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from config import DATA_DIR, DB_PATH, DB_PATH_EXPLICIT, LEGACY_DB_PATH


STORAGE_PROBE_PATH = DATA_DIR / "storage_probe.txt"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(Path(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_data_dir() -> None:
    """Create the persistent data directory before opening SQLite."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def migrate_legacy_database() -> bool:
    """Copy the old local SQLite database into persistent storage once."""
    db_path = Path(DB_PATH)
    legacy_path = Path(LEGACY_DB_PATH)
    if db_path.exists() or not legacy_path.exists() or legacy_path.resolve() == db_path.resolve():
        return False

    shutil.copy2(legacy_path, db_path)
    return True


def init_db() -> None:
    """Create required tables if they do not exist."""
    ensure_data_dir()
    migrate_legacy_database()

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
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
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


def get_app_state(key: str) -> Optional[str]:
    """Read a small persisted app state value."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (key,),
        ).fetchone()
    return str(row["value"]) if row else None


def set_app_state(key: str, value: str) -> None:
    """Persist a small app state value."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, _now()),
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


def get_all_user_ids() -> list[int]:
    """Return all registered Telegram user IDs."""
    with _connect() as conn:
        rows = conn.execute("SELECT telegram_id FROM users ORDER BY created_at DESC").fetchall()
    return [int(row["telegram_id"]) for row in rows]


def get_users_page(limit: int = 20, offset: int = 0) -> list[dict]:
    """Return a page of users for admin views."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                telegram_id, goal, age, height, weight, activity,
                subscription_plan, premium_until, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


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


def get_user_meals(telegram_id: int, limit: int = 10) -> list[dict]:
    """Return recent meals for a user across all dates."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT date, dish_name, calories, protein, fat, carbs, created_at
            FROM meals
            WHERE telegram_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
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


def get_admin_stats() -> dict:
    """Return aggregate product metrics for admins."""
    today = date.today().isoformat()
    with _connect() as conn:
        users_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        meals_count = conn.execute("SELECT COUNT(*) AS count FROM meals").fetchone()["count"]
        meals_today = conn.execute(
            "SELECT COUNT(*) AS count FROM meals WHERE date = ?",
            (today,),
        ).fetchone()["count"]
        active_today = conn.execute(
            "SELECT COUNT(DISTINCT telegram_id) AS count FROM meals WHERE date = ?",
            (today,),
        ).fetchone()["count"]
        premium_users = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM users
            WHERE subscription_plan = 'premium'
              AND (premium_until IS NULL OR premium_until >= ?)
            """,
            (today,),
        ).fetchone()["count"]
        payments = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(amount), 0) AS amount
            FROM subscriptions
            """
        ).fetchone()

    return {
        "users_count": int(users_count),
        "meals_count": int(meals_count),
        "meals_today": int(meals_today),
        "active_today": int(active_today),
        "premium_users": int(premium_users),
        "payments_count": int(payments["count"]),
        "payments_amount": int(payments["amount"]),
    }


def get_db_status() -> dict:
    """Return filesystem and aggregate DB status for admins."""
    db_path = Path(DB_PATH)
    db_exists = db_path.exists()
    stats = get_admin_stats()
    data_dir_resolved = Path(DATA_DIR).resolve()
    db_resolved = db_path.resolve() if db_exists else db_path.parent.resolve() / db_path.name
    try:
        in_data_dir = db_resolved.is_relative_to(data_dir_resolved)
    except AttributeError:
        in_data_dir = str(db_resolved).startswith(str(data_dir_resolved))

    return {
        "db_path": str(db_path),
        "db_exists": db_exists,
        "db_size": db_path.stat().st_size if db_exists else 0,
        "users_count": stats["users_count"],
        "payments_count": stats["payments_count"],
        "payments_amount": stats["payments_amount"],
        "premium_users": stats["premium_users"],
        "in_app_data": str(db_resolved).startswith("/app/data"),
        "in_data_dir": in_data_dir,
        "db_path_explicit": DB_PATH_EXPLICIT,
    }


def get_storage_probe_status() -> dict:
    """Create/read a persistence probe token in DB and a file."""
    token = get_app_state("storage_probe_token")
    created_db_token = False
    if not token:
        token = secrets.token_hex(16)
        set_app_state("storage_probe_token", token)
        created_db_token = True

    STORAGE_PROBE_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists_before = STORAGE_PROBE_PATH.exists()
    file_token = STORAGE_PROBE_PATH.read_text(encoding="utf-8").strip() if file_exists_before else ""
    file_matches = file_token == token
    if not file_exists_before or not file_matches:
        STORAGE_PROBE_PATH.write_text(token, encoding="utf-8")

    return {
        "db_token": token,
        "file_token": file_token,
        "created_db_token": created_db_token,
        "file_exists_before": file_exists_before,
        "file_matches": file_matches,
        "probe_path": str(STORAGE_PROBE_PATH),
    }


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


def get_recent_payments(limit: int = 10) -> list[dict]:
    """Return recent subscription payments."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id, plan, amount, currency, created_at
            FROM subscriptions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_fridge_item(
    telegram_id: int,
    name: str,
    quantity: str = "",
    expires_at: str = "",
) -> int:
    """Save a product in user's Premium fridge."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO fridge_items (telegram_id, name, quantity, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                name.strip(),
                quantity.strip(),
                expires_at.strip(),
                _now(),
            ),
        )
        return int(cursor.lastrowid)


def get_fridge_items(telegram_id: int, limit: int = 50) -> list[dict]:
    """Return user's saved fridge products."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, quantity, expires_at, created_at
            FROM fridge_items
            WHERE telegram_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_fridge_item(telegram_id: int, item_id: int) -> bool:
    """Delete one fridge product owned by user."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM fridge_items WHERE telegram_id = ? AND id = ?",
            (telegram_id, item_id),
        )
        return cursor.rowcount > 0


def clear_fridge(telegram_id: int) -> int:
    """Delete all fridge products for a user and return deleted count."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM fridge_items WHERE telegram_id = ?",
            (telegram_id,),
        )
        return int(cursor.rowcount)


def backup_database_file() -> Optional[Path]:
    """Create a timestamped backup copy of the current DB file."""
    db_path = Path(DB_PATH)
    if not db_path.exists():
        return None

    backups_dir = Path(DATA_DIR) / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backups_dir / f"bot_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def restore_database_file(uploaded_db_path: Path) -> Path:
    """Validate and replace the current DB file, keeping a backup first."""
    with sqlite3.connect(uploaded_db_path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity_check failed: {integrity}")

    backup_path = backup_database_file()
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(uploaded_db_path, db_path)
    init_db()
    return backup_path or db_path
