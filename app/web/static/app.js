// Polls the API every 2 s and renders the page. Plain JavaScript, no build step.
// All model- or supplier-written text is inserted with textContent, never as HTML.
"use strict";

const POLL_MS = 2000;
const ITEM_NAMES = {
  packaging_pouch_100g: "Packaging pouch 100 g",
  plantain_kg: "Plantain",
  cooking_oil_l: "Cooking oil",
};
const STAGE_LABELS = {
  negotiating: "negotiating", invoicing: "invoicing", checking: "policy check",
  awaiting_owner: "waiting for owner", paying: "paying", paid: "paid", blocked: "blocked",
  denied: "denied", no_deal: "no deal", failed: "failed",
};

const $ = (sel) => document.querySelector(sel);
const money = (v) => (v == null ? "" : "$" + Number(v).toFixed(2));
const price = (v) => (v == null ? "" : "$" + Number(v).toFixed(2));
const itemName = (i) => ITEM_NAMES[i] || i;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4000);
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

// ---------- inventory ----------
let editing = false;
let lastInventory = null;
function renderInventory(rows) {
  if (editing) return; // don't clobber a field the user is typing in
  const key = JSON.stringify(rows);
  if (key === lastInventory) return; // unchanged: keep the same inputs so clicks land
  lastInventory = key;
  const tbody = $("#inventory");
  tbody.replaceChildren(...rows.map((r) => {
    const input = el("input", { type: "number", min: "0", value: r.stock, "aria-label": `Stock of ${itemName(r.item)}` });
    input.addEventListener("focus", () => (editing = true));
    input.addEventListener("blur", () => (editing = false));
    input.addEventListener("change", async () => {
      editing = false;
      try {
        const res = await api("PATCH", `/inventory/${r.item}`, { stock: Number(input.value) });
        if (res.run_started) toast(`${itemName(r.item)} is low: restock started`);
        else if (res.note) toast(res.note);
      } catch (e) { toast(e.message); }
      lastInventory = null;
      refresh();
    });
    return el("tr", { class: r.low ? "low" : "" },
      el("td", {}, itemName(r.item), " ", r.low ? el("span", { class: "low-badge" }, "LOW") : null),
      el("td", { class: "num" }, input),
      el("td", { class: "num" }, r.reorder_level),
      el("td", { class: "num" }, price(r.last_paid_unit_price)),
      el("td", { class: "muted" }, r.unit));
  }));
}

// ---------- live run ----------
let lastTranscript = null;
function renderRun(data) {
  const { runner, detail } = data;
  document.querySelectorAll(".btn.scenario, #reset").forEach((b) => (b.disabled = runner.busy));

  const stage = $("#stage");
  const transcript = $("#transcript");
  if (!detail) {
    stage.textContent = runner.busy ? "starting…" : "idle";
    stage.className = "pill" + (runner.busy ? " live" : "");
    $("#run-id").textContent = "";
    lastTranscript = null;
    transcript.replaceChildren(el("p", { class: "empty" }, "Pick a scenario to start a restock."));
    renderBanner(null);
    if (runner.last_error) toast(runner.last_error);
    return;
  }
  $("#run-id").textContent = `${detail.run.id} · ${itemName(detail.run.item)}`;
  stage.textContent = STAGE_LABELS[detail.stage] || detail.stage;
  const live = ["negotiating", "invoicing", "checking", "paying"].includes(detail.stage);
  stage.className = "pill " + (live ? "live" : detail.stage);

  const msgs = detail.offers.map((o) => {
    const who = o.from === "shop" ? "🏪 Shop agent" : "🚚 Supplier agent";
    let terms = o.action.replace("_", " ").toUpperCase();
    if (o.action === "request") terms += ` ${o.qty} by ${o.delivery_date}`;
    else if (o.action !== "walk_away") terms += ` ${o.qty} @ ${price(o.unit_price)} · ${o.delivery_date}`;
    return el("div", { class: `msg ${o.from}${o.valid ? "" : " rejected"}` },
      el("div", { class: "who" }, `${who} · round ${o.round}`),
      el("div", { class: "terms" }, terms),
      o.rationale ? el("div", { class: "why" }, `“${o.rationale}”`) : null,
      o.valid ? null : el("div", { class: "reject-reason" }, `Rejected by limits check: ${o.reason}`));
  });
  if (detail.deal) {
    msgs.push(el("div", { class: "deal-line" },
      `Deal: ${detail.deal.qty} × ${price(detail.deal.unit_price)} = ${money(detail.deal.total)}` +
      (detail.deal.invoice_id ? ` · invoice ${detail.deal.invoice_id}` : "")));
  } else if (detail.stage === "no_deal") {
    msgs.push(el("div", { class: "deal-line" }, "No deal: nothing to pay."));
  }
  const key = JSON.stringify([detail.run.id, detail.offers, detail.deal, detail.stage]);
  if (key !== lastTranscript) { // redraw only on change: keeps the reader's scroll position
    lastTranscript = key;
    const atBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
    transcript.replaceChildren(...msgs);
    if (atBottom) transcript.scrollTop = transcript.scrollHeight;
  }

  renderBanner(detail);
  if (runner.last_error) toast(runner.last_error);
}

