"""Held payments waiting for the owner.

Approve/deny actions arrive with the approval-queue feature; this module creates
pending approvals and expires the ones the owner never answered.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from app.models import iso, utcnow


def create_pending(conn: sqlite3.Connection, deal_id: str, decision_id: int,
                   timeout_hours: int, now: datetime | None = None) -> int:
    now = now or utcnow()
    cur = conn.execute(
        "INSERT INTO approvals (deal_id, decision_id, status, created_at, expires_at)"
        " VALUES (?, ?, 'pending', ?, ?)",
        (deal_id, decision_id, iso(now), iso(now + timedelta(hours=timeout_hours))),
    )
    return cur.lastrowid


def expire_stale(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """Mark unanswered approvals past their deadline as expired; their runs become denied.

    Returns the affected deal IDs.
    """
    now_s = iso(now or utcnow())
    rows = conn.execute(
        "SELECT a.id, a.deal_id, d.run_id FROM approvals a JOIN deals d ON d.id = a.deal_id"
        " WHERE a.status = 'pending' AND a.expires_at <= ?",
        (now_s,),
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE approvals SET status = 'expired', decided_at = ? WHERE id = ?", (now_s, r["id"]))
        conn.execute("UPDATE runs SET outcome = 'denied' WHERE id = ?", (r["run_id"],))
    return [r["deal_id"] for r in rows]
