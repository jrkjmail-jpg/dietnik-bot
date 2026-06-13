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
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@bothostru")
BOT_RELEASE = os.getenv("BOT_RELEASE", "2026.06.13-single-month-1")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
YOOKASSA_RETURN_URL = os.getenv("YOOKASSA_RETURN_URL", "https://t.me/").strip()
YOOKASSA_TAX_SYSTEM_CODE = os.getenv("YOOKASSA_TAX_SYSTEM_CODE", "").strip()
YOOKASSA_PAYMENT_MODE = os.getenv("YOOKASSA_PAYMENT_MODE", "full_prepayment").strip()


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


_AUTO_DB_BACKUP_ENABLED = _get_int_env("AUTO_DB_BACKUP_INTERVAL_HOURS", 24) > 0
AUTO_DB_BACKUP_INTERVAL_HOURS = 24 if _AUTO_DB_BACKUP_ENABLED else 0
PAYMENT_CHECK_INTERVAL_SECONDS = max(
    5,
    _get_int_env("PAYMENT_CHECK_INTERVAL_SECONDS", 10),
)
BASIC_PRICE_RUB = max(1, _get_int_env("BASIC_PRICE_RUB", 490))
PREMIUM_PRICE_RUB = max(1, _get_int_env("PREMIUM_PRICE_RUB", 890))
YOOKASSA_VAT_CODE = min(6, max(1, _get_int_env("YOOKASSA_VAT_CODE", 1)))
YOOKASSA_TEST_MODE = _get_bool_env("YOOKASSA_TEST_MODE", False)
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
