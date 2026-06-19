# decisions.md — BillToBoxAgent

> **Purpose.** Confirmed answers to the CLAUDE.md "Open Questions" plus the
> conventions resolved in task 1. Output of WORKPLAN task 2. This file **unblocks
> the config (task 4) and systemd (task 22) tasks**. Companion to
> [`CONVENTIONS.md`](CONVENTIONS.md).
>
> **Confirmed with the project owner:** 2026-06-19.

---

## 1. Confirmed decisions

| # | Question | Decision | Config / effect |
|---|---|---|---|
| Conventions (task 1) | | | |
| C1 | Config format | **YAML** (`config.yaml` + Pydantic + `SecretStr`), not `.env` | `config.example.yaml`; inline `Environment=BTB_CONFIG=` in systemd |
| C2 | Remote access | **Join the Caddy multi-app scheme** (`tailscale cert`), not `tailscale serve` | dashboard binds `127.0.0.1`; Caddy fronts TLS |
| Accounting & filing | | | |
| 1 | Fiscal year start month | **January — calendar quarters** | `FISCAL_YEAR_START_MONTH=1` |
| 2 | Year-folder label format | **`2026`** (plain year) | `FY_LABEL_PREFIX=""` |
| 3 | Date driving the quarter | **Invoice date** | `QUARTER_DATE_FIELD=invoice_date` |
| 4 | Local PDF backup on the Pi | **No** — Drive is system of record; short-lived processing cache only | no local backup dir |
| Sources | | | |
| 5 | Mailboxes to poll | **Gmail + Outlook**, **Doccle stubbed** | `POLLING_SOURCES=gmail,outlook` (doccle present but NotImplemented) |
| Billtobox send | | | |
| 6 | Send transport | **SMTP with app password** (keeps mail scopes read-only) | `SMTP_HOST/PORT/USERNAME/PASSWORD` |
| 7 | Account type | **Billtobox** (email-ingest path identical either way) | recorded for portal refs |
| Scheduling | | | |
| 8 | Polling schedule | **Daily at 07:00**, `Persistent=true` | `OnCalendar=*-*-* 07:00:00` |
| Storage | | | |
| 9 | Google Drive root | **Agent creates a new `Invoices` folder** on first run and records its ID | `drive.file` scope; no pre-supplied ID needed |
| 10 | Storage backend | **Google Drive** (My Drive), not Cloud Storage | Drive API v3 |
| Observability (your request) | | | |
| 11 | Agent-action logging | **Two-layer: live SSE log stream + structured `agent_events` table + `get_agent_events` tool** | see §2 |
| Dashboard | | | |
| 12 | Dashboard port | **public `:8003` → internal `127.0.0.1:9003`** (next free in HEC's 8000/8001/8002 scheme) | `web.host=127.0.0.1`, `web.port=9003`; Caddy `:8003` |
| 13 | Confidence threshold | **0.85** (CLAUDE.md default; not changed) | `CONFIDENCE_THRESHOLD=0.85` |

---

## 2. Observability design (decision #11, expanded)

You asked for "a good logger system (see HomeEnergyCenter) that lets me inspect the
agent's actions in the webpage; the data should be available for debugging **and for
Claude to inspect and use**." HEC already splits observability into two layers; we
mirror that and add a structured agent-activity layer so the agent can read its own
history.

**Layer A — raw logs (mirror HEC exactly).**
- structlog → rotating JSON file `logs/billtobox_agent.log` (see CONVENTIONS.md §7).
- `GET /api/logs/stream` Server-Sent-Events endpoint tails the file (replay current
  session by default, or `?replay_hours=N`), reopening across rotation — copied from
  HEC's `web/api.py::stream_logs` / `_tail_log_sse`.
- `/logs` page renders the live stream; `/debug` page reuses it for an "Agent activity"
  panel plus a per-source health table (HEC parity).

**Layer B — structured agent activity (the new bit).**
- New SQLite table **`agent_events`**, written only by the worker:

  | Column | Type | Notes |
  |---|---|---|
  | `id` | INTEGER PK | |
  | `run_id` | INTEGER FK→runs | which agent run |
  | `invoice_id` | INTEGER FK→invoices, nullable | the invoice this step touched, if any |
  | `ts` | TEXT (ISO-8601 UTC) | when the step happened |
  | `step` | INTEGER | monotonic step index within the run |
  | `event_type` | TEXT | `tool_call` \| `tool_result` \| `decision` \| `llm_message` \| `error` |
  | `tool` | TEXT, nullable | tool name (e.g. `extract_invoice`, `store_pdf_to_drive`) |
  | `level` | TEXT | `info` \| `warning` \| `error` |
  | `summary` | TEXT | one-line human-readable description |
  | `inputs_json` | TEXT (JSON), nullable | tool inputs — **secrets/PDF bytes redacted**, hashes/sizes kept |
  | `outputs_json` | TEXT (JSON), nullable | tool result / decision payload — **redacted** |

- **Redaction is mandatory** before any write or display: API keys, tokens, raw PDF
  bytes (store `content_hash` + byte length instead), and full email bodies are never
  persisted to `agent_events`. Mirrors HEC's `_config_view` `***` scrubbing.
- **Human view:** dashboard "Agent activity" timeline (filter by run / invoice / level),
  and a per-invoice audit trail on the invoice detail page — this is what makes the
  money-adjacent Billtobox send auditable.
- **Claude/agent view:** a registered tool **`get_agent_events(invoice_id=None,
  run_id=None, limit=50)`** returns recent structured events so the agent can inspect
  what it (or a prior run) already did and self-correct — e.g. "did I already upload
  this invoice / already flag it for review?". Read-only; the worker remains the sole
  writer.
- Relationship to existing tables: `runs` = per-run summary (counts/errors, already in
  spec); `invoices` = per-invoice state; **`agent_events` = the fine-grained step log**
  within a run. This is the agent analogue of HEC's `decisions` table.

**Workplan impact:** task 5 adds the `agent_events` table + migration; task 7 wires the
structlog+SSE layer; task 18 adds the `/logs`, `/debug`, "Agent activity" and audit-trail
views; task 21 registers `get_agent_events` as a tool. Recorded here so those tasks build
to this design.

---

## 3. Values still to provide (not blocking decisions; needed at config/send time)

These are account-specific values/secrets. They go into the gitignored `config.yaml`
(never committed) when the relevant task runs. Placeholders ship in `config.example.yaml`.

| Value | Needed by | Status |
|---|---|---|
| `ANTHROPIC_API_KEY` | task 13 (extraction) | **TO PROVIDE** |
| Google OAuth `client_id` / `client_secret` (+ one-time consent) | task 8 (Gmail), 14 (Drive) | **TO PROVIDE** |
| Microsoft `client_id` / `client_secret` / `tenant_id` (+ consent) | task 9 (Outlook) | **TO PROVIDE** |
| Billtobox **upload mailbox address** | task 20 (send) | **TO PROVIDE** |
| Billtobox **accepted sender address(es)** | task 20 (send) | **TO PROVIDE** |
| SMTP `host` / `port` / `username` / `app password` for the sender | task 20 (send) | **TO PROVIDE** |
| Pi deployment path + service user | task 22 (systemd) | **Proposed** (see §4) |
| Git remote URL | task 3 (scaffold) | **Provided:** `https://github.com/Avincki/BillToBoxAgent.git` |

---

## 4. Deployment targets (following HEC's scheme — confirm at deploy time)

HEC on the Pi: app `/opt/homecenter/HomeEnergyCenter`, service user `homecenter`,
public port `:8000`. Following that scheme for BillToBox (proposed, adjust if the Pi
layout differs):

- Service user / home: **`billtobox`** / **`/opt/billtobox`**
- App dir: **`/opt/billtobox/BillToBoxAgent`**, venv `.venv`
- Config: **`/opt/billtobox/BillToBoxAgent/config.yaml`** (`Environment=BTB_CONFIG=…`)
- Ports: dashboard internal **`127.0.0.1:9003`**, Caddy public **`:8003`**
- HEC's Windows dev path (for inspection only):
  `C:\Users\AlexVinckier\Dropbox (Personal)\Python\HomeEnergyCenter\HomeEnergyCenter`

---

## 5. Deferred / out of scope

- **Doccle** — API unconfirmed; stays fully stubbed (`NotImplementedError`,
  `# TODO: confirm Doccle API`) until you supply a developer account + OpenAPI spec.
  Listed in `POLLING_SOURCES` design space but disabled. Tracked separately
  (CLAUDE.md / handover brief §6.3).
- **Banqup/Billtobox REST API** — future fallback only; the email-to-mailbox method is
  the chosen integration for now.
- **Mail webhooks (Gmail Pub/Sub, Graph subscriptions) + `tailscale funnel`** — not in
  initial phases; polling timer only.
