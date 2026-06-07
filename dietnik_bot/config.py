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


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().casefold() in {"1", "true", "yes", "да", "on"}


AUTO_DB_BACKUP_INTERVAL_HOURS = _get_int_env("AUTO_DB_BACKUP_INTERVAL_HOURS", 6)
BASIC_PRICE_RUB = max(1, _get_int_env("BASIC_PRICE_RUB", 490))
PREMIUM_PRICE_RUB = max(1, _get_int_env("PREMIUM_PRICE_RUB", 890))
SUPPORT_AI_ENABLED = _get_bool_env("SUPPORT_AI_ENABLED", True)
SUPPORT_AI_MODEL = os.getenv("SUPPORT_AI_MODEL", "gpt-4o-mini")
SUPPORT_MESSAGE_MAX_CHARS = max(200, _get_int_env("SUPPORT_MESSAGE_MAX_CHARS", 1500))
SUPPORT_ATTACHMENT_MAX_FILE_BYTES = max(
    1024,
    _get_int_env("SUPPORT_ATTACHMENT_MAX_FILE_BYTES", 10 * 1024 * 1024),
)
SUPPORT_ADMIN_CHAT_ID_RAW = os.getenv("SUPPORT_ADMIN_CHAT_ID", "").strip()
SUPPORT_ADMIN_CHAT_ID = (
    int(SUPPORT_ADMIN_CHAT_ID_RAW)
    if SUPPORT_ADMIN_CHAT_ID_RAW.lstrip("-").isdigit()
    else None
)
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
