// Polls the API every 2 s and renders three views: Activity, Inventory, Policy.
// Plain JavaScript, no build step. All model- or supplier-written text goes in with
// textContent, never as HTML. Each block only redraws when its data changed, so open
// dropdowns, scroll positions and button clicks are never disturbed.
"use strict";

const POLL_MS = 2000;
const ITEM_NAMES = { packaging_pouch_100g: "Packaging pouches", plantain_kg: "Plantain", cooking_oil_l: "Cooking oil" };
const RULE_NAMES = {
  payee_allowlist: "Approved payee", deal_integrity: "Invoice matches the deal", duplicate: "Not already paid",
  price_spike: "No price spike", per_order_cap: "Within the per-order cap", weekly_cap: "Within the weekly cap",
  approval_threshold: "Under the approval limit",
};
const RULE_ORDER = Object.keys(RULE_NAMES);

const $ = (s) => document.querySelector(s);
const money = (v) => (v == null ? "" : "$" + Number(v).toFixed(2));
const itemName = (i) => ITEM_NAMES[i] || i;
const timeOf = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of children) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
const changed = (() => { const seen = {}; return (k, v) => { const s = JSON.stringify(v); if (seen[k] === s) return false; seen[k] = s; return true; }; })();
const forget = (k) => changed(k, Symbol()); // force the next render of block k

let toastTimer;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 4000);
}

async function api(method, path, body) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

// ---------- navigation ----------
function showView() {
  const view = (location.hash || "#activity").slice(1);
  const known = ["activity", "inventory", "policy"].includes(view) ? view : "activity";
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${known}`));
  document.querySelectorAll(".nav-link").forEach((a) => a.classList.toggle("active", a.dataset.view === known));
}
window.addEventListener("hashchange", showView);
const goActivity = () => { if (location.hash !== "#activity") location.hash = "#activity"; };

// ---------- tracker ----------
function stepsFor(d) {
  const held = d.decision && d.decision.status === "needs_approval";
  const steps = [
    { key: "low", label: "Low stock" },
    { key: "deal", label: "Negotiated" },
    { key: "rules", label: "Rules checked" },
    ...(held ? [{ key: "owner", label: "Your approval" }] : []),
    { key: "paid", label: "Paid" },
  ];
  const at = (k) => steps.findIndex((s) => s.key === k);
  // [index of the active step, its state]; steps before it are done
  const where = {
    negotiating: [at("deal"), "current"], invoicing: [at("rules"), "current"], checking: [at("rules"), "current"],
    awaiting_owner: [at("owner"), "wait"], paying: [at("paid"), "current"], paid: [steps.length, "done"],
    no_deal: [at("deal"), "fail"], blocked: [at("rules"), "fail"], denied: [at("owner"), "fail"], failed: [at("paid"), "fail"],
  }[d.stage] || [at("deal"), "current"];
  if (d.stage === "blocked") steps[at("rules")].label = "Blocked";
  if (d.stage === "no_deal") steps[at("deal")].label = "No deal";
  if (d.stage === "denied") steps[at("owner")].label = d.approval && d.approval.status === "expired" ? "Expired" : "Denied";
  return steps.map((s, i) => ({ ...s, state: i < where[0] ? "done" : i === where[0] ? where[1] : "todo" }));
}

function headlineFor(d) {
  const sup = d.run.supplier_name;
  const total = d.deal ? money(d.deal.total) : "";
  const request = d.offers.find((o) => o.action === "request");
  const lastWords = [...d.offers].reverse().find((o) => o.action !== "request");
  switch (d.stage) {
    case "negotiating": return [`Agents are negotiating with ${sup}`, lastWords ? "Working on a price…" : "Sending the request…"];
    case "invoicing": return ["Deal agreed", `Waiting for ${sup}'s invoice`];
    case "checking": return ["Checking your spending rules", `${total} to ${sup}`];
    case "awaiting_owner": return ["Waiting for your approval", d.decision.reason];
    case "paying": return [`Paying ${sup}`, "Sending the PayPal payout…"];
    case "paid": {
      const who = d.decision && d.decision.status === "needs_approval" ? "Approved by you" : "Approved by your rules";
      return [`Paid ${total} to ${sup}`, `${who} · invoice marked paid`];
    }
    case "blocked": return ["Blocked: no money moved", d.decision.reason, "bad"];
    case "denied": return d.approval && d.approval.status === "expired"
      ? ["Approval expired: no payment", "Nobody answered within the time limit."]
      : ["You declined this payment", "No money moved."];
    case "no_deal": return ["No deal this time", "The agents couldn't agree within their limits. Nothing to pay."];
    case "failed": return ["Payment didn't go through", (d.payment && d.payment.error) || "The payout failed.", "bad"];
    default: return [d.stage, ""];
  }
}

