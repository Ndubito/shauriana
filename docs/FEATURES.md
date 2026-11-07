# Feature Documentation

One section per feature, added when the feature is finished and tested. Each section covers what the feature does, how it works, how to run it, how it was tested, and anything that surprised us along the way.

| # | Feature | Branch | Status |
|---|---------|--------|--------|
| 1 | [Payment path (go/no-go)](#1-payment-path-gono-go) | `feature/payment-path` | ✅ Done, merged Nov 4, 2026 |
| 2 | [Scenario A end to end (fixed deal + policy engine)](#2-scenario-a-end-to-end-fixed-deal--policy-engine) | `feature/scenario-a` | ✅ Done, merged Nov 7, 2026 |

---

## 1. Payment path (go/no-go)

### What it is
The plumbing that moves money. The supplier invoices the shop, the shop pays that invoice through PayPal Payouts, and the supplier records the payment on the invoice. There is no AI and no policy engine yet. This is the spec's Nov 4 gate: nothing else gets built until real sandbox money moves end to end.

### What it does (spec steps 1, 2, 4, 5, 6)
| Step | Actor | PayPal call | Credentials |
|------|-------|-------------|-------------|
| 1 | Supplier | `POST /v2/invoicing/invoices` (with `Prefer: return=representation`) creates a draft invoice addressed to the shop | supplier |
| 2 | Supplier | `POST /v2/invoicing/invoices/{id}/send` moves it from `DRAFT` to `SENT` | supplier |
| 4 | Shop | `POST /v1/payments/payouts` sends one EMAIL item to the supplier for the invoice total, with `sender_batch_id` = deal ID | shop |
| 5 | Shop | `GET /v1/payments/payouts/{batch_id}` every 2.5 s until the item reaches a final status (default timeout 120 s) | shop |
| 6 | Supplier | `POST /v2/invoicing/invoices/{id}/payments` records the payment, with the payout item ID in the note | supplier |

Step 3 (`request_payment` → policy engine) comes in Feature 2.

### Files
| File | Purpose |
|------|---------|
| `app/config.py` | Loads `.env` and builds the shop and supplier credential sets. Picks the sandbox or live base URL. |
| `app/paypal/auth.py` | `TokenProvider`: client-credentials OAuth via `/v1/oauth2/token`. Caches the token per app and refreshes it 60 s before expiry. |
| `app/paypal/client.py` | `PayPalClient`: an authenticated `httpx` client for one app. Retries once on 401 and turns PayPal errors into `PayPalError` (status, name, message, details, debug_id). |
| `app/paypal/invoicing.py` | `create_invoice`, `send_invoice`, `get_invoice`, `record_payment`, plus `PAID_STATUSES`. |
| `app/paypal/payouts.py` | `create_payout`, `get_payout`, `wait_for_payout`. |
| `app/paypal/money.py` | Decimal helpers. Money is `Decimal` in code and a 2-decimal string on the wire. |
| `scripts/gonogo.py` | Runs the five steps for a fixed Scenario A deal (500 × `packaging_pouch_100g` @ $0.11 = $55.00). |
| `tests/test_paypal_unit.py` | Offline unit tests (no network). |

### Configuration
Copy `.env.example` to `.env` and fill it in. Values come from the PayPal Developer Dashboard.

```
PAYPAL_ENV=sandbox
SHOP_CLIENT_ID=            # REST app linked to the shop sandbox account (Payouts enabled)
SHOP_CLIENT_SECRET=
SHOP_SANDBOX_EMAIL=        # shop sandbox account login, e.g. sb-...@business.example.com
SUPPLIER_CLIENT_ID=        # REST app linked to the supplier sandbox account (Invoicing enabled)
SUPPLIER_CLIENT_SECRET=
SUPPLIER_SANDBOX_EMAIL=
```

### How to run
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows; use .venv/bin/python on macOS/Linux
.venv/Scripts/python -m pytest -q                            # offline tests
.venv/Scripts/python -m scripts.gonogo                       # full sandbox run, fresh deal ID
.venv/Scripts/python -m scripts.gonogo --deal-id deal_X      # reuse an ID to see the duplicate guard
```

The script exits with code `0` and prints `GO ✅` on success. On any failure it exits with `1` and prints `NO-GO ❌` with PayPal's error name, details and `debug_id`.

### Test results (Nov 4, 2026, sandbox)
**Full run:** passed.
```
[invoice] created INV2-Z2QN-SKQE-YXM8-Z6AF ($55.00, DRAFT)
[invoice] sent INV2-Z2QN-SKQE-YXM8-Z6AF to sb-…@business.example.com
[payout ] batch VGL2PZQVM8LPU created (sender_batch_id=deal_gonogo_001, PENDING)
[payout ] PENDING → PROCESSING → SUCCESS (item NSTXSTAX56M86)
[invoice] recorded payment EXTR-26811509LG7383500 → status MARKED_AS_PAID
GO ✅
```

**Duplicate guard:** passed. Rerunning with `--deal-id deal_gonogo_001` was rejected by PayPal:
```
400 USER_BUSINESS_ERROR — field SENDER_BATCH_ID: "Batch with given sender_batch_id already exists"
```

**Unit tests:** 6 passed. They cover rounding, token caching and refresh, OAuth error mapping, and payout polling through to SUCCESS, UNCLAIMED and timeout.

### Design decisions and gotchas
- **Deal ID = `sender_batch_id`.** PayPal refuses a second batch with the same ID, so a deal can't be paid twice even if our own checks fail.
- **No `PayPal-Request-Id` header on payouts.** With that header, PayPal treats a repeat call as an idempotent retry and returns the original batch, which would hide a duplicate. We want the rejection instead.
- **The invoice ends as `MARKED_AS_PAID`, not `PAID`.** PayPal shows `PAID` only when the buyer pays through the invoice's own checkout. A payment the supplier records shows `MARKED_AS_PAID`. `PAID_STATUSES` accepts `PAID`, `MARKED_AS_PAID` and `PAID_EXTERNAL`.
- **TLS uses the OS trust store (`truststore`).** On the dev machine, Avast Web/Mail Shield intercepts HTTPS and re-signs certificates. Windows trusts Avast's root but Python's bundled CA list does not. Using the OS store keeps verification on and also works on Render's Linux image. Never set `verify=False`.
- **`UNCLAIMED` means the email is wrong.** If a payout item ends `UNCLAIMED`, the receiver email is almost always not a sandbox account email. The script says so.
- **Every field was checked against PayPal's OpenAPI schemas.** The Invoicing v2 and Payouts v1 `schema.json` files were checked on Nov 4, 2026, as the spec requires.
- **The go/no-go script can leave an extra invoice.** It creates the invoice before paying, so a rejected duplicate run leaves one `SENT` invoice open in the supplier sandbox (`INV2-2QCC-UY2J-ZZFK-92WE` from the duplicate test). In the real flow the policy engine blocks a duplicate before any payment is attempted.

### Known limits / later
- Polling only. Webhooks are an add-on once the app is hosted.
- Payouts stands in for a business purchase. Production would use a vaulted, merchant-initiated payment (see the README caveat, coming with the README feature).

---

## 2. Scenario A end to end (fixed deal + policy engine)

### What it is
This feature puts the owner's rules between the shop and PayPal. A restock run now goes: low stock → deal → supplier invoice → **policy engine** → payout → invoice recorded. Every step is saved in SQLite. The deal is still fixed in code; LLM negotiation replaces it in Feature 3 without changing anything after the deal.

This meets the spec's Nov 5 gate: *"Scenario A runs end to end in the terminal: fixed deal, policy check, payout, invoice marked paid."*

### Flow
```
seed DB ─▶ stock below reorder level ─▶ run + fixed deal (offers logged)
   ─▶ supplier creates + sends invoice (deal ID as invoice reference)
   ─▶ shop calls request_payment(deal_id)          ← the shop's ONLY payment tool
        BEGIN IMMEDIATE
          build context from DB → evaluate() → save decision
          approved       → reserve payments row (status CREATED)
          needs_approval → approvals row "pending", run = held
          blocked        → run = blocked
        COMMIT
   ─▶ approved only: pay() → payout → poll → record on invoice
   ─▶ run = paid, inventory.last_paid_unit_price = deal price
```

### The policy engine (`app/policy/engine.py`)
It is plain code with no model calls. It checks the rules in a fixed order and stops at the first one that fails. Every rule that ran is saved in `checks`, including the ones that passed.

| # | Rule | Fails when | Outcome |
|---|------|-----------|---------|
| 1 | `payee_allowlist` | Invoice payee not in `approved_payees` (case and space insensitive) | blocked |
| 2 | `deal_integrity` | Invoice total ≠ deal total, invoice payee ≠ deal's supplier, or currency ≠ `USD` | blocked |
| 3 | `duplicate` | Deal already has a payment (CREATED/PENDING/SUCCESS/…) or a pending or approved approval | blocked |
| 4 | `price_spike` | Unit price more than 20% above `last_paid_unit_price` (no history means it passes) | blocked |
| 5 | `per_order_cap` | Total > $150 | blocked |
| 6 | `weekly_cap` | Last 7 days rolling (paid + in-flight + held, excluding this deal) + this total > $400 | blocked |
| 7 | `approval_threshold` | Total > $80 | needs_approval |
| 8 | — | All passed | approved |

The engine reads only numbers and emails. It never reads free text such as `Deal.rationale`, so supplier messages can't affect the decision. A test proves this.

### Rules file (`app/policy/rules.json`)
The spec's rules plus a `version` field (`2026-11-03`). `approved_payees` holds `${SUPPLIER_SANDBOX_EMAIL}`, which is filled in from `.env` at load time. A clean clone therefore works with anyone's sandbox, and no real email sits in the public repo. Numbers are parsed as `Decimal`, so `150` and `150.00` compare exactly. Every decision row stores the policy version and a snapshot of the rules in force, which the authorization record will use later.

### Database (`app/db.py`)
The spec's 8 tables in plain `sqlite3`. Money is stored as TEXT decimals. Additions to the spec:
- `runs.outcome` adds `started`, `held` and `failed` to the spec's `deal`, `no_deal`, `paid`, `blocked` and `denied`.
- `deals` stores `currency`, `rationale` and what the invoice said (`invoice_total`, `invoice_payee`), so `deal_integrity` compares the deal against the actual invoice.
- `decisions` stores `policy_version` and `rules_in_force`.
- `approvals` stores `decision_id` and `expires_at`.
- `payments` stores `amount`, `invoice_payment_id`, `error` and `updated_at`.

### Files
| File | Purpose |
|------|---------|
| `app/models.py` | `Deal`, `Invoice`, `Check`, `Decision` dataclasses |
| `app/db.py` | Schema, `connect()`, `wipe()`. DB path from `DATABASE_URL` (default `./data.db`, git-ignored). |
| `app/seed.py`, `data/seeds.json` | Seed inventory (3 items) and supplier, plus scenario overrides. `reset()` and `apply_scenario()`. |
| `app/envsubst.py` | `${VAR}` substitution for config and seed files |
| `app/policy/rules.py`, `rules.json` | Load the rules file (read-only) |
| `app/policy/engine.py` | `evaluate(deal, invoice, ctx, rules) -> Decision`, pure |
| `app/policy/context.py` | `build_context()`: last paid price, duplicate facts, rolling 7-day total from the DB |
| `app/approvals.py` | `create_pending()`, `expire_stale()` (unanswered after 24 h → `expired`, run → `denied`) |
| `app/shop/tools.py` | `request_payment(deal_id)`: the shop's only payment tool |
| `app/payments.py` | `create_row()` reserves the payment; `pay()` runs payout → poll → record on invoice |
| `app/paypal/gateway.py` | `PayPalGateway`: the four PayPal operations behind one object (tests use a fake) |
| `app/store.py` | Helpers for saving and loading deals, decisions and run outcomes |
| `app/restock.py` | `run_fixed_deal()`: one restock attempt with the scenario's fixed deal |
| `scripts/run_scenario.py` | Terminal runner |
| `tests/test_policy.py` | The spec's 9 policy tests plus edge cases |
| `tests/test_restock_flow.py` | Full flow against a fake gateway |

### How to run
```bash
.venv/Scripts/python -m pytest -q                 # 29 offline tests
.venv/Scripts/python -m scripts.run_scenario a    # resets the DB, seeds scenario A, runs it in the sandbox
```

### Test results (Nov 7, 2026)
**Live sandbox run:** passed.
```
[stock  ] packaging_pouch_100g at 40 (reorder at 100) → restock
[deal   ] deal_1107064350_548d: 500 × packaging_pouch_100g @ $0.11 = $55.00 USD
[invoice] INV2-TEUV-UDJ6-X9JT-XJRR ($55.00) created → sent
[policy ] payee_allowlist ✓ deal_integrity ✓ duplicate ✓ price_spike ✓ per_order_cap ✓ weekly_cap ✓ approval_threshold ✓
[policy ] → APPROVED: All rules passed.
[payout ] batch MBJ5RAALGMMC8 … SUCCESS item GQNH5HB3GAEFS
[invoice] INV2-TEUV-UDJ6-X9JT-XJRR → MARKED_AS_PAID
[run    ] run_1107064350_548d outcome = paid
```
The DB afterwards has 1 run (`paid`), 3 offers, 1 deal with its invoice attached, 1 decision with 7 passing checks, and 1 payment (`SUCCESS`, with batch, item and invoice payment IDs). `last_paid_unit_price` was updated from 0.10 to 0.11.

**Live duplicate:** passed. Calling `request_payment` again on the paid deal gave `blocked / duplicate` with **0 PayPal calls**. The run stays `paid` and both decisions are recorded.

**Offline tests: 29 passed.** The spec's 9 tests:
| Spec test | Result |
|---|---|
| Unknown payee is blocked | ✅ |
| Amount that differs from the invoice total is blocked | ✅ (plus: invoice from a different allowlisted payee than the deal) |
| Second payment on the same deal is blocked | ✅ (engine and full flow; no payout call) |
| Price 21% above last paid is blocked; 20% passes | ✅ (reason text matches the spec example: "$0.135 is 35% above last paid $0.10 (limit 20%)") |
| $150.01 is blocked; $150.00 passes the per-order cap | ✅ |
| A payment that pushes the week past $400 is blocked | ✅ (exactly $400 passes) |
| $80.01 needs approval; $80.00 is approved | ✅ |
| Unanswered approval expires as denied | ✅ (23:59 later still pending; 24:00 → expired, run denied) |
| Supplier text telling the shop to ignore its rules has no effect | ✅ (decision with injected text is identical to the one without) |

### Design decisions and gotchas
- **Rolling 7-day weekly cap.** Agreed with the owner: simpler than a calendar week and no Monday reset. Held (pending-approval) payments count toward it, so a waiting approval can't let a second order slip past the cap. Failed payouts don't count.
- **The decision and the payment reservation commit together.** `request_payment` runs inside `BEGIN IMMEDIATE`. When approved, it inserts a `payments` row with status `CREATED` before PayPal is called. A second request (even a concurrent one) sees that row and is blocked by `duplicate`. PayPal's `sender_batch_id` check is a second layer.
- **Run and deal IDs are timestamp plus random (`run_1107064350_548d`), not counters.** The deal ID becomes PayPal's `sender_batch_id`, and PayPal never allows reuse, even after we reset our DB. A counter would make every run after a reset fail as a "duplicate" at PayPal.
- **A rejected repeat request doesn't rewrite a finished run.** Found during live testing: the first version set a paid run to `blocked` when a repeat request was rejected. Policy outcomes now only update runs that are still open (`started`, `deal`). The rejected attempt is still kept in `decisions`.
- **Failure handling.**
  - A payout error or a non-SUCCESS final status sets the payment to `FAILED` or PayPal's status, and the run to `failed`. `last_paid_unit_price` is left unchanged.
  - If the payout succeeds but recording on the invoice fails, the run is still `paid` (the money moved) and the error is kept on the payment row.
- **Stock is not increased after payment.** Delivery and shipment tracking are out of scope. Each scenario run resets the seed.
- **`needs_approval` is recorded, with no buttons yet.** Approve and deny come with the approval-queue feature. Scenario A doesn't need them.

### Known limits / later
- Only scenario `a` is seeded. Scenarios B and C only need seed entries; B also needs the approve action.
- No authorization-record export yet. Decisions already store everything it needs.
