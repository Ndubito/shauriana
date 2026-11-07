"""The owner's rules file, loaded read-only. Numbers become Decimals; ${VAR}s come from env."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.envsubst import substitute

RULES_FILE = Path(__file__).resolve().parent / "rules.json"


@dataclass(frozen=True)
class Rules:
    version: str
    currency: str
    approved_payees: tuple[str, ...]  # lower-cased
    max_per_order: Decimal
    max_per_week: Decimal
    require_approval_above: Decimal
    max_unit_price_increase_pct: Decimal
    approval_timeout_hours: int

    def snapshot(self) -> dict:
        """The rules in force, as recorded on every decision (JSON-safe)."""
        return {
            "currency": self.currency,
            "approved_payees": list(self.approved_payees),
            "max_per_order": float(self.max_per_order),
            "max_per_week": float(self.max_per_week),
            "require_approval_above": float(self.require_approval_above),
            "max_unit_price_increase_pct": float(self.max_unit_price_increase_pct),
            "approval_timeout_hours": self.approval_timeout_hours,
        }


def from_dict(raw: dict) -> Rules:
    raw = substitute(raw)
    d = lambda key: Decimal(str(raw[key]))  # noqa: E731
    return Rules(
        version=str(raw["version"]),
        currency=raw["currency"],
        approved_payees=tuple(p.strip().lower() for p in raw["approved_payees"]),
        max_per_order=d("max_per_order"),
        max_per_week=d("max_per_week"),
        require_approval_above=d("require_approval_above"),
        max_unit_price_increase_pct=d("max_unit_price_increase_pct"),
        approval_timeout_hours=int(raw["approval_timeout_hours"]),
    )


def load_rules(path: Path = RULES_FILE) -> Rules:
    with open(path, encoding="utf-8") as f:
        # Parse floats as Decimal so 150 and 150.00 compare exactly.
        return from_dict(json.load(f, parse_float=Decimal))
