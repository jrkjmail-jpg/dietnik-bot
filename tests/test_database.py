import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1] / "dietnik_bot"
sys.path.insert(0, str(PROJECT_DIR))

import database  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name) / "data"
        database.DATA_DIR = data_dir
        database.DB_PATH = data_dir / "bot.db"
        database.STORAGE_PROBE_PATH = data_dir / "storage_probe.txt"
        database.LEGACY_DB_PATH = Path(self.temp_dir.name) / "missing.sqlite3"
        database.init_db()
        database.save_user(
            telegram_id=1001,
            gender="Мужской",
            age=30,
            height=180,
            weight=80,
            activity="Средняя",
            goal="Поддержание",
            norm_calories=2400,
            norm_protein=144,
            norm_fat=72,
            norm_carbs=294,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_period_report_contains_meal_count(self) -> None:
        database.save_meal(1001, "Омлет", 400, 30, 20, 15, "Хороший завтрак")
        database.save_meal(1001, "Салат", 250, 10, 12, 25, "Добавь белок")

        rows = database.get_period_stats(1001, 7)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["meals_count"], 2)
        self.assertEqual(rows[0]["calories"], 650)
        self.assertEqual(database.get_period_stats(1001, None), rows)

    def test_new_user_starts_with_one_trial(self) -> None:
        user = database.get_user(1001)
        self.assertEqual(user["subscription_plan"], "trial")
        self.assertEqual(user["trial_used"], 0)

        first_use = database.mark_trial_used(1001)
        second_use = database.mark_trial_used(1001)

        self.assertTrue(first_use)
        self.assertFalse(second_use)
        self.assertEqual(database.get_user(1001)["trial_used"], 1)

    def test_payment_is_idempotent_and_activates_basic(self) -> None:
        first = database.activate_subscription_payment(
            telegram_id=1001,
            plan="basic",
            amount=49000,
            currency="RUB",
            provider_payment_charge_id="provider-1",
            telegram_payment_charge_id="telegram-1",
            invoice_payload="dietnik_basic_30_rub",
            subscription_until="2026-07-07",
        )
        second = database.activate_subscription_payment(
            telegram_id=1001,
            plan="basic",
            amount=49000,
            currency="RUB",
            provider_payment_charge_id="provider-1",
            telegram_payment_charge_id="telegram-1",
            invoice_payload="dietnik_basic_30_rub",
            subscription_until="2026-08-06",
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(database.get_user(1001)["subscription_plan"], "basic")
        self.assertEqual(database.get_user(1001)["subscription_until"], "2026-07-07")
        self.assertIsNone(database.get_user(1001)["premium_until"])
        self.assertEqual(database.get_admin_stats()["payments_count"], 1)
        self.assertEqual(database.get_admin_stats()["payment_totals"], {"RUB": 49000})

    def test_premium_payment_sets_legacy_and_generic_expiry(self) -> None:
        inserted = database.activate_subscription_payment(
            telegram_id=1001,
            plan="premium",
            amount=89000,
            currency="RUB",
            provider_payment_charge_id="provider-premium",
            telegram_payment_charge_id="telegram-premium",
            invoice_payload="dietnik_premium_30_rub",
            subscription_until="2026-07-07",
        )

        user = database.get_user(1001)
        self.assertTrue(inserted)
        self.assertEqual(user["subscription_plan"], "premium")
        self.assertEqual(user["subscription_until"], "2026-07-07")
        self.assertEqual(user["premium_until"], "2026-07-07")


class LegacyMigrationTests(unittest.TestCase):
    def test_existing_user_keeps_legacy_basic_access_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir()
            db_path = data_dir / "bot.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE users (
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
                    INSERT INTO users VALUES (
                        2002, 'Женский', 28, 168, 62, 'Средняя', 'Поддержание',
                        2000, 112, 56, 262, '2026-01-01T12:00:00'
                    )
                    """
                )

            database.DATA_DIR = data_dir
            database.DB_PATH = db_path
            database.STORAGE_PROBE_PATH = data_dir / "storage_probe.txt"
            database.LEGACY_DB_PATH = Path(temp_dir) / "missing.sqlite3"
            database.init_db()

            user = database.get_user(2002)
            self.assertEqual(user["subscription_plan"], "basic")
            self.assertIsNone(user["subscription_until"])
            self.assertEqual(user["trial_used"], 1)


if __name__ == "__main__":
    unittest.main()
