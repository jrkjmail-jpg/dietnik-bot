"""SQLite storage for Dietnik users and meals."""

import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from config import DATA_DIR, DB_PATH, DB_PATH_EXPLICIT, LEGACY_DB_PATH


STORAGE_PROBE_PATH = DATA_DIR / "storage_probe.txt"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(Path(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
        _ensure_column(conn, "users", "subscription_until", "TEXT")
        _ensure_column(conn, "users", "trial_used", "INTEGER DEFAULT 0")
        _ensure_column(conn, "users", "updated_at", "TEXT")
        conn.execute(
            """
            UPDATE users
            SET subscription_until = premium_until
            WHERE subscription_plan = 'premium'
              AND subscription_until IS NULL
              AND premium_until IS NOT NULL
            """
        )
        conn.execute(
            """
            UPDATE users
            SET trial_used = 1
            WHERE subscription_plan IN ('basic', 'premium')
              AND COALESCE(trial_used, 0) = 0
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
            CREATE TABLE IF NOT EXISTS payment_intents (
                payload TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                created_at TEXT NOT NULL,
                paid_at TEXT,
                error_message TEXT,
                yookassa_payment_id TEXT,
                confirmation_url TEXT,
                notification_sent_at TEXT,
                subscription_until TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_threads (
                telegram_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'ai',
                last_message_at TEXT,
                escalated_at TEXT,
                closed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_admin_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
            """
        )
        _ensure_column(conn, "subscriptions", "invoice_payload", "TEXT")
        _ensure_column(conn, "subscriptions", "subscription_expires_at", "TEXT")
        _ensure_column(conn, "subscriptions", "is_recurring", "INTEGER DEFAULT 0")
        _ensure_column(conn, "subscriptions", "customer_email", "TEXT")
        _ensure_column(conn, "payment_intents", "error_message", "TEXT")
        _ensure_column(conn, "payment_intents", "yookassa_payment_id", "TEXT")
        _ensure_column(conn, "payment_intents", "confirmation_url", "TEXT")
        notification_column_added = _ensure_column(
            conn,
            "payment_intents",
            "notification_sent_at",
            "TEXT",
        )
        if notification_column_added:
            conn.execute(
                """
                UPDATE payment_intents
                SET notification_sent_at = COALESCE(paid_at, created_at)
                WHERE status = 'paid'
                """
            )
        _ensure_column(conn, "payment_intents", "subscription_until", "TEXT")
        conn.execute(
            """
            DELETE FROM subscriptions
            WHERE telegram_payment_charge_id IS NOT NULL
              AND telegram_payment_charge_id != ''
              AND id NOT IN (
                  SELECT MIN(id)
                  FROM subscriptions
                  WHERE telegram_payment_charge_id IS NOT NULL
                    AND telegram_payment_charge_id != ''
                  GROUP BY telegram_payment_charge_id
              )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_telegram_charge
            ON subscriptions(telegram_payment_charge_id)
            WHERE telegram_payment_charge_id IS NOT NULL
              AND telegram_payment_charge_id != ''
            """
        )

def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> bool:
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if column_name not in {column["name"] for column in columns}:
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )
        return True
    return False


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


def ensure_support_thread(telegram_id: int, status: str = "ai") -> None:
    """Create or reopen a support thread for a registered user."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO support_threads (
                telegram_id, status, last_message_at, closed_at
            )
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(telegram_id) DO UPDATE SET
                last_message_at = excluded.last_message_at,
                closed_at = NULL
            """,
            (telegram_id, status, _now()),
        )


def log_support_message(telegram_id: int, sender: str, message: str) -> None:
    """Persist one user, AI or admin support message."""
    ensure_support_thread(telegram_id)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO support_messages (
                telegram_id, sender, message, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, sender, message, _now()),
        )
        conn.execute(
            """
            UPDATE support_threads
            SET last_message_at = ?
            WHERE telegram_id = ?
            """,
            (_now(), telegram_id),
        )


def set_support_status(telegram_id: int, status: str) -> None:
    """Update support ownership: ai, admin or closed."""
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO support_threads (
                telegram_id, status, last_message_at, escalated_at, closed_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                status = excluded.status,
                last_message_at = excluded.last_message_at,
                escalated_at = CASE
                    WHEN excluded.status = 'admin' THEN excluded.last_message_at
                    ELSE support_threads.escalated_at
                END,
                closed_at = CASE
                    WHEN excluded.status = 'closed' THEN excluded.last_message_at
                    ELSE NULL
                END
            """,
            (
                telegram_id,
                status,
                now,
                now if status == "admin" else None,
                now if status == "closed" else None,
            ),
        )


def get_support_status(telegram_id: int) -> Optional[str]:
    """Return current support owner/status."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM support_threads WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return str(row["status"]) if row else None


def get_recent_support_messages(telegram_id: int, limit: int = 8) -> list[dict]:
    """Return recent support history in chronological order."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT sender, message
            FROM support_messages
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def remember_support_admin_message(
    chat_id: int,
    message_id: int,
    telegram_id: int,
) -> None:
    """Map an admin-chat message to the user whose ticket it represents."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO support_admin_messages (
                chat_id, message_id, telegram_id, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, message_id, telegram_id, _now()),
        )


