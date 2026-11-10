"""Provider-neutral interfaces for recording externally initiated payments.

Only the manual cash and bank-transfer provider exists in phase 1. Paystack and
virtual-account integrations are intentionally left as extension points.
"""

from __future__ import annotations

from typing import Protocol


class PaymentProvider(Protocol):
    """Validate and normalize the reference for a supported payment channel."""

    @property
    def provider_code(self) -> str: ...

    def normalize_reference(self, channel: str, reference: str | None) -> str | None: ...


class ManualProvider:
    """Manual staff-entered cash and bank-transfer payment references."""

    provider_code = "manual"
    _SUPPORTED_CHANNELS = frozenset({"cash", "bank_transfer"})

    def normalize_reference(self, channel: str, reference: str | None) -> str | None:
        if channel not in self._SUPPORTED_CHANNELS:
            raise ValueError("Manual provider supports cash and bank transfers only")
        normalized = (reference or "").strip()
        if channel == "cash":
            return normalized or None
        if not normalized:
            raise ValueError("Bank transfer reference is required")
        if len(normalized) > 160:
            raise ValueError("Bank transfer reference is too long")
        return normalized


MANUAL_PROVIDER: PaymentProvider = ManualProvider()
