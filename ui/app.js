// Agent on a Leash: the live UI. Plain JS, no build step, no network except our own API.
// Text that comes from emails or from the agent is only ever inserted with textContent (never innerHTML),
// so a malicious email cannot inject markup into the page.
"use strict";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const REDUCED = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
const SVGNS = "http://www.w3.org/2000/svg";

const S = {
  company: "acme.example",           // display hint only; the real check is on the server
  emails: new Map(), open: new Set(), logOpen: new Set(),
  cards: new Map(), dots: new Map(), pending: [],
  counts: { allow: 0, ask: 0, block: 0, leak: 0, total: 0 },
  queue: [], pumping: false, flashed: null, inboxKey: "", proposal: null,
};

// ---------- tiny helpers ----------
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}
function svg(tag, props = {}) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(props)) el.setAttribute(k, v);
  return el;
}
const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const isInternal = (addr) => addr.toLowerCase().split("@").pop() === S.company;
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage can be blocked; the page works without it */ } };

async function api(path, opts) {
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const d = data && data.detail;
    const err = new Error(typeof d === "string" ? d : (d && d.errors && d.errors.join(" ")) || r.statusText);
    err.status = r.status; err.warnings = (d && d.warnings) || [];
    throw err;
  }
  return data;
}
function toast(msg) {
  const t = h("div", { class: "toast" }, msg);
  $("toasts").append(t);
  setTimeout(() => t.remove(), 3200);
}

// ---------- theme + presenter mode ----------
function setTheme(t) { document.documentElement.dataset.theme = t; lsSet("theme", t); }
function setPresent(on) {
  document.documentElement.dataset.present = on ? "1" : "0";
  $("present-btn").setAttribute("aria-pressed", String(on));
  lsSet("present", on ? "1" : "0");
}
setTheme(lsGet("theme") || "dark");
setPresent(lsGet("present") === "1");

// ---------- describing actions ----------
const subjectOf = (id) => S.emails.get(id)?.subject ?? id;
const VERB = { label: "Label", move: "Move", reply: "Reply", forward: "Forward" };
const SHORT = { label: "LBL", move: "MOV", reply: "RE", forward: "FWD" };
function recipientsOf(a) {
  if (a.type === "forward") return [].concat(a.params?.to || []);
  if (a.type === "reply") { const s = S.emails.get(a.email_id)?.sender; return s ? [s] : []; }
  return [];
}
function describe(a) {
  const p = a.params || {};
  switch (a.type) {
    case "label": return `Label “${subjectOf(a.email_id)}” as “${p.label}”`;
    case "move": return `Move “${subjectOf(a.email_id)}” to “${p.folder}”`;
    case "reply": return `Reply to ${recipientsOf(a)[0] ?? "?"} about “${subjectOf(a.email_id)}”`;
    case "forward": return `Forward “${subjectOf(a.email_id)}” to ${recipientsOf(a).join(", ") || "(nobody)"}`;
    default: return `${a.type} ${a.email_id}`;
  }
}
// The reason text may quote the injected instruction: highlight that phrase so the audience sees it.
function reasonNodes(text) {
  const m = text.match(/^([\s\S]*?contains the instruction )"([^"]+)"([\s\S]*)$/);
  return m ? [m[1], h("mark", {}, `“${m[2]}”`), m[3]] : [text];
}

// ---------- inbox ----------
async function loadInbox() {
  const list = await api("/inbox");
  const key = JSON.stringify(list.map((e) => [e.id, e.folder, e.labels]));
  S.emails.clear(); list.forEach((e) => S.emails.set(e.id, e));
  $("inbox-count").textContent = `${list.length} emails`;
  if (key === S.inboxKey) return;   // nothing changed: keep the DOM (and any running highlight) as it is
  S.inboxKey = key;
  $("inbox").replaceChildren(...list.map((e) => {
    const internal = isInternal(e.sender);
    const row = h("div", { class: "email" + (S.open.has(e.id) ? " open" : ""), "data-id": e.id, tabindex: "0", role: "button", "aria-expanded": String(S.open.has(e.id)) },
      h("div", { class: "avatar" + (internal ? " int" : ""), "aria-hidden": "true" }, e.sender[0].toUpperCase()),
      h("div", { class: "subj" }, e.subject),
      h("div", { class: "from" }, e.sender),
      h("div", { class: "chips" },
        h("span", { class: "chip" + (internal ? "" : " ext") }, internal ? "internal" : "external"),
        e.folder !== "inbox" ? h("span", { class: "chip" }, "in " + e.folder) : null,
        e.labels.map((l) => h("span", { class: "chip tag" }, l))),
      h("div", { class: "body" }, e.body));
    const toggle = () => { row.classList.toggle("open"); row.setAttribute("aria-expanded", String(row.classList.contains("open"))); row.classList.contains("open") ? S.open.add(e.id) : S.open.delete(e.id); };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); } });
    return row;
  }));
  if (S.flashed && Date.now() < S.flashed.until) flashEmail(S.flashed.id, true);
}
function flashEmail(id, again = false) {
  if (!again) S.flashed = { id, until: Date.now() + 1100 };
  const el = document.querySelector(`.email[data-id="${CSS.escape(id)}"]`);
  if (!el) return;
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
  el.scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" });
}