def get_support_user_by_admin_message(
    chat_id: int,
    message_id: int,
) -> Optional[int]:
    """Resolve a reply in the support group to its Telegram user."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT telegram_id
            FROM support_admin_messages
            WHERE chat_id = ? AND message_id = ?
            """,
            (chat_id, message_id),
        ).fetchone()
    return int(row["telegram_id"]) if row else None


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
                norm_calories, norm_protein, norm_fat, norm_carbs,
                subscription_plan, trial_used, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'trial', 0, ?)
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


def save_payment_intent(
    payload: str,
    telegram_id: int,
    plan: str,
    amount: int,
    currency: str,
    customer_email: str,
    subscription_until: str | None = None,
) -> None:
    """Persist an invoice before its payment link is shown to the user."""
    if plan not in {"basic", "premium"}:
        raise ValueError("Unsupported subscription plan")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO payment_intents (
                payload, telegram_id, plan, amount, currency,
                customer_email, subscription_until, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?)
            """,
            (
                payload,
                telegram_id,
                plan,
                amount,
                currency,
                customer_email,
                subscription_until,
                _now(),
            ),
        )


def get_payment_intent(payload: str) -> Optional[dict]:
    """Return a pending or completed invoice by its unique payload."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM payment_intents WHERE payload = ?",
            (payload,),
        ).fetchone()
    return dict(row) if row else None


def get_pending_payment_intents(limit: int = 100) -> list[dict]:
    """Return YooKassa payments that still need automatic status checks."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_intents
            WHERE yookassa_payment_id IS NOT NULL
              AND yookassa_payment_id != ''
              AND status IN ('created', 'pending', 'waiting_for_capture')
            ORDER BY created_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_unnotified_paid_intents(limit: int = 100) -> list[dict]:
    """Return activated payments whose success message still needs delivery."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_intents
            WHERE status = 'paid'
              AND notification_sent_at IS NULL
            ORDER BY paid_at, created_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_payment_notification_sent(payload: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE payment_intents
            SET notification_sent_at = ?
            WHERE payload = ?
            """,
            (_now(), payload),
        )


def mark_payment_intent_failed(payload: str, error_message: str) -> None:
    """Store a sanitized provider error for payment diagnostics."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE payment_intents
            SET status = 'failed', error_message = ?
            WHERE payload = ?
            """,
            (error_message[:1000], payload),
        )


def update_payment_intent_from_yookassa(
    payload: str,
    yookassa_payment_id: str,
    status: str,
    confirmation_url: str,
) -> None:
    """Attach YooKassa identifiers and redirect URL to a local order."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE payment_intents
            SET yookassa_payment_id = ?, status = ?,
                confirmation_url = ?, error_message = NULL
            WHERE payload = ?
            """,
            (yookassa_payment_id, status, confirmation_url, payload),
        )


def mark_payment_intent_status(payload: str, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE payment_intents SET status = ? WHERE payload = ?",
            (status, payload),
        )


def get_recent_payment_intents(limit: int = 5) -> list[dict]:
    """Return recent invoice creation attempts for admin diagnostics."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                payload, telegram_id, plan, amount, currency,
                status, error_message, yookassa_payment_id,
                confirmation_url, created_at, paid_at
            FROM payment_intents
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_trial_used(telegram_id: int) -> bool:
    """Consume the one free photo analysis once for a Telegram user."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET trial_used = 1, updated_at = ?
            WHERE telegram_id = ? AND COALESCE(trial_used, 0) = 0
            """,
            (_now(), telegram_id),
        )
    return cursor.rowcount == 1


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
                subscription_plan, subscription_until, premium_until,
                trial_used, created_at
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