function renderTracker(data) {
  const { runner, detail: d } = data;
  document.querySelectorAll(".demo-btn").forEach((b) => (b.disabled = runner.busy));
  $("#tracker-empty").classList.toggle("hidden", !!d);
  $("#tracker-body").classList.toggle("hidden", !d);
  if (runner.last_error && changed("err", runner.last_error)) toast(runner.last_error);
  if (!d) return;

  // A new run starts minimal: close the drawers the user opened on the previous one.
  if (changed("runId", d.run.id)) { $("#d-chat").open = false; $("#d-checks").open = false; }

  const live = ["negotiating", "invoicing", "checking", "paying"].includes(d.stage);
  const request = d.offers.find((o) => o.action === "request");
  const qty = d.deal ? d.deal.qty : request && request.qty;

  if (changed("head", [d.run.id, d.stage, d.decision, d.approval, d.deal, d.payment, d.offers.length])) {
    const [headline, detail, tone] = headlineFor(d);
    $("#t-sub").textContent = `${itemName(d.run.item)}${qty ? ` · ${qty} ${d.run.unit}` : ""} · started ${timeOf(d.run.started_at)}`;
    $("#t-headline").textContent = headline;
    $("#t-detail").textContent = detail || "";
    $("#t-detail").className = "t-detail" + (tone === "bad" ? " bad" : "");
    $("#t-amount").textContent = d.deal ? money(d.deal.total) : "";
    $("#t-pulse").classList.toggle("hidden", !live);
    $("#t-steps").replaceChildren(...stepsFor(d).map((s) =>
      el("li", { class: `step ${s.state}` }, el("div", { class: "bar" }), el("div", { class: "label" }, s.label))));
  }

  // Approve / deny sits inside the tracker when this run is waiting for you.
  const pending = d.approval && d.approval.status === "pending" ? d.approval : null;
  if (changed("action", [d.run.id, pending && pending.id])) {
    const box = $("#t-action");
    box.classList.toggle("hidden", !pending);
    if (pending) {
      box.replaceChildren(
        el("div", { class: "a-text" },
          el("div", { class: "a-title" }, `${money(d.deal.total)} to ${d.run.supplier_name}`),
          el("div", {}, `${d.deal.qty} ${d.run.unit} at ${money(d.deal.unit_price)} · over your approval limit`)),
        el("button", { class: "btn deny", onclick: answer(pending.id, false) }, "Decline"),
        el("button", { class: "btn approve", onclick: answer(pending.id, true) }, "Approve"));
    }
  }

  // Negotiation drawer (closed by default; stays the way the user left it).
  if (changed("chat", [d.run.id, d.offers, d.deal])) {
    const rounds = Math.max(0, ...d.offers.map((o) => o.round));
    const rejected = d.offers.filter((o) => !o.valid).length;
    $("#d-chat-sum").textContent = `Show negotiation · ${rounds} round${rounds === 1 ? "" : "s"}` +
      (rejected ? ` · ${rejected} offer${rejected === 1 ? "" : "s"} rejected by limits` : "");
    $("#chat").replaceChildren(...d.offers.map((o) => {
      let terms = o.action.replace("_", " ");
      terms = terms[0].toUpperCase() + terms.slice(1);
      if (o.action === "request") terms += ` ${o.qty} ${d.run.unit} by ${o.delivery_date}`;
      else if (o.action !== "walk_away") terms += ` ${o.qty} @ ${money(o.unit_price)} · ${o.delivery_date}`;
      return el("div", { class: `msg ${o.from}${o.valid ? "" : " rejected"}` },
        el("div", { class: "who" }, `${o.from === "shop" ? "Shop agent" : "Supplier agent"} · round ${o.round}`),
        el("div", { class: "terms" }, terms),
        o.rationale ? el("div", { class: "why" }, `“${o.rationale}”`) : null,
        o.valid ? null : el("div", { class: "reject-reason" }, `Rejected by the limits check: ${o.reason}`));
    }));
  }

  // Policy checks drawer.
  if (changed("checks", [d.run.id, d.decision])) {
    const drawer = $("#d-checks");
    drawer.classList.toggle("hidden", !d.decision);
    if (d.decision) {
      const ran = d.decision.checks;
      const passed = ran.filter((c) => c.passed).length;
      $("#d-checks-sum").textContent = `Show policy checks · ${passed} of ${RULE_ORDER.length} passed`;
      const byRule = Object.fromEntries(ran.map((c) => [c.rule, c.passed]));
      $("#checks").replaceChildren(
        ...RULE_ORDER.map((r) => {
          const state = !(r in byRule) ? "skip" : byRule[r] ? "pass" : "fail" + (r === "approval_threshold" ? " warn" : "");
          const mark = state === "skip" ? "–" : state === "pass" ? "✓" : r === "approval_threshold" ? "!" : "✕";
          return el("div", { class: `check ${state}` }, el("span", { class: "mark" }, mark),
            RULE_NAMES[r], state === "skip" ? el("span", { class: "muted small" }, " (not reached)") : null);
        }),
        el("div", { class: "reason" }, d.decision.reason),
        el("a", { href: `/runs/${d.run.id}/authorization`, target: "_blank", rel: "noopener" }, "Open the authorization record"));
    }
  }
}