// ---------- the leash diagram ----------
const lanes = { leash: $("p-leash"), mail: $("p-mail"), out: $("p-out") };
const dotsLayer = $("dots"), fxLayer = $("fx"), gateEl = $("gate");
let gateTimer = null;

function travel(g, path, ms, from = 0, to = 1) {
  return new Promise((resolve) => {
    const L = path.getTotalLength();
    const ease = (x) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);
    const t0 = performance.now(), dur = REDUCED ? 0 : ms;
    const step = (now) => {
      const k = dur === 0 ? 1 : Math.min(1, (now - t0) / dur);
      const p = path.getPointAtLength(L * (from + (to - from) * ease(k)));
      g.setAttribute("transform", `translate(${p.x} ${p.y})`);
      if (k < 1) requestAnimationFrame(step); else resolve();
    };
    requestAnimationFrame(step);
  });
}
function setGate(state, hold = 1500) {
  clearTimeout(gateTimer);
  gateEl.dataset.state = state;
  if (state && state !== "ask") gateTimer = setTimeout(() => { gateEl.dataset.state = S.pending.length ? "ask" : ""; }, hold);
}
function burst(x, y, color, text) {
  const ring = svg("circle", { cx: x, cy: y, r: 20, class: "fx-ring", stroke: color });
  fxLayer.append(ring);
  const a = ring.animate([{ r: 20, opacity: 0.95 }, { r: 95, opacity: 0 }], { duration: REDUCED ? 1 : 650, easing: "ease-out" });
  a.onfinish = () => ring.remove();
  if (text) {
    const t = svg("text", { x, y: y - 92, class: "fx-text", fill: color }); t.textContent = text;
    fxLayer.append(t);
    const b = t.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)", offset: 0.2 }, { opacity: 1, offset: 0.7 }, { opacity: 0, transform: "translateY(-6px)" }], { duration: REDUCED ? 1 : 1500 });
    b.onfinish = () => t.remove();
  }
}
function spawnDot(a, decision) {
  const g = svg("g", { class: "dot", transform: "translate(126 105)" });
  g.append(svg("circle", { r: 16 }));
  const label = svg("text", { y: 3.5 }); label.textContent = SHORT[a.type] || "?";
  g.append(label);
  dotsLayer.append(g);
  const entry = { g, decision, arrived: null };
  entry.arrived = travel(g, lanes.leash, 560).then(() => { g.dataset.v = decision.verdict; });
  S.dots.set(a.id, entry);
}
function fade(g, ms = 450) {
  g.style.transition = `opacity ${ms}ms`; g.style.opacity = "0";
  setTimeout(() => g.remove(), ms + 30);
}
async function finishDot(entry) {
  const d = S.dots.get(entry.action.id);
  if (!d) return;
  S.dots.delete(entry.action.id);
  await d.arrived;
  const { g } = d;
  // The gate lights up with what actually happened (never left stuck on "ask" after a replay).
  setGate(entry.outcome === "blocked" ? "block" : entry.outcome === "executed" ? "allow" : "");
  if (entry.outcome === "executed") {
    const outside = entry.decision.flags.includes("external_recipient");
    g.dataset.v = "allow";
    await travel(g, outside ? lanes.out : lanes.mail, 760);
    const end = (outside ? lanes.out : lanes.mail).getPointAtLength((outside ? lanes.out : lanes.mail).getTotalLength());
    burst(end.x, end.y, "var(--allow)");
    fade(g, 300);
  } else if (entry.outcome === "blocked") {
    burst(500, 105, "var(--block)", "BLOCKED");
    g.dataset.v = "block"; fade(g, 500);
  } else if (entry.outcome === "rejected_by_human") {
    burst(500, 105, "var(--faint)", "REJECTED");
    fade(g, 500);
  } else { burst(500, 105, "var(--ask)", "FAILED"); fade(g, 500); }
}

