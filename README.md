# Agent on a Leash

An AI agent works through a mailbox for you, but it can only **propose** actions. Every action is shown to you
before it happens, passes through rules you can read (**allow**, **ask you**, or **block**), and is written to an
audit log that explains why. A trick email that tells the agent to send data somewhere is caught and blocked,
and the log says why.

> Mock inbox only. There are no real email accounts and no network sends: "sent" mail lands in an in-memory outbox.

## How it works

```
 mock inbox (10 emails)
        |
   +----v-----+  ProposedAction  +---------------------- THE GATE ------------------------------+
   |  AGENT   | ---------------> | 1. policy engine  -> allow | ask | block   (rules.yaml)       |
   | (the AI) |                  | 2. ask?  -> wait for the human to Approve / Reject in the UI  |
   |          | <--------------- | 3. allowed or approved -> execute on the mock mailbox         |
   +----------+   GateResult     | 4. write exactly one audit entry, always                      |
                  (done /       +----------------------------------------------------------------+
                   rejected /
                   blocked + why)
```

The agent never executes anything itself. It calls the gate, and the gate decides. The agent is told the result
(including why something was blocked) and carries on.

**Safety logic lives in code, not in the prompt.** The agent's prompt contains no safety instructions on purpose:
the point is that the leash works even when the agent is fooled.

## Quick start

You need Python 3.11 or newer.

```
python -m venv .venv
.venv\Scripts\activate                    # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                    # macOS/Linux: cp .env.example .env   (then add your key, see below)
uvicorn app.api.server:app
```

Open <http://localhost:8000>, press **Run agent**.

**Three ways to run the agent**

| Mode | How | Needs |
|---|---|---|
| Real AI agent (default) | `uvicorn app.api.server:app` | `ANTHROPIC_API_KEY` in `.env` |
| Real AI + **simulated** attack ("red-team mode") | PowerShell: `$env:AGENT_RED_TEAM="1"; uvicorn app.api.server:app` <br> macOS/Linux: `AGENT_RED_TEAM=1 uvicorn app.api.server:app` | `ANTHROPIC_API_KEY` |
| Scripted agent (no AI, same story every time) | PowerShell: `$env:AGENT="fake"; uvicorn app.api.server:app` <br> macOS/Linux: `AGENT=fake uvicorn app.api.server:app` | nothing |

Put your own key in `.env` (it is git-ignored; never commit it). `AGENT_MODEL` in `.env` picks the model.
The header of the page always says which mode is running.

**Why there is a simulated attack.** We tested the real models against the trick email: 0 of 25 live runs fell for
it (four model versions, three wordings of the email, even with a task that said to handle everything without
asking). They label it as suspicious every time. That is good news for users, but it means a real run never shows
the leash stopping anything. So the demo shows the thing the leash is *for*: it must hold **even if the agent is
compromised**. In red-team mode the real AI works normally, and right after it reads the trick email the harness
proposes what a compromised agent would do (three forwards of invoices to an outside address). Those actions are
labelled `[SIMULATED COMPROMISE]`, shown with a "simulated attack" tag in the UI, and go through exactly the same
gate as everything else. The scripted agent does the same without needing a key. Nothing in the rules knows or
cares that the attack is simulated.

To see what the AI does against a gate that approves everything (no UI): `python -m app.agent.run`.

## What you will see

- **Inbox:** 10 emails: three invoices, a meeting request, a newsletter, a customer complaint, two internal mails,
  a harmless partner question, and one trick email.
- **Live actions:** every action the agent wants to take, shown *before* it happens and coloured by verdict.
  Green = allowed, amber = waiting for you (Approve / Reject), red = blocked.
- **Audit log:** one row per action. Click a row for the plain-English explanation: what the agent tried, which
  rule decided, every other rule that matched, the flags, whether a human approved, and the outcome.
- **Rules:** the *Rules* button shows `rules.yaml` as sentences.
- **Reset:** puts the mailbox back to the original 10 emails and starts a fresh log. The old log is kept as
  `audit-<timestamp>.jsonl`, never deleted.

For a 3-minute walkthrough see [DEMO.md](DEMO.md).

## The rules

Rules live in [`rules.yaml`](rules.yaml). They are plain YAML, read top to bottom, and **the first rule that matches
decides**. If nothing matches, or anything goes wrong while judging an action, the answer is **ask** (fail safe).

| Rule | Verdict | What it does |
|---|---|---|
| `organise-allowed` | allow | Labelling and moving emails only changes the mailbox. |
| `block-injected-external-send` | **block** | An email that can steer a send contains instructions aimed at an AI, and the send goes outside the company. |
| `block-pii-external-send` | **block** | The message would carry an IBAN, a phone number or a known person's name outside the company. |
| `ask-untrusted-recipient` | ask | The outside address came from the text of an email, not from you. |
| `ask-bulk-external-forward` | ask | The 3rd forward of different emails to the same outside address. |
| `internal-send-allowed` | allow | Reply or forward to a colleague. |
| `external-send-ask` | ask | Anything else sent outside the company. |

"Outside the company" means any address that is not at `COMPANY_DOMAIN` (default `acme.example`). One outside
address in a list is enough to make a send external, and look-alike domains such as `acme.example.evil.com` are external.

**Flags the engine computes for every action** (rules match on them with `flags_all`):