function answer(id, approve) {
  return async (ev) => {
    ev.target.parentElement.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      await api("POST", `/approvals/${id}`, { approve });
      toast(approve ? "Approved: paying the supplier" : "Declined: no payment");
    } catch (e) { toast(e.message); }
    forget("action"); forget("others"); refresh();
  };
}

// ---------- other approvals (older runs still waiting) ----------
function renderApprovals(rows, currentRunId) {
  const badge = $("#nav-badge");
  badge.textContent = rows.length;
  badge.classList.toggle("hidden", rows.length === 0);
  const others = rows.filter((a) => a.run_id !== currentRunId);
  if (!changed("others", others)) return;
  const box = $("#other-approvals");
  box.classList.toggle("hidden", others.length === 0);
  box.replaceChildren(el("h2", {}, "Also waiting for you"), ...others.map((a) =>
    el("div", { class: "row" },
      el("div", { class: "grow" }, el("b", {}, `${money(a.amount)} · ${itemName(a.item)}`),
        el("div", { class: "muted small" }, a.reason)),
      el("button", { class: "btn deny", onclick: answer(a.id, false) }, "Decline"),
      el("button", { class: "btn approve", onclick: answer(a.id, true) }, "Approve"))));
}

// ---------- history ----------
const OUTCOME = {
  paid: ["Paid", "good"], blocked: ["Blocked", "bad"], denied: ["Declined", "bad"], no_deal: ["No deal", "neutral"],
  failed: ["Failed", "bad"], held: ["Waiting for you", "warn"], deal: ["In progress", "neutral"], started: ["In progress", "neutral"],
};
function renderHistory(runs, currentRunId) {
  const earlier = runs.filter((r) => r.id !== currentRunId);
  if (!changed("history", earlier)) return;
  const list = $("#history");
  if (!earlier.length) { list.replaceChildren(el("li", { class: "muted" }, "No earlier restocks.")); return; }
  list.replaceChildren(...earlier.map((r) => {
    const [label, tone] = OUTCOME[r.outcome] || [r.outcome, "neutral"];
    return el("li", {},
      el("span", { class: "when" }, timeOf(r.started_at)),
      el("span", { class: "what" }, `${itemName(r.item)}${r.qty ? ` · ${r.qty} ${r.unit}` : ""}`),
      r.total != null ? el("span", {}, money(r.total)) : null,
      el("span", { class: `tag ${tone}` }, label),
      r.outcome === "blocked" && r.reason ? el("span", { class: "why" }, r.reason) : null);
  }));
}

// ---------- inventory ----------
let editing = false;
function renderInventory(rows) {
  if (editing || !changed("inventory", rows)) return;
  $("#inventory").replaceChildren(...rows.map((r) => {
    const input = el("input", { type: "number", min: "0", value: r.stock, "aria-label": `Stock of ${itemName(r.item)}` });
    input.addEventListener("focus", () => (editing = true));
    input.addEventListener("blur", () => (editing = false));
    input.addEventListener("change", async () => {
      editing = false;
      try {
        const res = await api("PATCH", `/inventory/${r.item}`, { stock: Number(input.value) });
        if (res.run_started) { toast(`${itemName(r.item)} is low: the agents are restocking`); goActivity(); }
        else if (res.note) toast(res.note);
        else toast("Stock updated");
      } catch (e) { toast(e.message); }
      forget("inventory"); refresh();
    });
    return el("tr", { class: r.low ? "low" : "" },
      el("td", {}, el("div", { class: "item-name" }, itemName(r.item)), el("div", { class: "item-unit" }, `per ${r.unit}`)),
      el("td", { class: "num" }, input),
      el("td", { class: "num" }, r.reorder_level),
      el("td", { class: "num" }, r.reorder_qty),
      el("td", { class: "num" }, money(r.max_unit_price)),
      el("td", { class: "num" }, money(r.last_paid_unit_price)));
  }));
}

