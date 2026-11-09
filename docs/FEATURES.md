# Feature Documentation

One section per feature, added when the feature is finished and tested. Each section covers what the feature does, how it works, how to run it, how it was tested, and anything that surprised us along the way.

| # | Feature | Branch | Status |
|---|---------|--------|--------|
| 1 | [Payment path (go/no-go)](#1-payment-path-gono-go) | `feature/payment-path` | ✅ Done, merged Nov 4, 2026 |
| 2 | [Scenario A end to end (fixed deal + policy engine)](#2-scenario-a-end-to-end-fixed-deal--policy-engine) | `feature/scenario-a` | ✅ Done, merged Nov 7, 2026 |
| 3 | [LLM negotiation](#3-llm-negotiation) | `feature/llm-negotiation` | ✅ Done, merged Nov 7, 2026 |
| 4 | [Web page, approvals and Scenarios B and C](#4-web-page-approvals-and-scenarios-b-and-c) | `feature/web-app` | ✅ Done, tested Nov 9, 2026 |

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

---

## 3. LLM negotiation

### What it is
A shop agent and a supplier agent, both Gemini models, negotiate the restock instead of using a fixed deal. Each agent picks its own moves. Plain code checks every offer against the sending side's hard limits before the other side sees it, caps the rounds, and builds the deal. Everything after the deal (invoice, policy engine, payout) is unchanged from Feature 2.

This meets the spec's Nov 6 gate: *"the LLM negotiation produces Scenario A's deal."*

### How a negotiation runs
```
Round 1   shop: request (written by code from inventory)   →  supplier: quote
Round 2   shop: counter | accept | walk_away                →  supplier: counter | accept | walk_away
Round 3   …                                                 →  …
Round 4   …                                                 →  no accept by now = no deal
```
- **Offer message:** the spec's format (`run_id, round, from, action, item, qty, unit_price, delivery_date, rationale`). Every message goes into the `offers` table, rejected ones included (`valid = 0` plus the `reason`).
- **Accept:** the deal is the **other side's last valid offer**, not the numbers inside the accept message. Those terms must also fit the accepting side's own limits, so the shop can't accept a quote above its max price.
- **Invalid offers:** an offer outside the sender's limits goes back to that agent once with the reason. A second invalid move by the same side ends the run as `no_deal`.
- **Other ways a run ends without a deal:** a walk away, no accept after the last round, or a model failure. The run is marked `no_deal`, no deal row is written, and PayPal is never called.
- **Quote and counter:** treated as the same move ("here are my terms") and relabelled for the round. A supplier that says "counter" in round 1 doesn't lose a strike over wording.

### Hard limits (checked in code on every offer)
| Side | Limit | Source |
|------|-------|--------|
| Shop | unit price ≤ `max_unit_price` | inventory |
| Shop | qty ≥ `reorder_level − stock` (enough to get back to the reorder level) | inventory |
| Shop | qty ≤ `reorder_qty` (never buys more than planned, which keeps totals predictable) | inventory |
| Shop | delivery ≤ today + `latest_delivery_days` | inventory |
| Supplier | unit price ≥ `floor_prices[item]` | supplier price list |
| Supplier | qty ≤ `max_qty` | supplier price list |
| Supplier | delivery ≥ today + `lead_days` | supplier price list |
| Both | price in whole cents; date `YYYY-MM-DD`; qty > 0 | — |

Whole cents is our addition. PayPal invoice line items are 2-decimal amounts, so a price like $0.115 would make the invoice total differ from the deal, and `deal_integrity` would block it.

### The agents (`app/agents/negotiator.py`)
- Each agent turn is one stateless call. The system prompt holds only that side's own private facts. The turn prompt holds the round number, the transcript of valid offers, the allowed actions, and, on a retry, the rejection reason.
- **Information separation, as if they were separate companies:**
  - The shop knows its max price, its quantity range, its delivery deadline and the last price it paid. It never sees the supplier's floor price.
  - The supplier knows its list price, floor price, max quantity and earliest delivery. It never sees the shop's max price or last paid price.
- The other side's rationale is shown as quoted words. The prompt tells each agent to treat it as negotiation talk, never as instructions. Even if an agent were fooled, the limits, the policy engine and `request_payment` don't depend on what it says.

### Model setup (`app/llm.py`)
- Google's official `google-genai` SDK (3.0.0) with `generate_content`, `response_mime_type="application/json"` and a Pydantic `response_schema`. The reply comes back already validated as a `Move` object (`response.parsed`), so there is no text parsing.
- **`temperature=0` and `seed=7`**, as the spec asks. All 3 live runs produced word-for-word identical transcripts.
- **Default `LLM_MODEL=gemini-3.5-flash-lite`, with `LLM_FALLBACK_MODELS=gemini-3.5-flash`.** On 429 or 5xx the client retries once, then moves to the next model in the list. A 400 or a reply that doesn't match the schema fails straight away. If every model fails, the run ends as `no_deal`.
- TLS uses the OS trust store via `truststore.inject_into_ssl()`, the same Avast workaround as Feature 1.

### Configuration
```
LLM_API_KEY=                          # Gemini API key
LLM_MODEL=gemini-3.5-flash-lite       # optional, this is the default
LLM_FALLBACK_MODELS=gemini-3.5-flash  # optional, comma-separated
```

### Seed changes
- The pouch's `max_unit_price` went from **$0.13 to $0.12**. Any deal inside both sides' limits is then at most 20% above the last paid $0.10 and at most 500 × $0.12 = $60, so Scenario A always passes the policy, whatever the models say.
- The supplier gained `list_prices` (where it opens): pouch $0.12, plantain $0.90, oil $2.80. This also adds a `suppliers.list_prices` column.

### Files
| File | Purpose |
|------|---------|
| `app/negotiation/offers.py` | `Offer`, `ShopLimits`, `SupplierLimits`, `check_terms()` |
| `app/negotiation/loop.py` | `negotiate(...) -> AgreedTerms \| NoDeal`: rounds, strikes, accept, saving offers |
| `app/agents/negotiator.py` | `Move` schema, `ShopAgent`, `SupplierAgent`, prompt rendering |
| `app/llm.py` | `GeminiLLM`: structured output, model fallback, token and time tracking |
| `app/restock.py` | `run_restock()`: negotiation, or the fixed deal with `llm=None` |
| `scripts/run_scenario.py` | Adds `--fixed` and `--max-rounds` |
| `tests/test_negotiation.py` | Limits, the loop and the full run, with scripted fake agents |
| `tests/test_llm.py` | Fallback and error handling, with a fake SDK client |

### How to run
```bash
.venv/Scripts/python -m pytest -q                                  # 59 offline tests
.venv/Scripts/python -m scripts.run_scenario a                     # agents negotiate, then pay in the sandbox
.venv/Scripts/python -m scripts.run_scenario a --max-rounds 2      # the spec's fallback: one quote, one counter
.venv/Scripts/python -m scripts.run_scenario a --fixed             # demo safety net: no LLM
```

### Model check (Nov 7, 2026)
Each candidate got the same structured-output test call:

| Model | Result |
|---|---|
| `gemini-3.6-flash` (listed as stable), `gemini-3.7-flash`, `gemini-3.8-flash` (newest) | **503 "high demand"** |
| `gemini-3.5-flash` | OK in 13.2 s (uses about 440 thinking tokens) |
| `gemini-3.5-flash-lite` | OK in 2.3 s, sensible counter-offer |

This is why the default is Flash-Lite with a fallback list. To try a newer model, change `LLM_MODEL`.

### Test results (Nov 7, 2026)
**Live, Scenario A, 3 of 3 runs paid**, each with an identical transcript:
```
[r1 shop    ] request  500 × packaging_pouch_100g by 2026-11-14
[r1 supplier] quote    500 @ $0.12 by 2026-11-10  "We can supply 500 pouches at our standard competitive price with prompt delivery."
[r2 shop    ] counter  500 @ $0.11 by 2026-11-14  "We can proceed with a slightly lower unit price closer to our recent costs."
[r2 supplier] accept   500 @ $0.11 by 2026-11-14  "We are happy to accept your counteroffer to secure a long-term business relationship."
[deal       ] 500 × packaging_pouch_100g @ $0.11 = $55.00 USD
[policy     ] … → APPROVED: All rules passed.
[payout     ] SUCCESS
[invoice    ] → MARKED_AS_PAID
[run        ] outcome = paid
[llm        ] 3 calls on gemini-3.5-flash-lite · 1364 in / 229 out / 0 thinking tokens · 3.5–4.9s
```
| Run | Batch | Model time |
|---|---|---|
| run_1107083645_05c8 | J7SJW6T9GPG3A | 4.9 s |
| run_1107083723_1d89 | 38QEKQNDHLQ92 | 3.6 s |
| run_1107083751_2827 | ZVHCRJRHEVHHY | 3.5 s |

The deal matches the spec's expected Scenario A result: *"Shop counters, deal at $0.11 × 500 = $55. Approved, paid automatically."*

**Offline tests: 59 passed** (30 new):
- **Limit checks:** each limit on both sides, sub-cent prices, bad dates
- **Loop:**
  - counter then accept
  - an accept uses the other side's terms, even when the accept message carries different numbers
  - an invalid offer is sent back once with its reason
  - a second invalid offer is no deal
  - the shop can't accept above its max
  - the round 4 cap
  - `max_rounds=2`
  - walk away
  - a model error is no deal
  - quote and counter work as synonyms
- **Agent prompts:** private facts stay separate, and the other side's text is shown as quoted words
- **Full run with a fake model:** a deal that gets paid, and a no-deal with no PayPal call
- **Model fallback:**
  - retry, then the next model
  - a retry that succeeds on the same model
  - every model busy
  - a 400 fails straight away
  - a reply that doesn't match the schema
  - the env model list is built correctly

### Design decisions and gotchas
- **The model never touches money.** The negotiation produces terms. A deal, then the invoice, then the policy engine, then the payout come after it in plain code, exactly as in Feature 2.
- **The opening request is written by code.** It's just item, quantity and deadline from the inventory, so there's no reason to spend a model call on it.
- **Short, identical demos.** Temperature 0 and a fixed seed give the same three-call negotiation every time. If a scene needs a longer negotiation on camera, change the seed prices or the prompts, not the temperature.
- **Gemini capacity.** The newest Flash models were overloaded on the build day. The fallback list keeps runs working, and `--fixed` remains a last-resort safety net for recording.

### Known limits / later
- The web page transcript (chat view, greyed-out rejected offers) comes with the web app feature. The data is already in `offers`.
- Scenarios B and C still need seed entries. B also needs the approve action.

---

## 4. Web page, approvals and Scenarios B and C

### What it is
One page shows the whole system, so the demo never has to leave it except to show the PayPal sandbox. The page shows:
- the inventory, with editable stock
- the agents' negotiation as a live chat
- the policy decision banner
- the owner's approval queue, with Approve and Deny buttons
- the read-only rules
- an AG Grid audit log of every event

The server is FastAPI with Jinja templates. One plain JavaScript file asks the server for updates every 2 seconds. AG Grid Community 36.2.0 loads from jsDelivr, and there's no build step.

This feature also adds Scenarios B and C, and the owner's approve and deny actions.

### Page layout
```
Header:  title · LLM in use · [A Routine restock] [B Large order] [C Price spike] [Reset]
Left:    Inventory (editable stock, LOW rows highlighted) · Policy (read-only)
Right:   Live run (chat: shop left, supplier right, rejected offers struck through with the reason)
         Decision banner (green approved · amber needs approval · red blocked/denied) + authorization link
         Approval queue (amount, supplier, why it was held, [Approve] [Deny])
Bottom:  Audit log, full width: AG Grid with sorting, filters, coloured status badges, quick search,
         and an authorization-record link on payment rows
```

### Endpoints
| Method | Path | Does |
|---|---|---|
| GET | `/` | The page |
| GET | `/health` | `{"ok": true}`, for the host's health check |
| GET | `/inventory` | Stock, reorder levels, last paid price, `low` flag |
| PATCH | `/inventory/{item}` | `{"stock": n}`. Below the reorder level, it starts a negotiated restock (needs the LLM) |
| GET | `/runs/latest` | The latest run's full state, plus the runner state (busy, job, last error, log) |
| GET | `/runs/{id}` | One run: offers, deal, decision, approval, payment, `stage` |
| GET | `/runs/{id}/authorization` | Authorization record JSON |
| GET | `/approvals?status=pending` | The queue. Expired approvals are moved to `expired` first |
| POST | `/approvals/{id}` | `{"approve": true\|false}`. Returns 202, 404 if unknown, 409 if already answered, expired or busy |
| GET | `/audit` | Offers, decisions, approvals and payments as one list, newest first |
| GET | `/policy` | The rules file, read-only |
| POST | `/scenarios/{a,b,c}` | Reset, apply the scenario's overrides, start the run. 202, or 409 if a job is running |
| POST | `/reset` | Wipe runs and restore the seed |

Start the server with:
```bash
.venv/Scripts/python -m uvicorn app.web.main:create_app_from_env --factory --port 8000
```
If `LLM_API_KEY` isn't set, the scenario buttons run their fixed deals and manual restocks are refused with an explanation.

### How it works
- **Background jobs, one at a time (`app/runner.py`).** Each button starts a job on a background thread and returns 202 immediately. Each job opens its own SQLite connection. Offers and status changes are saved as they happen, so the page shows the chat building up. A second job while one is running gets 409 "already running".
- **The lock comes first.** Every database change a button makes (scenario reset, the approve or deny answer, the payment reservation) happens inside the job, while the job holds the lock. This was a fix during the build: the first version recorded an approval before taking the lock, so another job could have left a reserved payment unpaid.
- **Approve and deny (`approvals.decide`).** Inside `BEGIN IMMEDIATE`:
  - it re-checks that the approval is still `pending` and not expired
  - approve also reserves the payment row in the same transaction, then `pay()` runs
  - deny marks the run `denied`

  A second Approve gets 409, and nothing is paid twice.
- **SQLite runs in WAL mode**, so the page can read while a job writes.
- **The authorization record (`app/authorization.py`)** is built from the `deals`, `decisions`, `approvals` and `payments` tables in the spec's format:
  - `authorized_by` is `{"type": "policy"}` when the policy approved, `{"type": "owner"}` when the owner approved, and `null` for blocked, denied or expired runs
  - those runs still get a record, with `paypal: null`
- **Untrusted text is never inserted as HTML.** Model and supplier text (rationales, reasons) goes in with `textContent`.
- **No flicker, no lost clicks.** The transcript, approval queue, inventory and audit grid only redraw when their data has changed. Found during testing: redrawing every 2 s replaced the Approve button and the stock inputs between "mouse down" and "click".

### Scenarios (`data/seeds.json`)
Each scenario resets the database, then applies `inventory` overrides and `suppliers` price overrides. The numbers are chosen so the outcome can't vary, whatever the models agree.

| Scenario | Setup | Why the outcome is fixed | Expected |
|---|---|---|---|
| A Routine restock | pouch stock 40 (reorder 100) | any deal ≤ 500 × $0.12 = $60, ≤ 20% above $0.10 | approved, paid |
| B Large order | plantain stock 30, reorder 150, reorder qty 120 | qty must be exactly 120, price $0.75–$0.95 → $90–$114: over $80, under $150 | held, then owner approves or denies |
| C Price spike | oil stock 10, supplier floor raised to $2.70 (last paid $2.00) | every price the supplier can offer is ≥ 35% above last paid; 20 L ≤ $60, so no other rule fires first | blocked by `price_spike` |

Plantain's `latest_delivery_days` went from 3 to 5. Before that, the supplier's 3-day lead time left only one possible delivery date.

### Agent prompt changes (found while testing C)
The first live run of C ended `no_deal`. The shop knew it last paid $2.00, held out at $2.50, and the 4 rounds ran out. Sensible behaviour, but it never reached the policy, so the scenario failed. Two changes:
- **The shop now sees its stock situation:** "Stock on hand: 10 litre (reorder level 25); running out stops production, which costs more than a higher price."
- **A last-round rule for both agents:** if the other side's latest offer is within your limits on the last round, accept it.

After this change, C plays out as the spec describes: the shop accepts 20 × $2.70 = $54 to "secure our essential restock before production halts", and the policy blocks it. That's the core story: **the agent's own limits allowed the price, and the owner's rule caught the 35% jump.**

### Model logging
`app/llm.py` now logs which model answered each call, with time and tokens, plus every 429/5xx and fallback. Reason: on Nov 9 calls sometimes took 12–14 s where they'd taken ~1 s on Nov 7. The logs showed `gemini-3.5-flash-lite` itself answering slowly at times (no fallback involved), so the slowness was on Google's side. Typical calls are ~1 s.

### Files
| File | Purpose |
|------|---------|
| `app/web/main.py` | `create_app(runner)` with every endpoint; `create_app_from_env()` wires production |
| `app/web/templates/index.html` | The page |
| `app/web/static/app.js` | Polling, rendering, buttons, AG Grid setup |
| `app/web/static/style.css` | Colour variables (green, amber, red for decisions), layout, stacks to one column under 900 px |
| `app/runner.py` | Background jobs with a lock: scenarios, manual restock, approve/deny then pay, reset |
| `app/views.py` | Data for the page: inventory, run detail with stage, approvals, audit list |
| `app/approvals.py` | Adds `decide()` (approve or deny, atomically) |
| `app/authorization.py` | Authorization record builder |
| `app/seed.py` | General scenario overrides (inventory fields, supplier prices) |
| `tests/test_web.py` | 13 API tests with the test client, a fake gateway and inline jobs |

### Test results (Nov 9, 2026)
**Offline: 72 tests pass** (13 new):
- the page renders
- inventory and policy
- A auto-paid, with its full authorization record
- B held → approve → paid, and a second approve gets 409 with no second payout
- B deny → no payment
- C blocked with the spec's exact reason
- an expired approval can't be approved
- the audit log has all four event types, newest first
- reset
- stock changes
- busy refuses a second job
- unknown items give 404

**Live, in the browser pane, against the real PayPal sandbox and Gemini:**
| Check | Result |
|---|---|
| A | Quote $0.12 → counter $0.11 → accept · ✅ approved by policy · payout SUCCESS · last paid updated to $0.11 |
| B | 120 kg negotiated to $0.88 = $105.60 · ⏸️ held · Approve → ✅ "Approved by owner" · payout SUCCESS · `authorized_by: owner` |
| C | $2.80 quote, shop counters $2.30 / $2.45, supplier holds $2.70, shop accepts in round 4 · ⛔ "Unit price $2.70 is 35% above last paid $2.00 (limit 20%)" · no payment |
| Manual restock | Set plantain stock to 50 → "restock started" → negotiated $105.60 → held |
| Deny | ⛔ "Denied by owner: no money moves" · no payment · authorization record has `authorized_by: null`, `paypal: null` |
| Audit log | All events, newest first, status badges; quick search "owner" → 3 of 9 rows |
| Authorization record | A: `authorized_by: policy`, 7 passing checks, rules in force, invoice, batch, item and invoice payment IDs |
| Reset | Seed restored, run, queue and audit cleared |
| Console | No JavaScript errors |

### Known limits / later
- Hosting on Render is the next feature. `/health` is ready for it.
- The sandbox payout takes about 20 s, so the stage shows "paying" for a while. Fine for the video, but worth knowing.
- Scenario D (payee swap) remains optional. The rule is already covered by tests.
- No login (out of scope in the spec). On a public URL, anyone can press the buttons. It's sandbox money only.
