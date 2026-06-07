"""Project configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_PATH_EXPLICIT = bool(os.getenv("DB_PATH"))
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "bot.db"))
PERSISTENCE_PATH = Path(os.getenv("PERSISTENCE_PATH", DATA_DIR / "bot_state.pkl"))
LEGACY_DB_PATH = BASE_DIR / "dietnik.sqlite3"

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bothostru")


def _get_int_env(name: str, default: int) -> int:
    """Read integer environment values without crashing on a bad deploy variable."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


AUTO_DB_BACKUP_INTERVAL_HOURS = _get_int_env("AUTO_DB_BACKUP_INTERVAL_HOURS", 6)
PREMIUM_PRICE_RUB = max(1, _get_int_env("PREMIUM_PRICE_RUB", 890))
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = {
    int(admin_id.strip())
    for admin_id in ADMIN_IDS_RAW.split(",")
    if admin_id.strip().isdigit()
}


def validate_config() -> None:
    """Fail fast when required secrets are missing."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")

    if missing:
        names = ", ".join(missing)
        raise RuntimeError(
            f"Не найдены переменные окружения: {names}. "
            "Создайте файл .env по примеру .env.example."
        )
