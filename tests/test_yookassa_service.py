import asyncio
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


PROJECT_DIR = Path(__file__).resolve().parents[1] / "dietnik_bot"
sys.path.insert(0, str(PROJECT_DIR))

import yookassa_service  # noqa: E402
import main  # noqa: E402


class YooKassaServiceTests(unittest.TestCase):
    def test_create_payment_sends_redirect_receipt_and_metadata(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "id": "yk-1",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yookassa.test/pay"},
        }

        with (
            patch.object(yookassa_service, "YOOKASSA_SHOP_ID", "shop"),
            patch.object(yookassa_service, "YOOKASSA_SECRET_KEY", "secret"),
            patch.object(yookassa_service.requests, "post", return_value=response) as post,
        ):
            result = yookassa_service.create_payment(
                "dietnik:basic:30:1001:test",
                1001,
                "basic",
                "Dietnik Basic на 30 дней",
                49000,
                "user@example.com",
            )

        self.assertEqual(result["id"], "yk-1")
        request = post.call_args
        self.assertEqual(request.kwargs["auth"], ("shop", "secret"))
        self.assertEqual(
            request.kwargs["headers"]["Idempotence-Key"],
            "dietnik:basic:30:1001:test",
        )
        payload = request.kwargs["json"]
        self.assertEqual(payload["amount"], {"value": "490.00", "currency": "RUB"})
        self.assertEqual(payload["metadata"]["telegram_id"], "1001")
        self.assertEqual(payload["metadata"]["plan"], "basic")
        self.assertEqual(
            payload["receipt"]["customer"]["email"],
            "user@example.com",
        )

    def test_direct_payment_validation_checks_amount_and_owner(self) -> None:
        intent = {
            "payload": "dietnik:basic:30:1001:test",
            "telegram_id": 1001,
            "plan": "basic",
            "amount": 49000,
            "currency": "RUB",
            "yookassa_payment_id": "yk-1",
        }
        payment = {
            "id": "yk-1",
            "status": "succeeded",
            "paid": True,
            "amount": {"value": "490.00", "currency": "RUB"},
            "metadata": {
                "local_payment_id": intent["payload"],
                "telegram_id": "1001",
                "plan": "basic",
            },
        }

        self.assertIsNone(main._validate_direct_payment(intent, payment))
        payment["amount"]["value"] = "890.00"
        self.assertEqual(
            main._validate_direct_payment(intent, payment),
            "amount_mismatch",
        )

    def test_successful_payment_is_activated_and_user_is_notified(self) -> None:
        intent = {
            "payload": "dietnik:basic:30:1001:auto",
            "telegram_id": 1001,
            "plan": "basic",
            "amount": 49000,
            "currency": "RUB",
            "customer_email": "user@example.com",
            "yookassa_payment_id": "yk-auto-1",
        }
        payment = {
            "id": "yk-auto-1",
            "status": "succeeded",
            "paid": True,
            "amount": {"value": "490.00", "currency": "RUB"},
            "metadata": {
                "local_payment_id": intent["payload"],
                "telegram_id": "1001",
                "plan": "basic",
            },
        }
        bot = AsyncMock()
        user = {
            "telegram_id": 1001,
            "subscription_until": None,
            "premium_until": None,
        }

        with (
            patch.object(main, "get_user", return_value=user),
            patch.object(main, "activate_subscription_payment", return_value=True),
            patch.object(main, "mark_payment_notification_sent") as mark_notified,
        ):
            asyncio.run(main._activate_direct_payment(bot, intent, payment))

        bot.send_message.assert_awaited_once()
        mark_notified.assert_called_once_with(intent["payload"])
        args = bot.send_message.await_args.args
        self.assertEqual(args[0], 1001)
        self.assertIn("Оплата прошла успешно", args[1])
        self.assertIn("Basic", args[1])

    def test_basic_user_gets_prorated_premium_upgrade(self) -> None:
        expiry = date.today() + timedelta(days=15)
        user = {
            "subscription_plan": "basic",
            "subscription_until": expiry.isoformat(),
            "premium_until": None,
        }

        upgrade = main._premium_upgrade_details(user)
        offer = main._checkout_offer(user, "premium")
        with patch.object(main, "is_configured", return_value=True):
            markup = main._subscription_markup(user, premium_only=True)
        buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
        ]

        expected_price = (
            (main.PREMIUM_PRICE_RUB - main.BASIC_PRICE_RUB) * 15 + 29
        ) // 30
        self.assertEqual(upgrade["price"], expected_price)
        self.assertEqual(offer["price"], expected_price)
        self.assertEqual(offer["subscription_until"], expiry.isoformat())
        self.assertTrue(offer["is_upgrade"])
        self.assertFalse(any(button.callback_data == "buy_basic" for button in buttons))
        self.assertTrue(any(button.callback_data == "buy_premium" for button in buttons))
        self.assertTrue(
            any(str(expected_price) in button.text for button in buttons)
        )

    def test_premium_upgrade_never_exceeds_full_price_difference(self) -> None:
        expiry = date.today() + timedelta(days=60)
        user = {
            "subscription_plan": "basic",
            "subscription_until": expiry.isoformat(),
            "premium_until": None,
        }

        upgrade = main._premium_upgrade_details(user)

        self.assertEqual(
            upgrade["price"],
            main.PREMIUM_PRICE_RUB - main.BASIC_PRICE_RUB,
        )
        self.assertEqual(upgrade["days"], 60)
        self.assertEqual(upgrade["billable_days"], 30)
        self.assertEqual(upgrade["subscription_until"], expiry.isoformat())


if __name__ == "__main__":
    unittest.main()
