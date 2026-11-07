"""Run a demo scenario end to end from the terminal (fixed deal, real PayPal sandbox).

  python -m scripts.run_scenario a        # reset DB, seed scenario A, run it
  python -m scripts.run_scenario a --db ./demo.db
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from app import db, seed
from app.config import ConfigError, load_settings
from app.paypal.gateway import PayPalGateway
from app.policy.rules import load_rules
from app.restock import RestockError, run_fixed_deal


def log(tag: str, msg: str) -> None:
    print(f"[{tag:<7}] {msg}", flush=True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario")
    p.add_argument("--db", help="SQLite file (default: DATABASE_URL or ./data.db)")
    args = p.parse_args()

    load_dotenv()
    try:
        settings = load_settings()
        rules = load_rules()
        seed_data = seed.load_seed_file()
    except (ConfigError, KeyError) as e:
        print(f"Config error: {e}")
        return 2

    conn = db.connect(args.db)
    scenario = seed.apply_scenario(conn, seed_data, args.scenario.lower())
    log("seed", f"scenario {args.scenario.upper()} loaded: {scenario.get('title', '')} "
                f"(rules {rules.version})")

    gateway = PayPalGateway(settings, on_poll=lambda b, i: log("payout", f"batch={b} item={i}"))
    try:
        result = run_fixed_deal(conn, scenario, rules=rules, gateway=gateway,
                                shop_email=settings.shop.email, log=log)
    except RestockError as e:
        print(f"\nRun failed: {e}")
        return 1
    finally:
        gateway.close()

    run_id = conn.execute("SELECT run_id FROM deals WHERE id = ?", (result.decision.deal_id,)).fetchone()[0]
    outcome = conn.execute("SELECT outcome FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
    log("run", f"{run_id} outcome = {outcome}")
    if result.payment:
        pm = result.payment
        print(f"\nPayment: status={pm.status} batch={pm.payout_batch_id} item={pm.payout_item_id} "
              f"invoice_status={pm.invoice_status}" + (f" error={pm.error}" if pm.error else ""))
    return 0 if outcome in ("paid", "held", "blocked") else 1


if __name__ == "__main__":
    sys.exit(main())
