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
        database.save_payment_intent(
            payload="dietnik:basic:30:1001:test",
            telegram_id=1001,
            plan="basic",
            amount=49000,
            currency="RUB",
            customer_email="user@example.com",
        )
        database.mark_payment_intent_failed(
            "dietnik:basic:30:1001:test",
            "TelegramBadRequest: PAYMENT_PROVIDER_INVALID",
        )
        failed_intent = database.get_payment_intent(
            "dietnik:basic:30:1001:test"
        )
        self.assertEqual(failed_intent["status"], "failed")
        self.assertIn("PAYMENT_PROVIDER_INVALID", failed_intent["error_message"])

        database.save_payment_intent(
            payload="dietnik:basic:30:1001:paid",
            telegram_id=1001,
            plan="basic",
            amount=49000,
            currency="RUB",
            customer_email="user@example.com",
            subscription_until="2026-07-07",
        )
        database.update_payment_intent_from_yookassa(
            "dietnik:basic:30:1001:paid",
            "yk-payment-1",
            "pending",
            "https://yookassa.test/pay",
        )
        pending_intent = database.get_payment_intent(
            "dietnik:basic:30:1001:paid"
        )
        self.assertEqual(pending_intent["yookassa_payment_id"], "yk-payment-1")
        self.assertEqual(pending_intent["status"], "pending")
        self.assertEqual(pending_intent["subscription_until"], "2026-07-07")
        pending = database.get_pending_payment_intents()
        self.assertEqual([item["payload"] for item in pending], [
            "dietnik:basic:30:1001:paid"
        ])
        first = database.activate_subscription_payment(
            telegram_id=1001,
            plan="basic",
            amount=49000,
            currency="RUB",
            provider_payment_charge_id="provider-1",
            telegram_payment_charge_id="telegram-1",
            invoice_payload="dietnik:basic:30:1001:paid",
            subscription_until="2026-07-07",
            customer_email="user@example.com",
        )
        second = database.activate_subscription_payment(
            telegram_id=1001,
            plan="basic",
            amount=49000,
            currency="RUB",
            provider_payment_charge_id="provider-1",
            telegram_payment_charge_id="telegram-1",
            invoice_payload="dietnik:basic:30:1001:paid",
            subscription_until="2026-08-06",
            customer_email="user@example.com",
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(database.get_user(1001)["subscription_plan"], "basic")
        self.assertEqual(database.get_user(1001)["subscription_until"], "2026-07-07")
        self.assertIsNone(database.get_user(1001)["premium_until"])
        self.assertEqual(database.get_admin_stats()["payments_count"], 1)
        self.assertEqual(database.get_admin_stats()["payment_totals"], {"RUB": 49000})
        intent = database.get_payment_intent("dietnik:basic:30:1001:paid")
        self.assertEqual(intent["status"], "paid")
        self.assertEqual(intent["customer_email"], "user@example.com")
        self.assertEqual(database.get_pending_payment_intents(), [])
        self.assertEqual(
            [item["payload"] for item in database.get_unnotified_paid_intents()],
            ["dietnik:basic:30:1001:paid"],
        )
        database.mark_payment_notification_sent(intent["payload"])
        self.assertEqual(database.get_unnotified_paid_intents(), [])
        attempts = database.get_recent_payment_intents()
        self.assertEqual(
            {attempt["status"] for attempt in attempts},
            {"failed", "paid"},
        )

        with sqlite3.connect(database.DB_PATH) as conn:
            email = conn.execute(
                "SELECT customer_email FROM subscriptions"
            ).fetchone()[0]
        self.assertEqual(email, "user@example.com")

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

    def test_support_history_status_and_admin_mapping(self) -> None:
        database.ensure_support_thread(1001)
        database.log_support_message(1001, "user", "Не активировалась подписка")
        database.log_support_message(1001, "ai", "Передаю вопрос администратору")
        database.set_support_status(1001, "admin")
        database.remember_support_admin_message(-100500, 77, 1001)
        database.remember_support_admin_message(-100500, 78, 1001)

        history = database.get_recent_support_messages(1001)
        self.assertEqual(
            history,
            [
                {"sender": "user", "message": "Не активировалась подписка"},
                {"sender": "ai", "message": "Передаю вопрос администратору"},
            ],
        )
        self.assertEqual(database.get_support_status(1001), "admin")
        self.assertEqual(
            database.get_support_user_by_admin_message(-100500, 77),
            1001,
        )
        self.assertEqual(
            database.get_support_user_by_admin_message(-100500, 78),
            1001,
        )
        stats = database.get_admin_stats()
        self.assertEqual(stats["open_support_threads"], 1)
        self.assertEqual(stats["escalated_support_threads"], 1)


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