// ---------- scorecard ----------
function setMeter(id, value) {
  const el = $(id);
  if (el.textContent === String(value)) return;
  el.textContent = String(value);
  el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop");
}
function renderMeters() {
  setMeter("m-allow", S.counts.allow); setMeter("m-ask", S.counts.ask);
  setMeter("m-block", S.counts.block); setMeter("m-leak", S.counts.leak);
  $("m-safe-box").classList.toggle("bad", S.counts.leak > 0);
}
function countEntry(e) {
  S.counts.total++;
  if (e.outcome === "blocked") S.counts.block++;
  else if (e.approval === "approved" || e.approval === "rejected") S.counts.ask++;
  else S.counts.allow++;
  // The promise of the whole product: nothing leaves the company unless a human said yes.
  if (e.outcome === "executed" && e.decision.flags.includes("external_recipient") && e.approval !== "approved") S.counts.leak++;
  renderMeters();
}

// ---------- the decision dock ----------
function renderDock() {
  const wrap = $("dock-wrap");
  const item = S.pending[0];
  if (!item) { wrap.replaceChildren(); return; }
  const { action: a, decision: d } = item;
  const to = recipientsOf(a);
  const outside = to.some((r) => !isInternal(r));
  const body = a.params?.body;
  const approve = h("button", { class: "btn approve", type: "button" }, "Approve ", h("kbd", {}, "A"));
  const reject = h("button", { class: "btn reject", type: "button" }, "Reject ", h("kbd", {}, "X"));
  approve.addEventListener("click", () => decide(a.id, true));
  reject.addEventListener("click", () => decide(a.id, false));
  wrap.replaceChildren(h("div", { class: "dock", role: "group", "aria-label": "Needs your decision" },
    h("div", { class: "dock-head" }, h("span", {}, "Needs your decision"), S.pending.length > 1 ? h("span", { class: "more" }, `+${S.pending.length - 1} waiting`) : null),
    h("div", { class: "dock-title" }, `${VERB[a.type] || a.type} an email`),
    h("dl", { class: "kv" },
      h("dt", {}, "About"), h("dd", {}, subjectOf(a.email_id)),
      to.length ? [h("dt", {}, "To"), h("dd", {}, to.join(", "), outside ? h("span", { class: "outsidetag" }, "outside the company") : null)] : null,
      a.rationale ? [h("dt", {}, "Agent says"), h("dd", {}, `“${a.rationale}”`)] : null),
    body ? h("div", { class: "preview" }, body) : null,
    h("p", { class: "why" }, ...reasonNodes(d.reason)),
    h("div", { class: "dock-actions" }, approve, reject)));
}
async function decide(id, approve) {
  const btns = document.querySelectorAll(".dock-actions .btn");
  btns.forEach((b) => (b.disabled = true));
  try { await api(`/${approve ? "approve" : "reject"}/${id}`, { method: "POST" }); }
  catch (e) { btns.forEach((b) => (b.disabled = false)); toast(e.message); }
}

