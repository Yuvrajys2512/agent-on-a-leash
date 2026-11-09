// Agent on a Leash UI. Plain JS, no build step.
// Everything that comes from emails or the agent is inserted with textContent, never innerHTML.
"use strict";

const $ = (id) => document.getElementById(id);
const emails = new Map();   // email id -> email
const cards = new Map();    // action id -> card element
const INTERNAL = "acme.example"; // display hint only; the real check is on the server

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const isInternal = (addr) => addr.toLowerCase().split("@").pop() === INTERNAL;
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const clip = (s, n) => (s.length > n ? s.slice(0, n) + "…" : s);

// ---------- describing actions ----------
function subjectOf(id) { return emails.get(id)?.subject ?? id; }
function describe(a) {
  const p = a.params || {};
  switch (a.type) {
    case "label": return `Label “${subjectOf(a.email_id)}” as “${p.label}”`;
    case "move": return `Move “${subjectOf(a.email_id)}” to folder “${p.folder}”`;
    case "reply": return `Reply to ${emails.get(a.email_id)?.sender ?? "?"} (re: “${subjectOf(a.email_id)}”): “${clip(p.body || "", 90)}”`;
    case "forward": return `Forward “${subjectOf(a.email_id)}” to ${[].concat(p.to || []).join(", ") || "(nobody)"}`;
    default: return `${a.type} ${a.email_id}`;
  }
}

// ---------- inbox ----------
async function loadInbox() {
  const list = await api("/inbox");
  emails.clear();
  list.forEach((e) => emails.set(e.id, e));
  $("inbox-count").textContent = `${list.length} emails`;
  const box = $("inbox");
  box.replaceChildren(...list.map((e) => h("details", { class: "email" },
    h("summary", {}, h("b", {}, e.subject), h("div", { class: "from" }, e.sender),
      h("div", {},
        h("span", { class: "chip" + (isInternal(e.sender) ? "" : " ext") }, isInternal(e.sender) ? "internal" : "external"),
        e.folder !== "inbox" ? h("span", { class: "chip" }, "folder: " + e.folder) : null,
        e.labels.map((l) => h("span", { class: "chip" }, l)))),
    h("pre", {}, e.body))));
}

// ---------- live feed ----------
function clearFeed() {
  cards.clear();
  $("feed").replaceChildren(h("p", { class: "empty" }, "Press Run agent. Every action the agent wants to take appears here first."));
}
function addCard(ev) {
  const { action: a, decision: d } = ev;
  const feed = $("feed");
  feed.querySelector(".empty")?.remove();
  const simulated = (a.rationale || "").startsWith("[SIMULATED COMPROMISE]");
  const card = h("div", { class: `card ${d.verdict}` },
    h("div", {}, h("span", { class: `badge ${d.verdict}` }, d.verdict), " ", h("span", { class: "muted" }, `rule: ${d.rule_id}`),
      simulated ? h("span", { class: "sim-tag", title: "This action was injected by the test harness to simulate a compromised agent." }, "simulated attack") : null),
    h("div", { class: "what" }, describe(a)),
    a.rationale ? h("div", { class: "rationale" }, `Agent: “${a.rationale}”`) : null,
    h("div", { class: "why" }, d.reason, d.flags.length ? ` [${d.flags.join(", ")}]` : ""),
    h("div", { class: "slot" }));
  cards.set(a.id, card);
  feed.append(card);
  feed.scrollTop = feed.scrollHeight;
}
function addButtons(ev) {
  const card = cards.get(ev.action.id);
  if (!card) return;
  card.classList.add("waiting");
  const decide = async (what, btns) => {
    btns.forEach((b) => (b.disabled = true));
    try { await api(`/${what}/${ev.action.id}`, { method: "POST" }); } catch (e) { btns.forEach((b) => (b.disabled = false)); alert(e.message); }
  };
  const ok = h("button", { class: "approve" }, "Approve");
  const no = h("button", { class: "reject" }, "Reject");
  ok.addEventListener("click", () => decide("approve", [ok, no]));
  no.addEventListener("click", () => decide("reject", [ok, no]));
  card.querySelector(".slot").replaceChildren(h("div", { class: "buttons" }, ok, no));
  card.scrollIntoView({ block: "nearest" }); // make sure the buttons are visible
}
function finishCard(entry) {
  const card = cards.get(entry.action.id);
  if (!card) return;
  card.classList.remove("waiting");
  const text = {
    executed: ["Done", entry.detail],
    blocked: ["Blocked", "Nothing happened."],
    rejected_by_human: ["You rejected it", "Nothing happened."],
    failed: ["Failed", entry.detail],
  }[entry.outcome] || [entry.outcome, entry.detail];
  card.querySelector(".slot").replaceChildren(h("div", { class: "result" }, h("b", {}, text[0] + ". "), text[1]));
}

