"""Direct YooKassa API integration for subscription payments."""

from decimal import Decimal

import requests

from config import (
    YOOKASSA_PAYMENT_MODE,
    YOOKASSA_RETURN_URL,
    YOOKASSA_SECRET_KEY,
    YOOKASSA_SHOP_ID,
    YOOKASSA_TAX_SYSTEM_CODE,
    YOOKASSA_TEST_MODE,
    YOOKASSA_VAT_CODE,
)


BASE_URL = "https://api.yookassa.ru/v3"


class YooKassaError(RuntimeError):
    """A safe API error with enough detail for admin diagnostics."""


def is_configured() -> bool:
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)


def _amount_value(amount_kopecks: int) -> str:
    return f"{Decimal(amount_kopecks) / 100:.2f}"


def _raise_for_status(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            details = response.json()
        except ValueError:
            details = response.text[:1000]
        raise YooKassaError(
            f"HTTP {response.status_code}: {details}"
        ) from exc


def create_payment(
    local_payment_id: str,
    telegram_id: int,
    plan: str,
    title: str,
    amount_kopecks: int,
    customer_email: str,
) -> dict:
    if not is_configured():
        raise YooKassaError("YOOKASSA_SHOP_ID или YOOKASSA_SECRET_KEY не заданы")

    amount_value = _amount_value(amount_kopecks)
    payload = {
        "amount": {"value": amount_value, "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": YOOKASSA_RETURN_URL,
        },
        "description": title,
        "metadata": {
            "local_payment_id": local_payment_id,
            "telegram_id": str(telegram_id),
            "plan": plan,
            "test_mode": "true" if YOOKASSA_TEST_MODE else "false",
        },
        "receipt": {
            "customer": {"email": customer_email},
            "items": [
                {
                    "description": title,
                    "quantity": "1.00",
                    "amount": {"value": amount_value, "currency": "RUB"},
                    "vat_code": YOOKASSA_VAT_CODE,
                    "payment_subject": "service",
                    "payment_mode": YOOKASSA_PAYMENT_MODE,
                }
            ],
        },
    }
    if YOOKASSA_TAX_SYSTEM_CODE:
        payload["receipt"]["tax_system_code"] = int(YOOKASSA_TAX_SYSTEM_CODE)

    response = requests.post(
        f"{BASE_URL}/payments",
        auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
        headers={
            "Content-Type": "application/json",
            "Idempotence-Key": local_payment_id,
        },
        json=payload,
        timeout=60,
    )
    _raise_for_status(response)
    result = response.json()
    confirmation_url = (result.get("confirmation") or {}).get("confirmation_url")
    if not result.get("id") or not confirmation_url:
        raise YooKassaError("ЮKassa не вернула ID или ссылку на оплату")
    return result


def get_payment(yookassa_payment_id: str) -> dict:
    if not is_configured():
        raise YooKassaError("YOOKASSA_SHOP_ID или YOOKASSA_SECRET_KEY не заданы")
    response = requests.get(
        f"{BASE_URL}/payments/{yookassa_payment_id}",
        auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
        timeout=60,
    )
    _raise_for_status(response)
    return response.json()
