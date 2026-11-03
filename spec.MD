# Restocking Agents with Spending Guardrailas — Build Spec

PayPal AI Hackathon · Deadline Nov 12, 2026, 12:00pm PST (11:00pm Nairobi) · Nov 3, 2026 · @Nathan Achar

## Overview

A shop agent and a supplier agent negotiate restocks and settle them over PayPal. A policy engine the owner controls checks every payment before money moves.

**Problem.** Agentic commerce needs agents that can pay. Owners will not give an agent unlimited spending authority, and an agent that cannot pay is only a recommendation engine. The missing piece is a layer of hard rules the agent cannot override, with the owner brought in only above a set risk level.

**User.** The owner of a small product business with a few recurring suppliers. The demo case is a Nairobi plantain chip maker restocking packaging, plantain and cooking oil.

**Core claim.** The language model negotiates and asks to pay. Plain code decides whether money moves, and no prompt or supplier message can change that decision.

**Why now.** Banks and card networks say agentic commerce has moved ahead of the rules on liability and authorization. Visa's partnerships lead calls a detailed audit trail for agent transactions crucial, and Amex now asks for a "purchase intent" record when an agent's purchase goes wrong ([Payments Dive, Oct 28, 2026](https://www.paymentsdive.com/news/liability-looms-over-ai-shopping/872979/)). The project produces both for every payment: an audit log entry and an authorization record that shows the rules in force, each check, and who approved.

**Build mode.** Solo, with AI coding tools, about 55 hours from Nov 3 to Nov 11. One scenario runs end to end first; everything else is added on top of a working system.

**Prize targets.**

| Prize | Amount | Why this project fits |
| --- | --- | --- |
| 1st / 2nd / 3rd place | $12,000 / $8,000 / $5,000 | Covers all five judging criteria |
| Best Use of Agentic Commerce | $5,000 | Agents transact on the owner's behalf |
| Most Creative | $5,000 | Agent-to-agent deals inside enforced limits |
| Best Demo Delivery | $5,000 | Three visible outcomes in under 3 minutes |
| Best Use of AG Grid | $5,000 / $2,000 / $1,000 | Audit log and approval queue built on AG Grid |
| Best Use of Render | $1,000 / $750 / $500 in credits | Hosted on Render |

Check the rules on whether one project can win more than one prize ([hackathon page](https://paypalaihackathon.devpost.com)).

**Hard requirements.** Meaningful use of both PayPal and AI. A working build that judges can run, or a hosted URL. A public GitHub repo with a license visible in its About section. A public YouTube demo under 3 minutes.

## How it works

A restock moves from low stock to a negotiated deal, an invoice, a policy check, and one of three outcomes: paid, held for the owner, or blocked.

&#91;embedded content: restock flow · negotiation, policy check, three outcomes\]

The shop agent's request goes through the policy engine every time; only a payment that passes all rules, or one the owner approves, reaches PayPal.

## Components

One backend process holds both agents, the policy engine and the payment code. Each agent uses its own sandbox credentials. The supplier agent runs in the same process for the demo, but it is written as if it were a separate company: it talks to the shop only through offer messages and PayPal.

| Component | Job | Built with |
| --- | --- | --- |
| Shop agent | Spots low stock, negotiates, asks to pay a deal | LLM with structured JSON output and one tool: `request_payment(deal_id)` |
| Supplier agent | Quotes, counters, issues and sends the invoice, records payment | LLM + PayPal Agent Toolkit |
| Negotiation loop | Passes offers between agents, caps rounds, rejects offers outside limits | Plain code |
| Policy engine | Returns approved, needs approval or blocked for every payment request | Plain code + a JSON rules file |
| Payments module | Sends the payout, polls its status, triggers invoice payment record | PayPal REST APIs (Payouts, Invoicing) |
| Authorization record | One JSON file per payment: rules in force, every check and result, who approved, PayPal IDs | Plain code, built from the audit tables |
| Approval queue | Holds payments that wait for the owner | Backend endpoints + web page |
| Audit log | Stores every offer, policy decision and payment | SQLite, shown in AG Grid |
| Web app | One page: inventory, live run, approvals, audit log | Server-rendered HTML from FastAPI, plain JavaScript, AG Grid from a CDN |
| Hosting | Public demo URL for judges | Render |

The shop agent never calls PayPal itself. Its only payment tool is `request_payment`, and that tool runs the policy engine before the payments module is ever reached.

## PayPal integration

The supplier side uses the Agent Toolkit for invoices, and the shop side pays through the Payouts REST API. The toolkit's payment tools only pay an order after a buyer approves it in a browser, so an agent cannot pay through them alone ([Agent Toolkit](https://github.com/paypal/agent-toolkit/)).

**Sandbox setup.** In the [Developer Dashboard](https://developer.paypal.com/dashboard/), create two sandbox business accounts, `shop` and `supplier`, and one REST app for each. Confirm the shop app has Payouts enabled. All amounts are in USD, because PayPal does not support KES as a transaction currency; the page may show a KES equivalent for context.

**Money flow, one restock.**

| Step | Actor | Call | Through |
| --- | --- | --- | --- |
| 1 | Supplier agent | Create invoice addressed to the shop's sandbox email | Agent Toolkit `create_invoice` |
| 2 | Supplier agent | Send the invoice | Agent Toolkit `send_invoice` |
| 3 | Shop agent | `request_payment(deal_id)` → policy engine | Own code |
| 4 | Payments module | `POST /v1/payments/payouts` to the supplier email, amount = invoice total, `sender_batch_id` = deal ID | REST, shop credentials |
| 5 | Payments module | `GET /v1/payments/payouts/{payout_batch_id}` until the item reads `SUCCESS` | REST, polled every 2–3 s |
| 6 | Supplier agent | Record the payment on its invoice, with the payout item ID in the note | Invoicing API `POST /v2/invoicing/invoices/{id}/payments`, supplier credentials |

**Details that matter.**

- Using the deal ID as `sender_batch_id` stops a double payment: PayPal rejects a repeated batch ID.
- Get an OAuth token from `/v1/oauth2/token` per app and cache it until it expires.
- Check every endpoint and field against the [REST docs](https://developer.paypal.com/api/rest/) the day it is written. AI coding tools invent PayPal field names.
- Poll for payout status in the first version. Webhooks are an add-on once the app is hosted.
- Shipment tracking is out of scope for this flow.

**The Payouts caveat.** Payouts is meant for rebates and gig pay, so it is a stand-in for a business purchase. A production version would use a vaulted, merchant-initiated payment the owner sets up once. The README says this before a judge asks.

**Go/no-go test (Nov 4).** A plain script, no AI, runs steps 1, 2, 4, 5 and 6 end to end in the sandbox. Nothing else gets built until it passes.

## Negotiation protocol

The agents trade structured offers for at most four rounds, and code rejects any offer outside either side's limits. The model chooses its moves; the limits live outside the model.

**Build order.** The first version uses a fixed deal written in code, so the payment and policy path runs end to end by Nov 5. The LLM negotiation replaces the fixed deal on Nov 6. If it is not stable by the end of that day, it shrinks to one quote and at most one counter-offer.

**Offer message.**

```json
{
  "run_id": "run_014",
  "round": 2,
  "from": "shop",
  "action": "counter",
  "item": "packaging_pouch_100g",
  "qty": 500,
  "unit_price": 0.11,
  "delivery_date": "2026-11-10",
  "rationale": "Last order was $0.10 a unit; meeting halfway on 500."
}
```

`action` is one of `request`, `quote`, `counter`, `accept`, `walk_away`. The `rationale` line appears in the transcript on screen.

**Order of play.**

1. Shop opens with a `request`: item, quantity needed, latest delivery date.
2. Supplier answers with a `quote`.
3. Each side then accepts, counters or walks away, until round 4.
4. An `accept` creates a deal record. No accept by round 4 ends the run as no deal.

**Hard limits, checked in code on every offer.**

| Side | Limits | Stored in |
| --- | --- | --- |
| Shop | Max unit price, min quantity, latest delivery date | Inventory row for the item |
| Supplier | Floor unit price, max quantity, earliest delivery date | Supplier price list |

An offer outside the sender's limits is sent back to that agent once with the reason. A second bad offer ends the run as no deal. The limits are given to each agent in its prompt, but the code check is what holds.

**Model.** Any model with reliable structured output (OpenAI, Claude, Gemini or Llama). Temperature 0 or close to it.

**Repeatable demos.** Seed data fixes stock levels, price lists and last-paid prices for each scenario. The price-spike scenario raises the supplier's floor price in its seed, so the block happens on every run regardless of what the model says.

## Policy engine

The policy engine is plain code with no model calls. It reads one rules file, checks each payment request against the rules in a fixed order, and the first failing rule decides the outcome. Every rule result is logged, including the ones that passed.

**Rules file.**

```json
{
  "currency": "USD",
  "approved_payees": ["supplier@business.example.com"],
  "max_per_order": 150,
  "max_per_week": 400,
  "require_approval_above": 80,
  "max_unit_price_increase_pct": 20,
  "approval_timeout_hours": 24
}
```

Replace the payee with the supplier's real sandbox email. The page shows these rules read-only.

**Evaluation order.**

1. **Payee allowlist.** Invoice payee not on the list → blocked.
2. **Deal integrity.** Payment amount differs from the invoice total, or the invoice comes from a different payee than the deal → blocked.
3. **Duplicate.** The deal already has a payment → blocked.
4. **Price spike.** Unit price more than 20% above the last paid price for that item → blocked.
5. **Per-order cap.** Total above $150 → blocked.
6. **Weekly cap.** This week's paid and pending totals plus this payment above $400 → blocked.
7. **Approval threshold.** Total above $80 → needs approval.
8. Otherwise → approved.

**Decision output.**

```json
{
  "deal_id": "deal_031",
  "status": "blocked",
  "rule": "price_spike",
  "reason": "Unit price $0.135 is 35% above last paid $0.10 (limit 20%).",
  "checks": [
    {"rule": "payee_allowlist", "passed": true},
    {"rule": "deal_integrity", "passed": true},
    {"rule": "duplicate", "passed": true},
    {"rule": "price_spike", "passed": false}
  ]
}
```

A held payment that the owner does not answer within 24 hours counts as denied.

**Tests, written with each rule; the full set passes by Nov 9.**

- [ ] Unknown payee is blocked
- [ ] Amount that differs from the invoice total is blocked
- [ ] Second payment on the same deal is blocked
- [ ] Price 21% above last paid is blocked; 20% passes
- [ ] $150.01 is blocked; $150.00 passes the per-order cap
- [ ] A payment that pushes the week past $400 is blocked
- [ ] $80.01 needs approval; $80.00 is approved
- [ ] Unanswered approval expires as denied
- [ ] Supplier text in the deal that tells the shop to ignore its rules has no effect on the decision

## Data model and API

Eight SQLite tables hold the state, and a small REST API exposes it to the page. Each run (one restock attempt) ties together its offers, deal, decision, approval and payment.

**Tables.**

| Table | Key columns |
| --- | --- |
| `inventory` | item, unit, stock, reorder\_level, reorder\_qty, max\_unit\_price, latest\_delivery\_days, last\_paid\_unit\_price |
| `suppliers` | id, name, paypal\_email, floor\_prices (per item), max\_qty, lead\_days |
| `runs` | id, item, supplier\_id, started\_at, outcome (deal, no\_deal, paid, blocked, denied) |
| `offers` | id, run\_id, round, from, action, qty, unit\_price, delivery\_date, rationale, valid |
| `deals` | id, run\_id, qty, unit\_price, total, delivery\_date, invoice\_id, payee\_email |
| `decisions` | id, deal\_id, status, rule, reason, checks (JSON), created\_at |
| `approvals` | id, deal\_id, status (pending, approved, denied, expired), decided\_at |
| `payments` | id, deal\_id, payout\_batch\_id, payout\_item\_id, status, created\_at |

**Python interfaces between parts.**

```python
negotiate(item: str, supplier_id: str) -> Deal | NoDeal
request_payment(deal_id: str) -> Decision        # the shop agent's only payment tool
evaluate(deal: Deal, invoice: Invoice) -> Decision  # policy engine, no model calls
pay(deal: Deal) -> Payment                       # payout, poll, record on invoice
```

**Endpoints the page uses.**

| Method | Path | Does |
| --- | --- | --- |
| GET | `/inventory` | Stock levels and reorder levels |
| PATCH | `/inventory/{item}` | Set stock; starts a run if it falls below the reorder level |
| GET | `/runs/{id}` | Transcript, deal, decision, payment status |
| GET | `/runs/latest` | The run currently on screen |
| GET | `/approvals?status=pending` | The approval queue |
| POST | `/approvals/{id}` | Body `{"approve": true}` or `false` |
| GET | `/audit` | Every offer, decision and payment, newest first |
| GET | `/policy` | The rules file, read-only |
| POST | `/scenarios/{a,b,c}` | Load a demo scenario's seed data and start it |
| POST | `/reset` | Wipe runs and restore seed data |
| GET | /runs/{id}/authorization | Download the authorization record for that run's payment as JSON |

**Authorization record.** One per payment, built from the `deals`, `decisions`, `approvals` and `payments` rows. It answers who authorized the payment, under which rules, and with what result.

```json
{
  "run_id": "run_014",
  "deal": {"supplier": "supplier@business.example.com", "item": "plantain_kg", "qty": 120, "unit_price": 0.85, "total": 102.00, "currency": "USD"},
  "policy_version": "2026-11-03",
  "rules_in_force": {"max_per_order": 150, "max_per_week": 400, "require_approval_above": 80, "max_unit_price_increase_pct": 20},
  "checks": [
    {"rule": "payee_allowlist", "passed": true},
    {"rule": "deal_integrity", "passed": true},
    {"rule": "duplicate", "passed": true},
    {"rule": "price_spike", "passed": true},
    {"rule": "per_order_cap", "passed": true},
    {"rule": "weekly_cap", "passed": true},
    {"rule": "approval_threshold", "passed": false}
  ],
  "decision": "needs_approval",
  "authorized_by": {"type": "owner", "at": "2026-11-08T14:02:11Z"},
  "paypal": {"invoice_id": "INV2-XXXX", "payout_batch_id": "XXXX", "payout_item_id": "XXXX", "status": "SUCCESS"}
}
```

For a payment the policy approved alone, `authorized_by.type` is `policy`. A blocked payment still gets a record, with no PayPal IDs.

## Web app

One page shows the whole system, so the demo video never leaves it except to show the PayPal sandbox. It refreshes the live run every 2 seconds.

**Stack for a solo build.** FastAPI renders the page with Jinja templates. A small plain JavaScript file polls `/runs/latest` every 2 seconds and posts approvals. AG Grid Community loads from a CDN. There is no React build step. Each payment row in the audit log links to its authorization record.

**Panels, top to bottom.**

- **Header:** project name, scenario buttons A, B and C, and a Reset button.
- **Inventory:** each item with stock, reorder level and an editable stock field. Low items are highlighted.
- **Live run:** the negotiation transcript as a chat between the two agents, each offer with its price, quantity and rationale. Offers rejected by the limits check show in a muted style with the reason.
- **Decision banner:** approved, needs approval or blocked, with the rule and reason in plain words.
- **Approval queue:** held payments with amount, supplier, reason held, and Approve and Deny buttons.
- **Policy:** the current rules, read-only.
- **Audit log:** a full-width AG Grid table of every event, newest first.

**AG Grid audit log columns.** Time, run, event type (offer, decision, approval, payment), actor, item, amount, status, rule, detail. Use Community features: sorting, filtering, a status cell renderer, and a quick search box. Row grouping is an Enterprise feature, so leave it out unless the sponsor provides a licence.

**Out of scope.** Login, mobile layout, editing rules from the page, more than one shop.

## Demo scenarios and video

Three scenarios show the three possible outcomes, and each one produces the same result on every run. The amounts below are demo values set to match the rules file.

| Scenario | Seed setup | Expected result |
| --- | --- | --- |
| A. Routine restock | Pouches drop to 40 (reorder at 100). Supplier quotes 500 at $0.12; last paid $0.10. | Shop counters, deal at $0.11 × 500 = $55. Approved, paid automatically, invoice marked paid. |
| B. Large order | Plantain drops low. Deal at 120 kg × $0.85 = $102. | Over the $80 threshold, so it is held. Owner taps Approve on screen, payout goes through. |
| C. Price spike | Oil supplier's floor price is $2.70 a litre; last paid $2.00. Deal for 20 L = $54. | Blocked by the price-spike rule (35% above last paid). No money moves; reason shown. |
| D. Payee swap (optional) | Supplier invoice names a different PayPal email from the approved one. | Blocked by the payee allowlist. Shows the defence against invoice fraud. |

**Video script, under 3 minutes.**

| Time | On screen | Point made |
| --- | --- | --- |
| 0:00–0:20 | The shop and its suppliers | Banks are asking who authorized an agent's payment. Small owners want agents that buy for them, within limits they set. |
| 0:20–0:40 | Policy panel | The owner's rules, enforced in code the model cannot change. |
| 0:40–1:20 | Scenario A, then the PayPal sandbox showing the payout | Agents negotiate, the shop agent pays, the invoice is marked paid. |
| 1:20–1:55 | Scenario B and the approval queue | A larger order waits for the owner, then completes. |
| 1:55–2:25 | Scenario C and the audit log | The agents agree a deal, the policy blocks it, and the log says why. Open one authorization record. |
| 2:25–2:50 | Architecture diagram, tools list | Agent Toolkit, Payouts, Invoicing, the LLM used, AG Grid, Render. |

Record the screen at 1080p with a voiceover. Use no copyrighted music. Upload to YouTube as public.

## Everything needed

The build needs seven accounts, a short list of libraries, a set of environment variables, and a submission checklist.

**Accounts.**

- [ ] Devpost account, registered for the hackathon as a solo entrant
- [ ] PayPal Developer account
- [ ] Two sandbox business accounts (`shop`, `supplier`), one REST app each, Payouts enabled on the shop app
- [ ] LLM API key
- [ ] GitHub account, public repo with an MIT licence
- [ ] Render account
- [ ] YouTube account for the demo video

**Libraries (Python version).**

- `paypal-agent-toolkit` for the supplier agent's invoice tools
- `httpx` or `requests` for Payouts and Invoicing REST calls
- `fastapi` and `uvicorn` for the backend
- `sqlite3` (built in) or `sqlmodel`
- The chosen LLM's SDK
- `pytest` for the policy tests
- AG Grid Community (frontend, loaded from a CDN or npm)

**Environment variables.**

```bash
PAYPAL_ENV=sandbox
SHOP_CLIENT_ID=...
SHOP_CLIENT_SECRET=...
SUPPLIER_CLIENT_ID=...
SUPPLIER_CLIENT_SECRET=...
SHOP_SANDBOX_EMAIL=...
SUPPLIER_SANDBOX_EMAIL=...
LLM_API_KEY=...
LLM_MODEL=...
DATABASE_URL=sqlite:///./data.db
```

Keep these out of the repo: commit a `.env.example` with empty values and put real values in Render's environment settings.

**Submission checklist.**

- [ ] Text description: problem, user, how it works, what is real and what is a stand-in
- [ ] Tools section: each PayPal API and AI tool, and what it does in the project
- [ ] Public GitHub repo with all source code, a licence visible in About, and setup steps that work from a clean clone
- [ ] Hosted URL on Render, with the Reset and scenario buttons working
- [ ] Demo video on YouTube, public, under 3 minutes, showing the app running end to end
- [ ] README states the Payouts caveat and lists the demo scenarios

## Timeline and working method

The solo plan runs nine days, with gates on Nov 4 and Nov 5 and submission on Nov 11, a day before the deadline.

&#91;embedded content: build timeline · Nov 3 to 12, one gate\]

One complete path comes first. Scenario A runs from the terminal with a fixed deal by Nov 5, the LLM negotiation replaces the fixed deal on Nov 6, and the page, hosting and the other scenarios follow.

**Checkpoints.**

- [ ] Nov 4 night: the plain payment script passes end to end in the sandbox
- [ ] Nov 5 night: Scenario A runs end to end in the terminal: fixed deal, policy check, payout, invoice marked paid
- [ ] Nov 6 night: the LLM negotiation produces Scenario A's deal, or negotiation is cut to one quote and one counter
- [ ] Nov 8 night: Scenarios A, B and C run on the Render URL
- [ ] Nov 11 night: submitted on Devpost

**Hours.** About 55 in total: 4 on weekday evenings, 6 on Thursday Nov 5, and 9 to 10 on Saturday and Sunday. Weekday work stops by 9:30pm. LeetCode and other side projects pause until Nov 12.

**Each evening.**

1. 15 minutes: pick one task from this doc and write down what done means.
2. About 3 hours: build in small AI-assisted steps, one function or module per prompt; run it, then commit.
3. 30 minutes: test, push to GitHub, and write the next day's first task and any blocker.

**Working with AI coding tools.**

- The repo holds this spec as `SPEC.md`, and every session starts by pointing the tool at it.
- One module with its tests per request; never the whole app at once.
- Every PayPal endpoint and field is checked against the REST docs before it runs.
- The policy rules are written, or reviewed line by line, by hand.
- `.env` stays out of prompts and commits.

**When stuck.** A PayPal setup problem that lasts more than 45 minutes goes to the PayPal Discord linked from the hackathon page. The developer advocates there are also judges.

**Cut order if behind.** Scenario D first, then extra AG Grid features, then the rationale lines in the transcript, then negotiation down to one quote and one counter. The real payout and the policy engine are never cut.

**If a helper joins.** Before Nov 7, the web page, built against the endpoints in the data model section. After Nov 7, the demo video, the Devpost text and scenario testing. The policy engine stays with Nathan.

**Add-backs, only if ahead of schedule.** A second supplier for price comparison, owner approval through a Telegram message, then webhooks in place of polling.

## Risks and judge questions

The two risks most likely to sink the entry are the payment path failing and the scope running past the deadline. Both have a set fallback.

| Risk | Effect | Fallback |
| --- | --- | --- |
| Payouts not available or failing in the sandbox | No real money movement; weak technical score | Fix by Nov 4 night. If still broken, have the supplier create an order and pay it as the sandbox buyer, and say so in the README |
| Negotiation takes too long to get right | Policy engine and page slip | On Nov 5, cut to one quote and at most one counter-offer |
| Model output differs between runs | Demo scenario fails on camera | Temperature 0, fixed seed data, limits in code, record several takes |
| Solo hours run short (classes, fatigue) | Features unfinished on Nov 9 | Follow the cut order in the timeline section; stop work by 9:30pm on weekdays |
| Hosting breaks close to the deadline | Judges cannot run it | Deploy on Nov 8, keep setup steps in the README as a second route |
| AI-written PayPal calls use wrong fields | Hours lost debugging | Check every call against the REST docs and test it the same day |

**Prepared answers.**

- **Why Payouts for a purchase?** It is the only sandbox route that lets an agent pay without a person approving each order. A production version uses a vaulted, merchant-initiated payment the owner sets up once.
- **Why not let the model decide whether to pay?** A model can be argued with. The rules file and the policy code cannot, and the audit log shows every check.
- **Who authorized this payment, and who is liable?** Every payment has an authorization record: the rules in force, every check, and whether the policy or the owner approved. That is the kind of purchase-intent proof card networks have started asking for.
- **What stops a malicious supplier agent?** It can only send offers. Offers outside the shop's limits are rejected in code, and payments go only to approved payees for the exact invoice total.
- **Is the negotiation real?** Both agents choose their own moves inside fixed limits. The transcript and rejected offers are on screen.
- **What if a payout fails?** The payment stays unpaid, the run is marked failed in the audit log, and the invoice stays open.
- **Why this user?** The demo shop is a real Nairobi snack business with recurring suppliers and small, frequent orders.