// ---------- live activity feed ----------
function clearFeed() {
  S.cards.clear(); S.pending = []; S.dots.forEach((d) => d.g.remove()); S.dots.clear();
  S.counts = { allow: 0, ask: 0, block: 0, leak: 0, total: 0 };
  $("feed").replaceChildren(h("p", { class: "empty" }, "Press Run agent. Every action the agent wants to take shows up here before it happens."));
  $("summary-wrap").replaceChildren(); renderDock(); renderMeters(); setGate("");
}
function addCard(ev) {
  const { action: a, decision: d } = ev;
  const feed = $("feed");
  feed.querySelector(".empty")?.remove();
  const simulated = (a.rationale || "").startsWith("[SIMULATED COMPROMISE]");
  const card = h("div", { class: `card ${d.verdict} fresh` },
    h("div", { class: "card-top" },
      h("span", { class: `badge ${d.verdict}` }, d.verdict),
      h("span", { class: "verb" }, VERB[a.type] || a.type),
      simulated ? h("span", { class: "simtag", title: "Injected by the test harness to simulate a compromised agent." }, "simulated attack") : null,
      h("span", { class: "rule" }, d.rule_id)),
    h("div", { class: "what" }, describe(a)),
    a.rationale ? h("div", { class: "quote" }, `Agent: “${a.rationale.replace("[SIMULATED COMPROMISE] ", "")}”`) : null,
    h("div", { class: "reasontxt" }, ...reasonNodes(d.reason)),
    h("div", { class: "slot" }));
  S.cards.set(a.id, card);
  feed.prepend(card);               // newest on top
  feed.scrollTop = 0;
}
function markWaiting(ev) {
  const card = S.cards.get(ev.action.id);
  if (!card) return;
  card.classList.add("waiting");
  card.querySelector(".slot").replaceChildren(h("div", { class: "result hold" }, h("span", { class: "pulse" }), h("b", {}, "Waiting for your decision")));
}
function finishCard(entry) {
  const card = S.cards.get(entry.action.id);
  if (!card) return;
  card.classList.remove("waiting");
  const approved = entry.approval === "approved";
  const [cls, head, tail] = {
    executed: ["good", approved ? "You approved it. Done." : "Done.", entry.detail],
    blocked: ["bad", "Stopped.", "Nothing happened."],
    rejected_by_human: ["bad", "You rejected it.", "Nothing happened."],
    failed: ["bad", "Failed.", entry.detail],
  }[entry.outcome] || ["", entry.outcome, entry.detail];
  card.querySelector(".slot").replaceChildren(h("div", { class: `result ${cls}` }, h("b", {}, head), clip(tail || "", 120)));
}
function showSummary() {
  const c = S.counts;
  if (!c.total) return;
  const clean = c.leak === 0;
  $("summary-wrap").replaceChildren(h("div", { class: "summary" },
    h("h3", {}, "Run complete"),
    h("div", { class: "big " + (clean ? "ok" : "bad") }, clean ? "Nothing left the company without your yes." : `${c.leak} message(s) left without your yes.`),
    h("p", {}, `${c.total} actions: ${c.allow} ran by themselves, ${c.ask} needed your decision, ${c.block} were stopped. Every one is in the audit trail.`)));
}

// ---------- audit trail ----------
async function loadLog() {
  const rows = (await api("/log")).slice().reverse();   // newest first
  $("log-count").textContent = rows.length ? `${rows.length} entries` : "";
  const box = $("log");
  if (!rows.length) { box.replaceChildren(h("p", { class: "empty" }, "Nothing logged yet.")); return; }
  box.replaceChildren(...rows.map((r) => {
    const id = r.action.id, d = r.decision;
    const det = h("details", { class: "logrow", "data-id": id },
      h("summary", {},
        h("span", { class: "t" }, new Date(r.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })),
        h("span", { class: `badge ${d.verdict}` }, d.verdict),
        h("span", { class: "sum" }, describe(r.action)),
        h("span", { class: "out" }, "→ ", h("b", {}, r.outcome.replaceAll("_", " ")), r.approval === "approved" ? " (you approved)" : r.approval === "rejected" ? " (you rejected)" : "")),
      h("div", { class: "explain" }, ...reasonNodes(r.explanation),
        h("div", { class: "meta" }, `rule ${d.rule_id} · flags: ${d.flags.join(", ") || "none"}`)));
    if (S.logOpen.has(id)) det.open = true;
    det.addEventListener("toggle", () => (det.open ? S.logOpen.add(id) : S.logOpen.delete(id)));
    return det;
  }));
}

