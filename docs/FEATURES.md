# Feature Documentation

One section per feature, added when the feature is finished and tested. Each section covers what the feature does, how it works, how to run it, how it was tested, and anything that surprised us along the way.

| # | Feature | Branch | Status |
|---|---------|--------|--------|
| 1 | [Payment path (go/no-go)](#1-payment-path-gono-go) | `feature/payment-path` | ✅ Done, merged Nov 4, 2026 |

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
