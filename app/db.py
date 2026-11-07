"""SQLite storage. Eight tables from the spec; money is stored as TEXT decimals."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    item                 TEXT PRIMARY KEY,
    unit                 TEXT NOT NULL,
    stock                INTEGER NOT NULL,
    reorder_level        INTEGER NOT NULL,
    reorder_qty          INTEGER NOT NULL,
    max_unit_price       TEXT NOT NULL,
    latest_delivery_days INTEGER NOT NULL,
    last_paid_unit_price TEXT
);

CREATE TABLE IF NOT EXISTS suppliers (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    paypal_email TEXT NOT NULL,
    list_prices  TEXT NOT NULL,      -- JSON {item: price}; where the supplier opens
    floor_prices TEXT NOT NULL,      -- JSON {item: price}; the lowest it will go
    max_qty      INTEGER NOT NULL,
    lead_days    INTEGER NOT NULL
);

-- outcome: started, deal, no_deal, held, paid, blocked, denied, failed
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    item        TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    outcome     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    round         INTEGER NOT NULL,
    from_side     TEXT NOT NULL,     -- "shop" | "supplier"
    action        TEXT NOT NULL,     -- request | quote | counter | accept | walk_away
    item          TEXT NOT NULL,
    qty           INTEGER,
    unit_price    TEXT,
    delivery_date TEXT,
    rationale     TEXT,
    valid         INTEGER NOT NULL DEFAULT 1,
    reason        TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    item           TEXT NOT NULL,
    qty            INTEGER NOT NULL,
    unit_price     TEXT NOT NULL,
    total          TEXT NOT NULL,
    currency       TEXT NOT NULL,
    delivery_date  TEXT NOT NULL,
    payee_email    TEXT NOT NULL,
    rationale      TEXT,
    invoice_id     TEXT,
    invoice_total  TEXT,
    invoice_payee  TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id        TEXT NOT NULL,
    status         TEXT NOT NULL,    -- approved | needs_approval | blocked
    rule           TEXT,
    reason         TEXT NOT NULL,
    checks         TEXT NOT NULL,    -- JSON list of {rule, passed}
    policy_version TEXT NOT NULL,
    rules_in_force TEXT NOT NULL,    -- JSON snapshot of the rules used
    created_at     TEXT NOT NULL
);

-- status: pending, approved, denied, expired
CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id     TEXT NOT NULL,
    decision_id INTEGER NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    decided_at  TEXT
);

-- status: CREATED (row written before PayPal is called), then PayPal's item status
-- (PENDING, SUCCESS, UNCLAIMED, ...) or FAILED if the call errored.
CREATE TABLE IF NOT EXISTS payments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id            TEXT NOT NULL,
    amount             TEXT NOT NULL,
    payout_batch_id    TEXT,
    payout_item_id     TEXT,
    invoice_payment_id TEXT,
    status             TEXT NOT NULL,
    error              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
"""

TABLES = ["payments", "approvals", "decisions", "deals", "offers", "runs", "suppliers", "inventory"]


def db_path_from_env() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./data.db")
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"Only sqlite DATABASE_URL is supported, got {url!r}")
    return url[len(prefix):]


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or db_path_from_env()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; use explicit BEGIN
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def wipe(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")