// ---------- audit grid (created the first time the log is opened) ----------
const STATUS_CLASS = {
  approved: "good", SUCCESS: "good", accept: "good", paid: "good",
  needs_approval: "warn", pending: "warn", PENDING: "warn", CREATED: "warn",
  blocked: "bad", denied: "bad", expired: "bad", rejected: "bad", FAILED: "bad", UNCLAIMED: "bad", walk_away: "bad",
};
let grid = null;
let auditRows = [];
function cellBadge(p) {
  if (!p.value) return "";
  const s = document.createElement("span");
  s.className = `status-badge ${STATUS_CLASS[p.value] || "neutral"}`; s.textContent = p.value; return s;
}
function cellLink(p) {
  if (!p.value) return "";
  const a = document.createElement("a"); a.href = p.value; a.target = "_blank"; a.rel = "noopener"; a.textContent = "record"; return a;
}
function ensureGrid() {
  if (grid || !window.agGrid) return;
  if (agGrid.ModuleRegistry && agGrid.AllCommunityModule) agGrid.ModuleRegistry.registerModules([agGrid.AllCommunityModule]);
  grid = agGrid.createGrid($("#audit-grid"), {
    columnDefs: [
      { field: "time", headerName: "Time", width: 105, sort: "desc", valueFormatter: (p) => (p.value ? new Date(p.value).toLocaleTimeString() : "") },
      { field: "run_id", headerName: "Run", width: 165 },
      { field: "event", headerName: "Event", width: 100 },
      { field: "actor", headerName: "Actor", width: 95 },
      { field: "item", headerName: "Item", width: 140, valueFormatter: (p) => itemName(p.value) },
      { field: "amount", headerName: "Amount", width: 100, type: "rightAligned", valueFormatter: (p) => money(p.value) },
      { field: "status", headerName: "Status", width: 125, cellRenderer: cellBadge },
      { field: "rule", headerName: "Rule", width: 140 },
      { field: "detail", headerName: "Detail", flex: 1, minWidth: 240, tooltipField: "detail" },
      { field: "authorization", headerName: "Auth", width: 80, cellRenderer: cellLink, sortable: false, filter: false },
    ],
    defaultColDef: { sortable: true, filter: true, resizable: true },
    rowData: auditRows,
  });
  $("#quick").addEventListener("input", (e) => grid.setGridOption("quickFilterText", e.target.value));
}
$("#d-audit").addEventListener("toggle", (e) => { if (e.target.open) { ensureGrid(); forget("audit"); renderAudit(auditRows); } });
function renderAudit(rows) {
  auditRows = rows;
  $("#audit-count").textContent = `· ${rows.length} event${rows.length === 1 ? "" : "s"}`;
  if (grid && $("#d-audit").open && changed("audit", rows)) grid.setGridOption("rowData", rows);
}

// ---------- polling ----------
let inflight = false;
async function refresh() {
  if (inflight) return;
  inflight = true;
  try {
    const [run, inv, appr, runs, audit] = await Promise.all([
      api("GET", "/runs/latest"), api("GET", "/inventory"), api("GET", "/approvals?status=pending"),
      api("GET", "/runs?limit=8"), api("GET", "/audit"),
    ]);
    const current = run.detail ? run.detail.run.id : null;
    renderTracker(run); renderApprovals(appr, current); renderHistory(runs, current);
    renderInventory(inv); renderAudit(audit);
  } catch (e) {
    toast(`Can't reach the server: ${e.message}`);
  } finally {
    inflight = false;
  }
}

document.querySelectorAll(".demo-btn.scenario").forEach((b) => b.addEventListener("click", async () => {
  try { await api("POST", `/scenarios/${b.dataset.scenario}`); toast(`Scenario ${b.dataset.scenario.toUpperCase()} started`); goActivity(); refresh(); }
  catch (e) { toast(e.message); }
}));
$("#reset").addEventListener("click", async () => {
  try { await api("POST", "/reset"); toast("Demo reset"); forget("inventory"); refresh(); }
  catch (e) { toast(e.message); }
});

showView();
refresh();
setInterval(refresh, POLL_MS);
