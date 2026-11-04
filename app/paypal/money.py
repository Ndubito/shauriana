"""Money helpers. Amounts are Decimals in code and 2-dp strings on the wire."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_decimal(value: Decimal | str | int | float) -> Decimal:
    # str() first so floats like 0.11 don't carry binary noise.
    return Decimal(str(value))


def fmt(value: Decimal | str | int | float) -> str:
    return str(to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))
