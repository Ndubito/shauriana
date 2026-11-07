"""Run a demo scenario end to end from the terminal (real PayPal sandbox).

  python -m scripts.run_scenario a                  # reset DB, seed A, agents negotiate, pay
  python -m scripts.run_scenario a --fixed          # skip the LLM: use the scenario's fixed deal
  python -m scripts.run_scenario a --max-rounds 2   # one quote and one counter
  python -m scripts.run_scenario a --db ./demo.db
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from app import db, seed
from app.config import ConfigError, load_settings
from app.llm import GeminiLLM, LLMError
from app.paypal.gateway import PayPalGateway
from app.policy.rules import load_rules
from app.restock import RestockError, run_restock


def log(tag: str, msg: str) -> None:
    print(f"[{tag:<11}] {msg}", flush=True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario")
    p.add_argument("--db", help="SQLite file (default: DATABASE_URL or ./data.db)")
    p.add_argument("--fixed", action="store_true", help="use the scenario's fixed deal instead of the LLM")
    p.add_argument("--max-rounds", type=int, default=4)
    args = p.parse_args()

    load_dotenv()
    try:
        settings = load_settings()
        rules = load_rules()
        seed_data = seed.load_seed_file()
        llm = None if args.fixed else GeminiLLM.from_env()
    except (ConfigError, KeyError, LLMError) as e:
        print(f"Config error: {e}")
        return 2

    conn = db.connect(args.db)
    scenario = seed.apply_scenario(conn, seed_data, args.scenario.lower())
    mode = "fixed deal" if llm is None else f"LLM negotiation ({', '.join(llm.models)}; max {args.max_rounds} rounds)"
    log("seed", f"scenario {args.scenario.upper()} loaded: {scenario.get('title', '')} · {mode} · rules {rules.version}")

    gateway = PayPalGateway(settings, on_poll=lambda b, i: log("payout", f"batch={b} item={i}"))
    try:
        result = run_restock(conn, scenario, rules=rules, gateway=gateway, shop_email=settings.shop.email,
                             llm=llm, max_rounds=args.max_rounds, log=log)
    except RestockError as e:
        print(f"\nRun failed: {e}")
        return 1
    finally:
        gateway.close()

    outcome = conn.execute("SELECT outcome FROM runs WHERE id = ?", (result.run_id,)).fetchone()[0]
    log("run", f"{result.run_id} outcome = {outcome}")
    if llm is not None:
        log("llm", llm.usage.summary())
    pr = result.payment_request
    if pr and pr.payment:
        pm = pr.payment
        print(f"\nPayment: status={pm.status} batch={pm.payout_batch_id} item={pm.payout_item_id} "
              f"invoice_status={pm.invoice_status}" + (f" error={pm.error}" if pm.error else ""))
    return 0 if outcome in ("paid", "held", "blocked", "no_deal") else 1


if __name__ == "__main__":
    sys.exit(main())