def get_period_stats(telegram_id: int, days: int | None = 7) -> list[dict]:
    """Return daily nutrition totals for a recent period or all recorded time."""
    params: list[object] = [telegram_id]
    date_filter = ""
    if days is not None:
        start_date = date.today() - timedelta(days=max(1, days) - 1)
        date_filter = "AND date >= ?"
        params.append(start_date.isoformat())

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                date,
                COUNT(*) AS meals_count,
                COALESCE(SUM(calories), 0) AS calories,
                COALESCE(SUM(protein), 0) AS protein,
                COALESCE(SUM(fat), 0) AS fat,
                COALESCE(SUM(carbs), 0) AS carbs
            FROM meals
            WHERE telegram_id = ? {date_filter}
            GROUP BY date
            ORDER BY date
            """,
            params,
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
              AND (
                  COALESCE(subscription_until, premium_until) IS NULL
                  OR COALESCE(subscription_until, premium_until) >= ?
              )
            """,
            (today,),
        ).fetchone()["count"]
        basic_users = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM users
            WHERE subscription_plan = 'basic'
              AND (subscription_until IS NULL OR subscription_until >= ?)
            """,
            (today,),
        ).fetchone()["count"]
        trial_users = conn.execute(
            "SELECT COUNT(*) AS count FROM users WHERE subscription_plan = 'trial'"
        ).fetchone()["count"]
        payments = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(amount), 0) AS amount
            FROM subscriptions
            """
        ).fetchone()
        payment_totals = conn.execute(
            """
            SELECT currency, COALESCE(SUM(amount), 0) AS amount
            FROM subscriptions
            GROUP BY currency
            ORDER BY currency
            """
        ).fetchall()
        open_support_threads = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM support_threads
            WHERE status IN ('ai', 'admin')
            """
        ).fetchone()["count"]
        escalated_support_threads = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM support_threads
            WHERE status = 'admin'
            """
        ).fetchone()["count"]

    return {
        "users_count": int(users_count),
        "meals_count": int(meals_count),
        "meals_today": int(meals_today),
        "active_today": int(active_today),
        "premium_users": int(premium_users),
        "basic_users": int(basic_users),
        "trial_users": int(trial_users),
        "open_support_threads": int(open_support_threads),
        "escalated_support_threads": int(escalated_support_threads),
        "payments_count": int(payments["count"]),
        "payments_amount": int(payments["amount"]),
        "payment_totals": {
            str(row["currency"]): int(row["amount"]) for row in payment_totals
        },
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
        "payment_totals": stats["payment_totals"],
        "premium_users": stats["premium_users"],
        "basic_users": stats["basic_users"],
        "trial_users": stats["trial_users"],
        "open_support_threads": stats["open_support_threads"],
        "escalated_support_threads": stats["escalated_support_threads"],
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
    subscription_until: str | None = None,
) -> None:
    """Update user's subscription plan."""
    premium_until = subscription_until if plan == "premium" else None
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET subscription_plan = ?, subscription_until = ?,
                premium_until = ?,
                trial_used = CASE WHEN ? IN ('basic', 'premium') THEN 1 ELSE trial_used END,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (plan, subscription_until, premium_until, plan, _now(), telegram_id),
        )


def activate_subscription_payment(
    telegram_id: int,
    plan: str,
    amount: int,
    currency: str,
    provider_payment_charge_id: str,
    telegram_payment_charge_id: str,
    invoice_payload: str,
    subscription_until: str,
    is_recurring: bool = False,
    customer_email: str | None = None,
) -> bool:
    """Atomically store a new payment and activate the selected plan."""
    if plan not in {"basic", "premium"}:
        raise ValueError("Unsupported subscription plan")
    premium_until = subscription_until if plan == "premium" else None
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO subscriptions (
                telegram_id, plan, amount, currency,
                provider_payment_charge_id, telegram_payment_charge_id,
                invoice_payload, subscription_expires_at, is_recurring,
                customer_email, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                plan,
                amount,
                currency,
                provider_payment_charge_id,
                telegram_payment_charge_id,
                invoice_payload,
                subscription_until,
                int(is_recurring),
                customer_email,
                _now(),
            ),
        )
        if cursor.rowcount != 1:
            return False
        conn.execute(
            """
            UPDATE users
            SET subscription_plan = ?, subscription_until = ?,
                premium_until = ?, trial_used = 1, updated_at = ?
            WHERE telegram_id = ?
            """,
            (plan, subscription_until, premium_until, _now(), telegram_id),
        )
        conn.execute(
            """
            UPDATE payment_intents
            SET status = 'paid', paid_at = ?
            WHERE payload = ?
            """,
            (_now(), invoice_payload),
        )
    return True


def get_recent_payments(limit: int = 10) -> list[dict]:
    """Return recent subscription payments."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                telegram_id, plan, amount, currency,
                telegram_payment_charge_id, subscription_expires_at,
                is_recurring, created_at
            FROM subscriptions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


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
