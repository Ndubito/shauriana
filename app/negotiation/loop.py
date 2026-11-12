"""The negotiation loop: plain code that passes offers between the agents.

Round 1: the shop's request (built by code) and the supplier's quote.
Rounds 2..max_rounds: the shop moves, then the supplier moves.
The final round (when there are 3+ rounds) allows only accept or walk_away.
An accept creates the deal from the other side's last valid offer. No accept by the
end of the last round, a walk_away, a second invalid move by one side, or a model
failure ends the run as no deal.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from app.agents.negotiator import Agent, Move, TurnView
from app.llm import LLMError
from app.models import iso, utcnow
from app.negotiation.offers import (PROPOSALS, SHOP, SUPPLIER, Offer, ShopLimits, SupplierLimits,
                                    check_terms, to_price)


@dataclass(frozen=True)
class AgreedTerms:
    qty: int
    unit_price: object  # Decimal
    delivery_date: str
    accepted_by: str
    rationale: str


@dataclass(frozen=True)
class NoDeal:
    reason: str


def _save(conn: sqlite3.Connection, o: Offer, valid: bool, reason: str | None) -> None:
    conn.execute(
        "INSERT INTO offers (run_id, round, from_side, action, item, qty, unit_price, delivery_date,"
        " rationale, valid, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (o.run_id, o.round, o.from_side, o.action, o.item, o.qty,
         str(o.unit_price) if o.unit_price is not None else None, o.delivery_date,
         o.rationale, int(valid), reason, iso(utcnow())),
    )


def negotiate(conn: sqlite3.Connection, *, run_id: str, item: str, request_qty: int,
              shop: Agent, supplier: Agent, shop_limits: ShopLimits, supplier_limits: SupplierLimits,
              max_rounds: int = 4, today: date | None = None, log=lambda *a: None) -> AgreedTerms | NoDeal:
    today = today or utcnow().date()
    transcript: list[Offer] = []          # valid offers only
    strikes = {SHOP: 0, SUPPLIER: 0}
    limits = {SHOP: shop_limits, SUPPLIER: supplier_limits}
    agents = {SHOP: shop, SUPPLIER: supplier}

    # Round 1: the shop's request comes from the inventory, not a model.
    request = Offer(run_id, 1, SHOP, "request", item, request_qty, None,
                    shop_limits.latest_delivery.isoformat(),
                    f"We need {request_qty} by {shop_limits.latest_delivery}.")
    _save(conn, request, True, None)
    transcript.append(request)
    log("r1 shop", f"request  {request_qty} × {item} by {request.delivery_date}")

    def last_proposal_from(side: str) -> Offer | None:
        return next((o for o in reversed(transcript) if o.from_side == side and o.action in PROPOSALS), None)

    def turn(side: str, rnd: int) -> AgreedTerms | NoDeal | None:
        other = SUPPLIER if side == SHOP else SHOP
        if rnd == 1:
            allowed = ("quote", "walk_away")
        elif rnd == max_rounds and max_rounds >= 3:
            # Final round: take the other side's last offer or leave. Without this, an agent
            # could keep countering until time ran out, even when that offer fit its limits.
            # (With max_rounds 2, the spec's "one quote and one counter" fallback still applies.)
            allowed = ("accept", "walk_away")
        else:
            allowed = ("counter", "accept", "walk_away")
        feedback = None
        while True:
            view = TurnView(rnd, max_rounds, list(transcript), allowed, feedback, today.isoformat())
            try:
                mv: Move = agents[side].move(view)
            except LLMError as e:
                log(f"r{rnd} {side}", f"model error: {e}")
                return NoDeal(f"{side} agent failed: {e}")

            # "quote" and "counter" both mean "here are my terms"; use the right word for the round.
            if mv.action in PROPOSALS:
                mv = mv.model_copy(update={"action": "quote" if rnd == 1 else "counter"})
            price = to_price(mv.unit_price)
            offer = Offer(run_id, rnd, side, mv.action, item, mv.qty, price, mv.delivery_date,
                          mv.rationale.strip())
            reason = None
            if mv.action not in allowed:
                reason = f"action {mv.action!r} is not allowed now (allowed: {', '.join(allowed)})"
            elif mv.action == "accept":
                theirs = last_proposal_from(other)
                if theirs is None:
                    reason = "there is no offer from the other side to accept"
                else:
                    # The deal is the other side's last terms, checked against MY limits.
                    offer = Offer(run_id, rnd, side, "accept", item, theirs.qty, theirs.unit_price,
                                  theirs.delivery_date, mv.rationale.strip())
                    reason = check_terms(side, theirs.qty, theirs.unit_price, theirs.delivery_date, limits[side])
                    if reason:
                        reason = f"cannot accept: {reason}"
            elif mv.action in PROPOSALS:
                reason = check_terms(side, mv.qty, price, mv.delivery_date, limits[side])

            if reason is None:
                _save(conn, offer, True, None)
                transcript.append(offer)
                if offer.action == "walk_away":
                    log(f"r{rnd} {side}", f"walk_away \"{offer.rationale}\"")
                    return NoDeal(f"{side} walked away: {offer.rationale}")
                log(f"r{rnd} {side}", f"{offer.action:<8} {offer.qty} @ ${offer.unit_price} "
                                      f"by {offer.delivery_date}  \"{offer.rationale}\"")
                if offer.action == "accept":
                    return AgreedTerms(offer.qty, offer.unit_price, offer.delivery_date, side, offer.rationale)
                return None

            _save(conn, offer, False, reason)
            strikes[side] += 1
            log(f"r{rnd} {side}", f"REJECTED {mv.action} {mv.qty} @ ${price}: {reason}")
            if strikes[side] >= 2:
                return NoDeal(f"{side} made a second invalid offer: {reason}")
            feedback = reason  # sent back once with the reason

    for rnd in range(1, max_rounds + 1):
        for side in ((SUPPLIER,) if rnd == 1 else (SHOP, SUPPLIER)):
            result = turn(side, rnd)
            if result is not None:
                return result
    return NoDeal(f"no accept after {max_rounds} rounds")