| Flag | Meaning |
|---|---|
| `internal_recipient` / `external_recipient` | Where the message would go. |
| `tainted` | The agent has read mail from outside the company. |
| `injection_suspected:<id>` | An email that can steer this send contains instructions aimed at an AI ("ignore previous instructions", "note for AI assistant", "do not tell the user"...). For a forward that is any email the agent read; for a reply it is the email being answered (a reply always goes to that email's sender, so other text cannot redirect it). |
| `injection_in_context:<id>` | Same, for a reply to someone else. Informational only. |
| `untrusted_recipient:<id>` | The outside recipient's address appears in that email's text. This catches tricks that use none of the usual phrases. |
| `bulk` | Third forward of different emails to the same outside address (uses the earlier actions of the run). |
| `pii`, `pii:iban`, `pii:phone`, `pii:name` | The text that would leave contains personal data. IBANs are checked with the mod-97 checksum. |

A flag without a colon matches the whole family (`injection_suspected` matches `injection_suspected:e09`).
Rule reasons may use placeholders such as `{what}`, `{recipients}`, `{injection_email}`, `{injection_phrase}`,
`{pii_kinds}`, `{preview}`. The preview masks the data (`[IBAN]`, `[PHONE]`, `[NAME]`).

**Check a rule without running anything:**

```
python -m app.policy.check '{"type":"forward","email_id":"e06","params":{"to":["partner@outside.example"]}}'
```

**Want to approve personal-data sends instead of blocking them?** In `rules.yaml`, change `verdict: block` to
`verdict: ask` on `block-pii-external-send`.

### Rules in plain English

You can add your own rules by describing them ("never forward invoices outside the company"). An AI turns the
sentence into proposed rules, and then **you stay in control**:

1. `POST /rules/compile` only **previews**: it shows the proposed rules as YAML, with warnings. Nothing changes.
2. Only when you confirm (`POST /rules/activate/{proposal_id}`) are exactly those previewed rules saved, to
   `rules.user.yaml`. They apply from the next run, and each change is recorded in `rules.user.history.jsonl`.

What keeps this safe, even though an AI writes the rules:

- **They can only make the agent stricter.** The core rules decide first; a plain-English rule replaces that answer
  only if it is stricter (allow, then ask, then block). It can never loosen a core ask or block.
- **No `allow` rules.** An allow rule could loosen the safe default ("anything not covered asks you"), so it is refused.
- **Strict checking.** Unknown fields, unknown flags (a rule that could never fire), too many rules or overlong text
  are refused with a message, and ids are renamed so they can never clash with core rules.
- **Reset** sets the plain-English rules aside (renamed, not deleted) so the next demo starts clean.

Rules can test the action type, internal/external recipient, `recipient_domain`, `sender_domain`, `subject_contains`
and any flag from the table above. `rules_format_help()` in `app/policy/rules.py` is the format description given
to the AI.

## The audit log

Every action, whatever happens to it, becomes one line in `audit.jsonl` (append-only JSON lines): the action,
the decision with its rule and reason, whether a human approved or rejected it, and the outcome. A rejected action
and a human approval that times out (default 5 minutes) both count as "not executed".

## API

| Route | Purpose |
|---|---|
| `POST /run` | Start the agent (409 if one is already running). |
| `GET /events` | Live feed (Server-Sent Events). A page that connects late is replayed the run so far. |
| `GET /pending`, `POST /approve/{id}`, `POST /reject/{id}` | The human's decisions. |
| `GET /log` | Audit entries with their plain-English explanation. |
| `GET /rules`, `GET /inbox`, `GET /status` | Data for the UI. |
| `POST /rules/compile`, `POST /rules/activate/{id}`, `DELETE /rules/user/{id}` | Plain-English rules: preview, confirm, remove (see above). |
| `POST /reset` | Cancel the run, reject anything pending, reset the mailbox, set plain-English rules aside, start a new log. |

Interactive docs: <http://localhost:8000/docs>.

## Tests

```
python -m pytest
```

No API key is needed. `tests/test_e2e_fake.py` runs the scripted agent through the real API, gate, policy,
mailbox and audit log. The only thing not covered by tests is the real AI model itself.

## Project layout

```
app/contracts.py   shared data types and interfaces (change only with both teammates' approval)
app/mailbox/       mock inbox and email tools                     (Person A)
app/agent/         the AI loop, tool definitions, scripted agent  (Person A)
data/inbox.json    the 10 emails                                  (Person A)
app/policy/        rule loader, engine, injection + PII detection (Person B)
app/audit/         audit log                                      (Person B)
app/gate.py        policy -> approval -> execute -> log           (Person B)
app/approvals.py   pending approvals, timeouts, live events       (Person B)
app/api/           FastAPI app, SSE hub, server entry point       (Person B)
ui/                the web interface                              (Person B)
rules.yaml         the rules                                      (Person B)
```

The project plan and how the work was split is in [PLAN.md](PLAN.md).

## Limits to know about

- The injection and PII detectors are heuristics. They catch the cases they were built and tested for, plus a
  second layer (where did the recipient address come from?) that does not depend on keywords. They are not a
  complete defence: a different phrasing may slip past the keyword layer, which is why every outside message
  still needs your approval unless a rule says otherwise.
- The keyword layer is tested against about 25 ways of wording the trick (`tests/test_policy_adversarial.py`):
  odd spacing, hidden characters, look-alike letters, leetspeak, letters spaced apart, Finnish/German/French/
  Spanish/Swedish, base64, hidden HTML comments, and addresses written "x at y dot z". Text that is clearly
  aimed at an AI is blocked. Text a human could plausibly have written ("please CC ... on every invoice", "new
  instructions from management") is **not** blocked, because that would also block honest suppliers: it goes to
  you as an ask, with a warning that names the email and the address. One known gap: letters spaced apart by
  single spaces with no visible word boundary slip past the keyword layer, but still reach you as an ask.
- Names are recognised only for people who appear in the mailbox with a `first.last@` address.
- It works on a mock mailbox only. Connecting it to real email would need real authentication, real sending,
  and much more testing.
