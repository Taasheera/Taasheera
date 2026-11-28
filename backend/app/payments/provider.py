"""Who collects a payment. Only the simulated provider exists; a real one implements the same `charge`."""

import secrets
from typing import Protocol

from ..models import Payment


class PaymentFailed(Exception):
    """The provider refused the payment. Nothing was collected."""


class PaymentProvider(Protocol):
    name: str

    def charge(self, payment: Payment) -> str:
        """Collect `payment.amount_minor` in `payment.currency` and return the provider's reference."""
        ...


class SimulatedProvider:
    """Test mode: every payment succeeds and nothing is charged. It never sees card details."""

    name = "simulated"

    def charge(self, payment: Payment) -> str:
        return f"sim_{secrets.token_hex(12)}"