function renderBanner(detail) {
  const banner = $("#banner");
  if (!detail || !detail.decision) { banner.className = "banner hidden"; return; }
  const d = detail.decision;
  const icons = { approved: "✅", needs_approval: "⏸️", blocked: "⛔" };
  const titles = { approved: "Approved by policy", needs_approval: "Needs owner approval", blocked: "Blocked: no money moves" };
  let title = titles[d.status];
  let look = d.status;
  if (d.status === "needs_approval" && detail.approval && detail.approval.status !== "pending") {
    const answered = {
      approved: ["Approved by owner", "approved", "✅"],
      denied: ["Denied by owner: no money moves", "blocked", "⛔"],
      expired: ["Approval expired: denied", "blocked", "⛔"],
    }[detail.approval.status];
    if (answered) { [title, look] = answered; icons[look] = answered[2]; }
  }
  banner.className = `banner ${look}`;
  $("#banner-icon").textContent = icons[look] || "";
  $("#banner-status").textContent = title;
  $("#banner-reason").textContent = d.rule ? `${d.rule.replace(/_/g, " ")}: ${d.reason}` : d.reason;

  const meta = $("#banner-meta");
  const parts = [el("span", {}, `${detail.deal ? money(detail.deal.total) : ""} · checks ${d.checks.map((c) => (c.passed ? "✓" : "✗")).join(" ")}`)];
  if (detail.payment) {
    parts.push(el("span", {}, `payout ${detail.payment.status}` + (detail.payment.payout_batch_id ? ` (${detail.payment.payout_batch_id})` : "")));
    if (detail.payment.error) parts.push(el("span", {}, detail.payment.error));
  }
  parts.push(el("a", { href: `/runs/${detail.run.id}/authorization`, target: "_blank", rel: "noopener" }, "authorization record"));
  meta.replaceChildren(...parts);
}

// ---------- approvals ----------
let lastApprovals = null;
function renderApprovals(rows) {
  // Only redraw when the queue changed, so a click never lands on a button being replaced.
  const key = JSON.stringify(rows);
  if (key === lastApprovals) return;
  lastApprovals = key;
  const box = $("#approvals");
  if (!rows.length) { box.replaceChildren(el("p", { class: "empty" }, "Nothing waiting for the owner.")); return; }
  box.replaceChildren(...rows.map((a) => {
    const answer = (approve) => async (ev) => {
      ev.target.closest(".btns").querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await api("POST", `/approvals/${a.id}`, { approve });
        toast(approve ? "Approved: paying the supplier" : "Denied: no payment");
        refresh();
      } catch (e) { toast(e.message); lastApprovals = null; refresh(); }
    };
    return el("div", { class: "approval" },
      el("div", {},
        el("div", { class: "amt" }, `${money(a.amount)} · ${itemName(a.item)}`),
        el("div", { class: "meta" }, `${a.qty} @ ${price(a.unit_price)} · ${a.supplier}`),
        el("div", { class: "meta" }, `Held: ${a.reason}`)),
      el("div", { class: "btns" },
        el("button", { class: "btn approve", onclick: answer(true) }, "Approve"),
        el("button", { class: "btn deny", onclick: answer(false) }, "Deny")));
  }));
}

