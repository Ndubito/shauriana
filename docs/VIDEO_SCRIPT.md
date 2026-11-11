# Demo video script (under 3 minutes)

Spec requirements: 1080p screen recording with a voiceover, no copyrighted music, public on YouTube.

Each run takes 30–60 s, mostly the ~20 s sandbox payout plus a few seconds per model call. **Don't wait on camera.** Start the scenario, talk over the tracker while it moves, and cut the dead time when editing. Speaking times below are for the finished video.

## Before you record
1. **Wake the site:** open https://restocking-agents.onrender.com about 2 minutes early, since the free instance can take up to a minute to wake. Then click **Reset demo**.
2. **Browser:** a 1920×1080 window, zoom 110–125% so text reads well. Hide bookmarks and other tabs.
3. **PayPal tab:** in a second tab, log in to **sandbox.paypal.com** as the **supplier** sandbox account and open its activity list. You'll show the payout landing in scene 3.
4. **Do a full dry run** of A, B and C once. Then click **Reset demo** before you hit record.
5. **The authorization record** opens as JSON in a new tab. A JSON viewer extension makes it readable, or zoom in on it.

---

## Scene 1 · 0:00–0:20 · The problem
**On screen:** Activity page in its empty state ("All stocked up"), then the **Inventory** page.

> "Agents are learning to buy things. But no small business owner will hand an AI unlimited spending power, and banks now ask who authorized an agent's payment. This is a Nairobi plantain chip maker with a few regular suppliers. Two AI agents restock for it, and the owner stays in control of the money."

## Scene 2 · 0:20–0:40 · The owner's rules
**On screen:** click **Policy**. Move the cursor slowly down the 7 rules.

> "These are the owner's rules, checked in code before any money moves: approved payees only, the invoice must match the deal, no double payments, no price spikes over 20 percent, caps per order and per week, and anything over 80 dollars waits for the owner. The AI can't change these, and nothing a supplier writes can either."

## Scene 3 · 0:40–1:20 · Scenario A: paid automatically
**On screen:** click **A Routine restock** in the sidebar. The tracker shows "Agents are negotiating…". Open **Show negotiation** while it runs. When it says **Paid $55.00**, open **Show policy checks** (7 ✓). Switch to the PayPal tab and refresh to show the payout received.

> "Pouches dropped below the reorder level, so the shop agent asks for 500. The supplier agent quotes 12 cents, the shop counters at 11, and they agree. The supplier sends a real PayPal invoice. The shop asks to pay, the policy engine checks all seven rules, they pass, and a PayPal payout goes out. The supplier marks its invoice paid. Here it is in the PayPal sandbox."

## Scene 4 · 1:20–1:55 · Scenario B: the owner decides
**On screen:** back in the app, click **B Large order**. The tracker reaches **"Waiting for your approval"** with the amber step and the **1** badge on Activity. Pause on the card, then click **Approve**. Show **"Paid $… · Approved by you"**.

> "A bigger plantain order. The agents agree on about a hundred dollars. That's over the owner's 80-dollar threshold, so the payment waits, with the reason right here. The owner approves, and it pays. If they'd declined, or not answered within 24 hours, no money would move."

## Scene 5 · 1:55–2:25 · Scenario C: blocked, and the audit trail
**On screen:** click **C Price spike**. When it shows **"Blocked: no money moved"**, open **Show policy checks** (✕ No price spike, the rest "not reached"). Click **Open the authorization record** and show the JSON briefly. Close it, scroll down, open **Audit log → Show**, and type "blocked" in the search box.

> "Now the oil supplier raised its price to 2.70, 35 percent above the last order. The shop agent agreed, because the price was within its own limits. But the owner's rule says no more than 20 percent, so it's blocked and no money moves. Every payment, even a blocked one, gets an authorization record: the rules in force, every check, and who approved it. And everything goes into the audit log."

## Scene 6 · 2:25–2:50 · How it's built
**On screen:** the GitHub README: the "How it works" diagram, then the "Tools used" table.

> "Under the hood: two Gemini agents with structured outputs negotiate. A policy engine in plain code decides. PayPal Invoicing and Payouts move the money in the sandbox. AG Grid shows the audit log, and it's hosted on Render. The model negotiates. Code decides whether money moves."

## Closing card · 2:50–2:58
**On screen:** a title card with the project name, **restocking-agents.onrender.com** and **github.com/Ndubito/shauriana**. Optionally add "Built by Nathan Achar".

---

## If something goes wrong while recording
- **"A job is already running":** wait for the current run to finish (watch the tracker), then click again.
- **"Demo limit reached":** the site allows 20 restocks per hour. Wait the minutes it says, or record locally (`python -m uvicorn app.web.main:create_app_from_env --factory --port 8000`).
- **A run is slow:** that's Gemini taking 5–10 s on a turn. Keep talking, then cut the gap when editing.
- **You need the same result every take:** run locally with `python -m scripts.run_scenario a --fixed` (no AI, fixed deal) as a last resort. Say so if you use it.