// ---------- rules drawer (plain English in, human confirms, then it applies) ----------
const WORD = { label: "label an email", move: "move an email", reply: "reply", forward: "forward" };
function sentence(r) {
  const w = r.when;
  const what = w.action_type.length ? w.action_type.map((t) => WORD[t] || t).join(" or ") : "do anything";
  const bits = [`When the agent tries to ${what}`];
  if (w.recipient === "internal") bits.push("to someone inside the company");
  if (w.recipient === "external") bits.push("to someone outside the company");
  if (w.recipient_domain?.length) bits.push("to " + w.recipient_domain.join(" or "));
  if (w.sender_domain?.length) bits.push("about mail from " + w.sender_domain.join(" or "));
  if (w.subject_contains?.length) bits.push("with " + w.subject_contains.map((s) => `“${s}”`).join(" or ") + " in the subject");
  if (w.flags_all?.length) bits.push("and " + w.flags_all.join(" + ") + " is detected");
  return bits.join(" ") + ":";
}
const OUTCOME = { allow: "it just happens", ask: "you are asked first", block: "it is blocked" };
function ruleCard(r, removable) {
  return h("div", { class: "rulecard" },
    h("div", { class: "rid" }, h("span", {}, r.id),
      removable ? (() => { const b = h("button", { class: "rm", type: "button" }, "Remove"); twoStep(b, "Click again to remove", () => removeRule(r.id)); return b; })() : null),
    h("div", { class: "sent" }, sentence(r) + " ", h("span", { class: `badge ${r.verdict}` }, OUTCOME[r.verdict])),
    h("div", { class: "desc" }, r.description || r.reason));
}
async function loadRules() {
  const rules = await api("/rules");
  const mine = rules.filter((r) => r.source === "user"), core = rules.filter((r) => r.source !== "user");
  $("rules-user").replaceChildren(...(mine.length ? mine.map((r) => ruleCard(r, true)) : [h("p", { class: "hint" }, "None yet. Describe one above.")]));
  $("rules-core").replaceChildren(...core.map((r) => ruleCard(r, false)));
}
let lastFocus = null;
const BEHIND = ["header.top", ".stage", "main.board"];   // not reachable by keyboard or screen reader while the drawer is open
function setBehindInert(on) { BEHIND.forEach((s) => { const el = document.querySelector(s); if (el) el.inert = on; }); }
function openDrawer() {
  lastFocus = document.activeElement; setBehindInert(true);
  $("scrim").hidden = false; $("drawer").hidden = false; loadRules(); $("rule-text").focus();
}
function closeDrawer() {
  if ($("drawer").hidden) return;
  $("scrim").hidden = true; $("drawer").hidden = true; setBehindInert(false); lastFocus?.focus?.();
}
// A destructive button needs two clicks within 3 seconds, so a stray click on stage cannot wipe the run.
function twoStep(btn, label, action) {
  let armed = null; const original = btn.textContent;
  btn.addEventListener("click", () => {
    if (armed) { clearTimeout(armed); armed = null; btn.textContent = original; action(); return; }
    btn.textContent = label;
    armed = setTimeout(() => { armed = null; btn.textContent = original; }, 3000);
  });
}
async function previewRule() {
  const text = $("rule-text").value.trim();
  const box = $("rule-result"), btn = $("rule-preview");
  if (!text) { box.replaceChildren(h("div", { class: "preview-card err" }, "Type a rule first.")); return; }
  btn.disabled = true; btn.textContent = "Thinking…";
  try {
    const p = await api("/rules/compile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    S.proposal = p.proposal_id;
    const confirm = h("button", { class: "btn primary", type: "button", onclick: () => confirmRule(p.proposal_id) }, "Confirm: apply from the next run");
    const discard = h("button", { class: "btn ghost", type: "button", onclick: () => { S.proposal = null; box.replaceChildren(); } }, "Discard");
    box.replaceChildren(h("div", { class: "preview-card" },
      h("div", { class: "big" }, "Here is what I understood"),
      ...p.previews.map((s) => h("div", {}, s)),
      p.warnings.length ? h("ul", {}, p.warnings.map((w) => h("li", {}, w))) : null,
      h("details", {}, h("summary", { class: "hint" }, "Show the rule file"), h("pre", {}, p.yaml)),
      h("div", { class: "row" }, confirm, discard)));
  } catch (e) {
    const msg = e.status === 501 ? "Rules in plain English are not set up on this server." : e.message;
    box.replaceChildren(h("div", { class: "preview-card err" }, h("div", { class: "big" }, "That rule can't be used"), msg,
      e.warnings?.length ? h("ul", {}, e.warnings.map((w) => h("li", {}, w))) : null));
  } finally { btn.disabled = false; btn.textContent = "Preview rule"; }
}
async function confirmRule(id) {
  try {
    await api(`/rules/activate/${id}`, { method: "POST" });
    $("rule-text").value = ""; $("rule-result").replaceChildren();
    toast("Rule added. It applies from the next run.");
    loadRules();
  } catch (e) { toast(e.message); }
}
async function removeRule(id) {
  try { await api(`/rules/user/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("Rule removed."); loadRules(); }
  catch (e) { toast(e.message); }
}

// ---------- status + mode ----------
function setStatus(text, cls = "") {
  const s = $("status");
  s.textContent = text; s.className = "pill " + cls;
  $("run-btn").disabled = cls === "running";
}
function banner(msg) { const b = $("banner"); b.hidden = !msg; b.textContent = msg || ""; }
async function loadConfig() {
  try {
    const c = await api("/config");
    if (c.company_domain) S.company = c.company_domain;
    const label = c.agent === "fake" ? "Scripted agent: attack is simulated"
      : c.red_team ? "Real AI + simulated attack" : "Real AI agent";
    const m = $("mode");
    m.textContent = label; m.className = "pill" + (c.agent === "fake" || c.red_team ? " sim" : ""); m.hidden = false;
    m.title = c.agent === "fake" ? "No AI: a fixed script replays the whole story, including the trick."
      : c.red_team ? "The real AI works normally. After it reads the trick email, the harness proposes what a compromised agent would do."
      : "The real AI decides what to do. Current models usually refuse the trick email.";
  } catch { /* the badge is a nicety */ }
}

// ---------- the event queue: pace the story so an audience can follow it ----------
const DELAY = { proposed: 480, done: 380, run_started: 250 };
function enqueue(ev) {
  if (ev.kind === "reset") S.queue.length = 0;
  S.queue.push(ev);
  if (!S.pumping) pump();
}
async function pump() {
  S.pumping = true;
  while (S.queue.length) {
    const ev = S.queue.shift();
    try { handle(ev); } catch (e) { console.error("event failed", ev, e); }
    const behind = S.queue.length > 6;           // catching up after a page reload: no delays
    await sleep(behind || REDUCED ? 0 : DELAY[ev.kind] || 0);
  }
  S.pumping = false;
}
function handle(ev) {
  switch (ev.kind) {
    case "run_started": banner(""); $("summary-wrap").replaceChildren(); setStatus("Agent running", "running"); break;
    case "run_finished": setStatus("Finished"); showSummary(); loadInbox(); break;
    case "run_error": setStatus("Error", "error"); banner("The agent stopped: " + ev.error); break;
    case "proposed": addCard(ev); flashEmail(ev.action.email_id); spawnDot(ev.action, ev.decision); break;
    case "pending": S.pending.push(ev); markWaiting(ev); renderDock(); setGate("ask", 0); break;
    case "done": {
      S.pending = S.pending.filter((p) => p.action.id !== ev.entry.action.id);
      renderDock(); finishCard(ev.entry); countEntry(ev.entry);
      finishDot(ev.entry); loadLog(); loadInbox();
      if (!S.pending.length && gateEl.dataset.state === "ask") setGate("");
      break;
    }
    case "rules_changed": loadRules(); break;
    case "reset": clearFeed(); banner(""); setStatus("Idle"); loadLog(); loadInbox(); loadRules(); break;
  }
}
function connect() {
  const es = new EventSource("/events");
  es.onopen = () => { S.queue.length = 0; clearFeed(); };   // the server replays the whole run on (re)connect
  es.onmessage = (m) => enqueue(JSON.parse(m.data));
}

// ---------- buttons + keyboard ----------
$("run-btn").addEventListener("click", async () => {
  try { await api("/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
  catch (e) { banner(e.message); }
});
twoStep($("reset-btn"), "Click again to reset", async () => { try { await api("/reset", { method: "POST" }); } catch (e) { banner(e.message); } });
$("rules-btn").addEventListener("click", openDrawer);
$("rules-close").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
$("rule-preview").addEventListener("click", previewRule);
$("theme-btn").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
$("present-btn").addEventListener("click", () => setPresent(document.documentElement.dataset.present !== "1"));
$("rule-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) previewRule(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeDrawer(); return; }
  if (e.target.matches("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if ((k === "a" || k === "x") && S.pending[0] && $("drawer").hidden) decide(S.pending[0].action.id, k === "a");
  else if (k === "p") $("present-btn").click();
  else if (k === "t") $("theme-btn").click();
  else if (k === "l") openDrawer();
});

(async () => {
  await loadConfig(); await loadInbox();
  await Promise.all([loadLog(), loadRules()]);
  clearFeed(); connect();
})();