// ---------- audit log ----------
async function loadLog() {
  const rows = await api("/log");
  $("log-count").textContent = rows.length ? `${rows.length} entries` : "";
  const box = $("log");
  if (!rows.length) return box.replaceChildren(h("p", { class: "empty" }, "Nothing logged yet."));
  box.replaceChildren(...rows.map((r) => h("details", { class: "logrow" },
    h("summary", {},
      h("span", { class: "t" }, new Date(r.ts).toLocaleTimeString()),
      h("span", { class: `badge ${r.decision.verdict}` }, r.decision.verdict),
      h("span", { class: "sum" }, describe(r.action), " → ", h("b", {}, r.outcome.replaceAll("_", " ")))),
    h("div", { class: "explain" }, r.explanation),
    h("div", { class: "meta" }, `rule ${r.decision.rule_id} · approval: ${r.approval ?? "none"} · flags: ${r.decision.flags.join(", ") || "-"}`))));
}

// ---------- rules panel ----------
const WORD = { label: "label an email", move: "move an email", reply: "reply", forward: "forward" };
const OUTCOME = { allow: "it just happens", ask: "you are asked first", block: "it is blocked" };
async function loadRules() {
  const rules = await api("/rules");
  $("rules").replaceChildren(...rules.map((r) => {
    const w = r.when;
    const what = w.action_type.length ? w.action_type.map((t) => WORD[t] || t).join(" or ") : "do anything";
    const who = { internal: " to someone inside the company", external: " to someone outside the company", any: "" }[w.recipient];
    const flags = w.flags_all.length ? ` and it is flagged ${w.flags_all.join(" + ")}` : "";
    return h("div", { class: "rule" },
      h("div", { class: "id" }, r.id),
      h("div", { class: "sentence" }, `When the agent tries to ${what}${who}${flags}: `, h("span", { class: `badge ${r.verdict}` }, OUTCOME[r.verdict])),
      h("div", { class: "muted" }, r.description || r.reason));
  }));
}

// ---------- status + events ----------
function setStatus(text, cls = "") {
  const s = $("status");
  s.textContent = text;
  s.className = "pill " + cls;
  $("run-btn").disabled = cls === "running";
}
function banner(msg) { const b = $("banner"); b.hidden = !msg; b.textContent = msg || ""; }

function onEvent(ev) {
  switch (ev.kind) {
    case "run_started": banner(""); setStatus("agent running", "running"); break;
    case "run_finished": setStatus("finished"); loadInbox(); break;
    case "run_error": setStatus("error", "error"); banner("The agent stopped: " + ev.error); break;
    case "proposed": addCard(ev); break;
    case "pending": addButtons(ev); break;
    case "done": finishCard(ev.entry); loadLog(); loadInbox(); break;
    case "reset": clearFeed(); banner(""); setStatus("idle"); loadLog(); loadInbox(); break;
  }
}
function connect() {
  const es = new EventSource("/events");
  es.onopen = clearFeed;           // the server replays the whole run on (re)connect
  es.onmessage = (m) => onEvent(JSON.parse(m.data));
}

// ---------- which agent is running (so nobody mistakes a simulated attack for a real one) ----------
async function loadConfig() {
  try {
    const c = await api("/config");
    const label = c.agent === "fake" ? "Scripted agent: attack is simulated"
      : c.red_team ? "Real AI + simulated attack (red-team mode)" : "Real AI agent";
    const m = $("mode");
    m.textContent = label;
    m.className = "pill" + (c.agent === "fake" || c.red_team ? " sim" : "");
    m.title = c.agent === "fake" ? "No AI: a fixed script replays the whole story, including the trick."
      : c.red_team ? "The real AI works normally. After it reads the trick email the harness proposes what a compromised agent would do."
      : "The real AI decides what to do. Current models usually refuse the trick email.";
    m.hidden = false;
  } catch { /* the badge is a nicety; the page works without it */ }
}

// ---------- buttons ----------
$("run-btn").addEventListener("click", async () => {
  try { await api("/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
  catch (e) { banner(e.message); }
});
$("reset-btn").addEventListener("click", async () => { try { await api("/reset", { method: "POST" }); } catch (e) { banner(e.message); } });
$("rules-btn").addEventListener("click", () => { $("rules-panel").hidden = false; });
$("rules-close").addEventListener("click", () => { $("rules-panel").hidden = true; });

(async () => { await loadInbox(); await Promise.all([loadLog(), loadRules(), loadConfig()]); connect(); })();
