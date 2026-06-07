import sys
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

    def test_payment_is_idempotent_and_activates_premium(self) -> None:
        first = database.activate_premium_payment(
            telegram_id=1001,
            amount=450,
            currency="XTR",
            provider_payment_charge_id="provider-1",
            telegram_payment_charge_id="telegram-1",
            invoice_payload="dietnik_premium_30",
            premium_until="2026-07-07",
        )
        second = database.activate_premium_payment(
            telegram_id=1001,
            amount=450,
            currency="XTR",
            provider_payment_charge_id="provider-1",
            telegram_payment_charge_id="telegram-1",
            invoice_payload="dietnik_premium_30",
            premium_until="2026-08-06",
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(database.get_user(1001)["premium_until"], "2026-07-07")
        self.assertEqual(database.get_admin_stats()["payments_count"], 1)
        self.assertEqual(database.get_admin_stats()["payment_totals"], {"XTR": 450})


if __name__ == "__main__":
    unittest.main()
