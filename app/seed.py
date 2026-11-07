"""Load seed data and demo scenarios into the database."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app import db
from app.envsubst import substitute

SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "seeds.json"


def load_seed_file(path: Path = SEED_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return substitute(json.load(f))


def reset(conn: sqlite3.Connection, seed: dict) -> None:
    """Wipe every table and restore inventory and suppliers from the seed."""
    conn.execute("BEGIN")
    try:
        db.wipe(conn)
        for row in seed["inventory"]:
            conn.execute(
                "INSERT INTO inventory VALUES (:item, :unit, :stock, :reorder_level, :reorder_qty,"
                " :max_unit_price, :latest_delivery_days, :last_paid_unit_price)",
                row,
            )
        for row in seed["suppliers"]:
            conn.execute(
                "INSERT INTO suppliers VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], row["name"], row["paypal_email"], json.dumps(row["floor_prices"]),
                 row["max_qty"], row["lead_days"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def apply_scenario(conn: sqlite3.Connection, seed: dict, name: str) -> dict:
    """Reset, then apply the scenario's stock changes. Returns the scenario definition."""
    scenarios = seed.get("scenarios", {})
    if name not in scenarios:
        raise KeyError(f"Unknown scenario {name!r}; known: {sorted(scenarios)}")
    scenario = scenarios[name]
    reset(conn, seed)
    for item, stock in scenario.get("set_stock", {}).items():
        conn.execute("UPDATE inventory SET stock = ? WHERE item = ?", (stock, item))
    for item, price in scenario.get("set_last_paid", {}).items():
        conn.execute("UPDATE inventory SET last_paid_unit_price = ? WHERE item = ?", (price, item))
    return scenario
