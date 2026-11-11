# Devpost submission text

Copy each section into the matching Devpost field. Replace the video link once the video is on YouTube.

---

## Project name
Restocking Agents with Spending Guardrails

## Tagline (short description)
AI agents negotiate a small shop's restocks and pay over PayPal, but plain code the owner controls decides whether money moves.

## Links
- **Live demo:** https://restocking-agents.onrender.com
- **Code (MIT):** https://github.com/Ndubito/shauriana
- **Video:** _YouTube link_

---

## Inspiration
Agentic commerce needs agents that can actually pay. But no small business owner will give an agent unlimited spending power, and an agent that can't pay is only a recommendation engine. Banks and card networks are asking the same question from the other side: when an agent pays, *who authorized it, and under which rules?*

We wanted to build the missing layer: hard spending rules the agent can't override, with the owner pulled in only above a risk level they choose, and a record for every payment showing exactly why it was allowed. Our test user is a real kind of business: a Nairobi plantain chip maker restocking packaging, plantain and cooking oil from a few recurring suppliers.

## What it does
When an item runs low, a **shop agent** and a **supplier agent** (two Gemini models) negotiate the restock over up to four rounds of structured offers. Each side knows only its own limits, and code rejects any offer outside them. When they agree, the supplier sends a real **PayPal invoice**.

The shop agent then asks to pay. Its only payment tool sends the request through a **policy engine**: plain code with no AI, reading the owner's rules file. The engine checks seven rules in order: approved payee, the invoice matches the deal, no double payment, no price spike over 20%, a $150 per-order cap, a $400 rolling weekly cap, and owner approval above $80. Then one of three things happens:
- **Approved:** a **PayPal payout** goes to the supplier, is polled until it succeeds, and the supplier marks its invoice paid.
- **Held:** the owner sees an Approve / Decline card. No answer in 24 hours counts as no.
- **Blocked:** no money moves, and the page says why in plain words.

Every offer, decision, approval and payment goes into an audit log (AG Grid). Each payment gets an **authorization record**: the rules in force, every check and its result, whether the policy or the owner approved it, and the PayPal IDs.

The web app keeps the owner's attention where it belongs. A delivery-tracker style progress bar shows what's happening right now, and the agents' chat and the policy checks stay folded away until you want them.

## How we built it
- **PayPal sandbox, two business accounts.** The supplier uses **Invoicing v2** to create, send and record payments on invoices. The shop uses **Payouts v1** to pay and poll. OAuth tokens are cached per app. The deal ID is the payout's `sender_batch_id`, so PayPal itself refuses to pay the same deal twice, on top of our own duplicate rule.
- **Gemini** (`gemini-3.5-flash-lite`, falling back to `gemini-3.5-flash`) via the `google-genai` SDK. A Pydantic response schema makes every agent turn a validated offer object. `temperature=0` and a fixed seed keep demos repeatable.
- **The policy engine** is a small pure Python function. Its tests follow the spec rule by rule: $150.01 blocked and $150.00 allowed, 21% blocked and 20% allowed, $80.01 held and $80.00 approved, and supplier text saying "ignore your rules" leaving the decision unchanged.
- **FastAPI + SQLite + plain JavaScript + AG Grid Community**, hosted on **Render** from a Blueprint, with a site-wide rate limit protecting the API key on the public demo.
- 81 offline tests with fake PayPal and model clients, plus live sandbox runs for every feature, written up in the repo's build log.
- Built solo by Nathan Achar, with AI coding assistance (Claude Code). The policy rules and their tests were reviewed by hand.

## Challenges we ran into
- **Making "the agent can't override the rules" actually true.** The decision and a "payment reserved" row are written in one database transaction, so even two simultaneous requests can't pay a deal twice. We left out PayPal's idempotency header on purpose: with it, a repeated payout returns the original instead of being rejected.
- **A paid run that became "blocked".** A rejected repeat request was overwriting a finished run's outcome. We caught it in live testing, and now only open runs can change.
- **The agents were too good at their jobs.** In the price-spike scenario, the shop agent refused the supplier's new price, so the run ended with no deal and never reached the policy. We gave the shop its real stock situation (running out stops production) and both agents a "close within your limits on the last round" rule. Now the agent agrees a price its own limits allow, and the owner's rule still catches the 35% jump. That's the whole point of the project.
- **A quantity bug the owner found.** With oil at 0, the shop asked for 20 L while needing at least 25 L, so no deal could ever pass its own limits. The fix: order the larger of the usual order and the shortfall.
- **Model capacity.** The newest Gemini Flash models returned 503 "high demand" on build day. A model fallback list keeps runs working.
- **Antivirus HTTPS scanning** broke Python's certificate checks locally. We verify against the OS trust store instead of disabling checks.

## Accomplishments that we're proud of
- Three outcomes that happen the same way on every run, against the real PayPal sandbox, with two live models negotiating each time. The seed data is chosen so the outcome can't vary, whatever price the agents agree.
- An authorization record for every payment, including blocked ones, that answers "who authorized this, under which rules?"
- A UI that lets things happen in the background and shows detail only on request.

## What we learned
- Keep the model and the money apart. The model's job is negotiating. Deciding whether to pay is a deterministic function with tests at every boundary.
- PayPal details matter: a recorded invoice payment shows `MARKED_AS_PAID`, not `PAID`; `sender_batch_id` is a free duplicate guard; and a payout to a non-sandbox email ends as `UNCLAIMED`.
- Live testing finds what unit tests don't: race conditions, buttons replaced mid-click, and agents behaving sensibly in ways that break a demo.

## What's next
- Replace Payouts with a **vaulted, merchant-initiated payment** the owner sets up once. Payouts is our sandbox stand-in for a business purchase.
- Move the supplier's invoice calls onto the **PayPal Agent Toolkit**, add a second supplier for price comparison, approvals by message (e.g. Telegram), and **webhooks** in place of polling.

## Built with
`paypal-payouts-api` · `paypal-invoicing-api` · `python` · `fastapi` · `sqlite` · `gemini` · `google-genai` · `ag-grid` · `javascript` · `render` · `pytest`

---

## What's real and what's a stand-in (for the description or a comment)
- **Real:** PayPal sandbox payouts and invoices; two live models negotiating; the policy engine and limits; approvals with expiry; the audit log and authorization records.
- **Payouts caveat:** the Payouts API is meant for rebates and payouts to people. It's used here because it's the only sandbox route that lets an agent pay without a person approving each order. Production would use a vaulted, merchant-initiated payment.
- **Stand-ins:** both agents run in one process, though the supplier is written as a separate company that only sees offers and its own prices. The supplier's invoices use the Invoicing REST API directly rather than the Agent Toolkit. Sandbox only, in USD.