// ---------- audit grid ----------
const STATUS_CLASS = {
  approved: "good", SUCCESS: "good", accept: "good", paid: "good",
  needs_approval: "warn", pending: "warn", PENDING: "warn", CREATED: "warn",
  blocked: "bad", denied: "bad", expired: "bad", rejected: "bad", FAILED: "bad", UNCLAIMED: "bad", walk_away: "bad",
};
function statusRenderer(p) {
  if (!p.value) return "";
  const span = document.createElement("span");
  span.className = `status-badge ${STATUS_CLASS[p.value] || "neutral"}`;
  span.textContent = p.value;
  return span;
}
function authRenderer(p) {
  if (!p.value) return "";
  const a = document.createElement("a");
  a.href = p.value; a.target = "_blank"; a.rel = "noopener"; a.textContent = "record";
  return a;
}

let grid;
let lastAudit = "";
function initGrid() {
  if (!window.agGrid) { $("#audit-grid").textContent = "AG Grid failed to load."; return; }
  if (agGrid.ModuleRegistry && agGrid.AllCommunityModule) agGrid.ModuleRegistry.registerModules([agGrid.AllCommunityModule]);
  grid = agGrid.createGrid($("#audit-grid"), {
    columnDefs: [
      { field: "time", headerName: "Time", width: 110, sort: "desc",
        valueFormatter: (p) => (p.value ? new Date(p.value).toLocaleTimeString() : "") },
      { field: "run_id", headerName: "Run", width: 170 },
      { field: "event", headerName: "Event", width: 105 },
      { field: "actor", headerName: "Actor", width: 100 },
      { field: "item", headerName: "Item", width: 150, valueFormatter: (p) => itemName(p.value) },
      { field: "amount", headerName: "Amount", width: 105, type: "rightAligned", valueFormatter: (p) => money(p.value) },
      { field: "status", headerName: "Status", width: 130, cellRenderer: statusRenderer },
      { field: "rule", headerName: "Rule", width: 150 },
      { field: "detail", headerName: "Detail", flex: 1, minWidth: 260, tooltipField: "detail" },
      { field: "authorization", headerName: "Auth", width: 85, cellRenderer: authRenderer, sortable: false, filter: false },
    ],
    defaultColDef: { sortable: true, filter: true, resizable: true },
    rowData: [],
    animateRows: true,
  });
  $("#quick").addEventListener("input", (e) => grid.setGridOption("quickFilterText", e.target.value));
}
function renderAudit(rows) {
  const key = JSON.stringify(rows);
  if (!grid || key === lastAudit) return; // only redraw when something changed
  lastAudit = key;
  grid.setGridOption("rowData", rows);
}

// ---------- polling ----------
let inflight = false;
async function refresh() {
  if (inflight) return;
  inflight = true;
  try {
    const [run, inv, appr, audit] = await Promise.all([
      api("GET", "/runs/latest"), api("GET", "/inventory"),
      api("GET", "/approvals?status=pending"), api("GET", "/audit"),
    ]);
    renderRun(run); renderInventory(inv); renderApprovals(appr); renderAudit(audit);
  } catch (e) {
    toast(`Can't reach the server: ${e.message}`);
  } finally {
    inflight = false;
  }
}

document.querySelectorAll(".btn.scenario").forEach((b) =>
  b.addEventListener("click", async () => {
    try { await api("POST", `/scenarios/${b.dataset.scenario}`); toast(`Scenario ${b.dataset.scenario.toUpperCase()} started`); refresh(); }
    catch (e) { toast(e.message); }
  }));
$("#reset").addEventListener("click", async () => {
  try { await api("POST", "/reset"); toast("Reset: seed data restored"); refresh(); }
  catch (e) { toast(e.message); }
});

initGrid();
refresh();
setInterval(refresh, POLL_MS);
