# CLAUDE_BillToBox.md — Complete Workplan: Supervised Invoice-Processing Agent

> A start-to-finish workplan reconstructed from the real development history of the
> BillToBoxAgent project. Follow the phases in order. Each step encodes a real decision,
> a real mistake, or a real pattern that was established during development. Where the
> original project made a misstep and corrected it, this document gives you the correct
> approach **up front** and notes what to avoid.

---

## Project Identity

**What it is:** A supervised Python invoice-processing **agent** that runs as a systemd
service on a Raspberry Pi (alongside an existing `homeenergycenter` project). It:

1. Collects invoices from **Gmail** and **Outlook** (Doccle stubbed for later).
2. Pre-filters junk cheaply, then extracts key fields (vendor, date, amount, currency) with **Claude AI**.
3. Files PDFs to **Google Drive**, organised by accounting quarter (`Invoices/<fy_label>/<quarter>/`).
4. With **human approval** via a dashboard, forwards each invoice to **Billtobox** via SMTP email.

It is a learning exercise in building a genuinely **agentic** system: a tool-calling loop,
policy-based autonomy, self-correction, and a steering dashboard with full observability.

**Target platform:** Raspberry Pi OS Lite (headless, no display). Two independent systemd
services that share host conventions but **not** code or virtualenvs:
- `agent-worker` — the agent loop, triggered by a systemd timer (`Type=oneshot`).
- `dashboard` — a long-lived FastAPI + Jinja2 web app, served over Tailscale via Caddy.

**System of record:** Google Drive holds the PDFs. **SQLite (WAL mode)** is the index,
metadata store, and audit log — it stores Drive file IDs, **not** PDF bytes.

### Technology Stack (specific versions / choices)

| Concern | Choice | Notes |
|---|---|---|
| Language | Python **3.11 / 3.12** | CI matrix runs both; dev venv used 3.12.1 |
| Package layout | **src layout**, package `billtobox_agent` | domain sub-packages |
| Build backend | **hatchling** | `packages=["src/billtobox_agent"]` |
| Dependency mgmt | **venv + pip**, no lockfile | mirrors HomeEnergyCenter (HEC) |
| Config | **YAML + Pydantic v2** (`SecretStr`) | **NOT** `.env` (see Decisions) |
| DB / ORM | **SQLAlchemy 2.0 async** + SQLite **WAL** | two-process safe |
| Migrations | **Alembic** | `0001_initial` |
| Logging | **structlog → stdlib** | rotating JSON file + stderr console |
| Web | **FastAPI + Jinja2** + SSE log stream | binds `127.0.0.1:9003` |
| Mail (Google) | `google-api-python-client`, `google-auth-oauthlib` | `gmail.readonly` + `drive.file` |
| Mail (Microsoft) | `msal` + plain `requests` | `Mail.Read`, device-code flow |
| LLM | `anthropic` SDK | PDF as base64 `document` block, Messages API tool-use |
| Send | **SMTP app password** | keeps mail scopes read-only |
| Tooling | **ruff, black, mypy (strict), pytest, coverage, pre-commit** | CI matrix 3.11/3.12 |
| Networking | **`tailscale cert` + Caddy reverse proxy** | **NOT** `tailscale serve` |
| Property testing | **hypothesis** | for `period_for` |

**Locked naming:** package `billtobox_agent`, env prefix `BTB_`, log file
`billtobox_agent.log`, DB `data/billtobox.db`, service user `billtobox` at
`/opt/billtobox/BillToBoxAgent`, dashboard public `:8003` → internal `127.0.0.1:9003`.

---

## Prerequisites

### Tools to install locally (development machine)

- **Python 3.11 or 3.12** — verify with `python --version`.
  > Pitfall from history (2026-06-19): the machine had 3.12 installed but the default
  > `python` on the Git Bash PATH resolved to an unrelated 3.8 (a PATH-order quirk).
  > Always confirm the interpreter the venv is built with; reference the exact 3.12
  > path if needed rather than trusting `python`.
- **git** + a GitHub account/repo.
- **gh** (GitHub CLI) — optional, but note the **workflow-scope caveat** below.
- A virtual environment per project: `python -m venv .venv`.

### Python packages (declared in `pyproject.toml`)

Runtime: `pydantic>=2`, `pyyaml`, `sqlalchemy[asyncio]>=2`, `alembic`, `aiosqlite`,
`structlog`, `anthropic`, `google-api-python-client`, `google-auth-oauthlib`, `msal`,
`requests`, `fastapi`, `uvicorn`, `jinja2`.

Dev (`[project.optional-dependencies] dev`): `ruff`, `black`, `mypy`, `pytest`,
`pytest-asyncio`, `coverage`, `hypothesis`, `pre-commit`, `types-*` stubs as needed.

### Accounts, API keys & credentials (placeholder until ready — non-blocking)

- **Anthropic API key** (`ANTHROPIC_API_KEY`).
- **Google OAuth client** (client id + secret) with Gmail + Drive APIs enabled.
- **Microsoft / Azure app registration** (client id; **secret optional** — public-client
  device-code flow needs none).
- **Billtobox** mailbox address + the sender address(es) it accepts.
- **SMTP** host/port/username/**app password**.
- **Raspberry Pi** with OS Lite, the `billtobox` service user, and Tailscale joined.

> History note: account-specific secrets were **placeholdered** (`REPLACE_ME`) so
> development never blocked on them. Provide real values only at config/send time.

### Reference URLs

Gmail API, Google Drive v3, Microsoft Graph mail API, Anthropic Claude API,
Tailscale serve/cert docs, Billtobox help center. (Doccle docs deferred until spec +
credentials are supplied.)

---

## Architecture Decisions

These are the binding decisions made during development. **Decisions reversed from the
original spec** are flagged — they are the most important to internalise.

### Decisions reversed from the original plan (study these first)

1. **Config: YAML + Pydantic, NOT `.env`.** (2026-06-19, task 1)
   The original CLAUDE spec assumed `.env`/`python-dotenv`. Inspecting HEC showed it uses
   **YAML config validated by Pydantic v2 with `SecretStr`**. *Reversed → use YAML.* No
   `python-dotenv`, no `.env.example`. Commit `config.example.yaml` with `REPLACE_ME`.

2. **Networking: `tailscale cert` + Caddy reverse proxy, NOT `tailscale serve`.**
   (2026-06-19, task 1) HEC fronts multiple apps with **Caddy** reusing shared
   `tailscale cert` material. *Reversed → join the Caddy scheme.* Dashboard binds
   `127.0.0.1:9003`; Caddy exposes `homecenter.<tailnet>.ts.net:8003`.

3. **Migrations: Alembic + SQLAlchemy 2.0 async, NOT a hand-rolled version table.**
   (2026-06-19) Mirror HEC's data layer exactly.

4. **systemd: inline `Environment=BTB_CONFIG=…`, NOT `EnvironmentFile=`.**
   (2026-06-19) Because config is a YAML file path, not env vars.

### Additive decisions (no spec conflict, but required)

5. **SQLite WAL mode** — HEC does *not* enable WAL, but BillToBox **needs** it because
   the worker and dashboard are two separate processes both touching the DB. Enable via a
   SQLAlchemy `connect` listener: `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;` per
   connection. The worker is the **sole writer**; the dashboard does reads + small status writes.

6. **systemd timer + oneshot worker split** — the worker is `Type=oneshot`, fired by a
   `Persistent=true` timer (default daily 07:00).

7. **Two-layer observability** (owner priority, decision #11, 2026-06-19):
   - **Layer A** — mirror HEC's structlog JSON file + `/api/logs/stream` SSE + `/logs`/`/debug`.
   - **Layer B** — a new structured **`agent_events`** SQLite table recording *every*
     tool call/result/decision (secrets + PDF bytes **redacted**), surfaced as a dashboard
     "Agent activity" timeline + per-invoice audit trail, **and exposed to the agent** as a
     read-only **`get_agent_events`** tool so Claude can inspect its own history and self-correct.
   This raised the tool count to **10**.

### Locked business decisions (decisions.md, 2026-06-19)

- Calendar-year quarters (`fiscal_year_start_month=1`), plain `2026` folder labels.
- Quarter driven by **invoice date** (`quarter_date_field=invoice_date`).
- **No** local PDF backup — Drive is system of record.
- Poll **Gmail + Outlook**; Doccle stubbed and excluded from `sources.polling`.
- Send via **SMTP app password**; **Billtobox** account (not Banqup).
- Timer **daily 07:00**, `Persistent=true`.
- Agent **creates** a new `Invoices` Drive folder on first run.
- Confidence threshold **0.85**.
- Dashboard **public `:8003` → `127.0.0.1:9003`**.

### Patterns to follow / anti-patterns to avoid

- **FOLLOW:** mirror HEC conventions exactly unless strictly unavoidable. Every divergence
  was justified explicitly (WAL, timer split, `agent_events`).
- **FOLLOW:** all mail connectors return the *same shape* (`MailMessageRef` / `FetchedPdf`
  from `mail/base.py`) so the worker treats every source uniformly.
- **FOLLOW:** put the linear-worker steps that aren't owned by an external service in a
  `pipeline/` package — distinct from `agent/` (the LLM tool-loop) and from the domain
  sub-packages (`mail/`, `drive/`, …).
- **ANTI-PATTERN:** never write to `agent_events` without `redact()`. Secrets and PDF
  bytes must be scrubbed at the write boundary (PDF bytes → hash + length).
- **ANTI-PATTERN:** never let the web layer issue raw SQL writes — all writes flow through
  worker tool functions that emit `agent_events`.
- **ANTI-PATTERN:** never sync a `.venv` into Dropbox/cloud storage (see Lessons).

---

## Workplan

> The phase/step order below mirrors the **actual** development sequence from the history
> files, with corrections applied. Build each phase end-to-end before the next.

### Phase 0 — Discovery & Decisions (do this before any code)

#### Step 0.1 — Inspect the reference project and document conventions
- **What:** Read the existing `HomeEnergyCenter` project. Extract: dependency manager,
  directory layout, logging setup, **config/secrets pattern**, systemd field conventions,
  and Tailscale/exposure model. Write a `CONVENTIONS.md` in the repo root.
- **Why:** The original spec assumed conventions HEC does **not** use. You must reconcile
  the spec against reality *before* writing code, or you will build the wrong thing.
- **Files:** `CONVENTIONS.md`.
- **Acceptance:** `CONVENTIONS.md` documents venv+pip/hatchling, src layout, structlog,
  **YAML+Pydantic config**, SQLAlchemy-async+Alembic, ruff/black/mypy, systemd fields, and
  the `tailscale cert`+Caddy model. Every divergence from the spec is flagged.
- **Pitfalls:** The history surfaced **three** divergences (config, systemd, Tailscale).
  Don't assume the spec is correct — *inspect, then confirm with the owner.*

#### Step 0.2 — Answer open questions → `decisions.md`
- **What:** Work the open-questions list with the owner. Lock: fiscal start month, folder
  label format, quarter date field, local backup yes/no, sources, send transport, account
  type, timer schedule, Drive root folder strategy, confidence threshold, dashboard port,
  and the **observability design**.
- **Why:** These decisions reshape multiple downstream tasks. Locking them prevents rework.
- **Files:** `decisions.md`.
- **Acceptance:** Every open question has a confirmed answer or an explicit "to provide
  later (non-blocking)" placeholder. The two-layer observability design is written down.
- **Pitfalls:** Don't proceed on unconfirmed assumptions. History shows two divergences
  were explicitly **confirmed** with the user (YAML, Caddy) before any code changed.

---

### Phase 1 — Scaffolding & Repository Setup

#### Step 1.1 — Scaffold the project + git init
- **What:** Create `pyproject.toml` (hatchling, dist name `billtobox-agent`, runtime +
  `[dev]` deps, `[tool.ruff/black/mypy/pytest/coverage]` mirroring HEC, `requires-python>=3.11`).
  Create `.gitignore` (HEC's + OAuth token files, `*.db`, PDF cache dir, `config.yaml`,
  `logs/`, TLS `*.key`/`*.crt`). Create `.pre-commit-config.yaml`. Create the package
  `src/billtobox_agent/` with **every** domain sub-package (`config`, `data`, `monitoring`,
  `mail`, `extraction`, `drive`, `billtobox`, `agent`, `web`, `utils`) each with a
  documented `__init__.py`. Create `tests/{unit,integration,fixtures}` with a smoke test,
  plus `scripts/` and `docs/` placeholders. `git init -b main`, add remote, commit, push.
- **Why:** A clean, convention-matching skeleton means every later step drops into place.
- **Files:** `pyproject.toml`, `.gitignore`, `.pre-commit-config.yaml`,
  `.github/workflows/ci.yml`, `README.md`, the full `src/` + `tests/` tree, `scripts/`, `docs/`.
- **Acceptance:** `pip install -e ".[dev]"` succeeds; `ruff check . && black --check . &&
  mypy src && pytest` all green on the empty skeleton; `pre-commit run --all-files` passes;
  every package has `__init__.py`.
- **Pitfalls:**
  - **Never sync `.venv` to cloud storage.** History (2026-06-19): the project lived in
    Dropbox; the venv install hit Dropbox sync file-locks (`WinError 32`) and a
    half-upgraded pip. Fix: mark `.venv` with `com.dropbox.ignored`, repair pip via
    `ensurepip`, then reinstall. **A venv must never be synced.**
  - **The CI workflow / `gh` token scope trap.** History: the `gh` OAuth token had
    `gist/read:org/repo` but **not** `workflow`, so pushing `ci.yml` was rejected. The
    owner tried pasting the YAML into GitHub's web editor — it **mangled the indentation
    twice** (`concurrency`/`jobs` nested wrongly, tokens split mid-line), producing three
    0-second failing runs. **Lesson: upload the file, don't paste it** — or run
    `gh auth refresh -s workflow` first and push. Keep `ci.yml` valid by running
    `yaml.safe_load` on it locally before committing.

#### Step 1.2 — Document local Python setup
- **What:** Write `docs/python-setup.md`: venv creation/activation per shell/IDE, the
  Dropbox-ignore caveat, and troubleshooting (PATH-order interpreter quirk, pip repair).
  Link it from the README.
- **Why:** The exact pitfalls above will bite the next developer; document them once.
- **Files:** `docs/python-setup.md`, `README.md` (link).
- **Acceptance:** A fresh clone can be set up by following the doc only.

---

### Phase 2 — Core: Config, Data, and Cross-Cutting Utilities

#### Step 2.1 — Configuration & secrets (YAML + Pydantic v2)
- **What:** `config/models.py` — a frozen, `extra="forbid"` `AppConfig` tree on a
  `_StrictModel` base. Use `SecretStr` for API keys/secrets, `EmailStr` for Billtobox
  addresses. Sections: `anthropic`, `google`, `microsoft`, `drive`, `billtobox`, `smtp`,
  `extraction` (`confidence_threshold=0.85`), `accounting` (`fiscal_year_start_month=1`,
  `quarter_date_field=invoice_date`, `fy_label_prefix=""`), `storage`
  (`sqlite_path=data/billtobox.db`), `sources` (`polling=[gmail,outlook]`), `web`
  (`host=127.0.0.1`, `port=9003`), `logging`. `config/loader.py` — `load_config(path)`,
  `load_config_from_env()`, `resolve_config_path()` reading `$BTB_CONFIG` (default
  `config.yaml`); raise `ConfigError` with formatted messages. `config/__init__.py`
  re-exports. Commit `config.example.yaml` with `REPLACE_ME` placeholders.
- **Why:** Everything downstream reads config. Frozen + extra-forbid catches typos and
  rogue keys at load time. `SecretStr` keeps secrets out of `repr`/logs.
- **Files:** `config/models.py`, `config/loader.py`, `config/__init__.py`,
  `config.example.yaml`, `tests/unit/test_config*.py`.
- **Acceptance:** loading the example file validates; missing required key → clear
  `ConfigError`; `SecretStr` never renders in `repr`/logs; tests cover defaults, secret
  redaction, validation (missing section, extra key, out-of-range, bad email,
  empty/unknown source, frozen) and loader error paths (missing/empty/bad-YAML/non-mapping).
- **Pitfalls:** Do **not** reach for `.env`/`python-dotenv` — that was the reversed
  assumption. Secrets live as `SecretStr` fields **inside** the YAML.

#### Step 2.2 — SQLite schema + WAL + Alembic + redaction/hashing utilities
- **What:** `data/models.py` — SQLAlchemy 2.0 `Mapped`/`mapped_column`, tz-aware UTC
  timestamps (a `_utcnow` helper). Tables: **`invoices`** (UNIQUE `content_hash`),
  **`runs`**, **`source_status`** (per-source watermark + last-success/error health), and
  **`agent_events`** (`run_id`, `invoice_id`, `ts`, `step`, `event_type`, `tool`, `level`,
  `summary`, `inputs_json`, `outputs_json`). `data/database.py` —
  engine/session-factory/`init_schema` + a `connect` listener issuing
  `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;` per connection. `data/repositories.py`
  + `data/unit_of_work.py`. `utils/redact.py` (`redact()` scrubs secrets, replaces PDF
  bytes with hash+length) and `utils/hashing.py` (`sha256_hex()`). Alembic: `alembic.ini`
  (`file_template=%%(rev)s_%%(slug)s`), async `env.py` resolving
  `BTB_DB_URL`/`BTB_SQLITE_PATH`, `0001_initial` covering all four tables.
- **Why:** WAL makes the two-process design safe. `agent_events` + `redact()` are the
  foundation of the observability layer and the agent's self-inspection.
- **Files:** `data/models.py`, `data/database.py`, `data/repositories.py`,
  `data/unit_of_work.py`, `utils/redact.py`, `utils/hashing.py`, `alembic.ini`,
  `alembic/env.py`, `alembic/versions/0001_initial.py`, `tests/integration/test_data*.py`,
  `tests/unit/test_redact.py`.
- **Acceptance:** integration test on a fresh DB asserts every table/column/constraint;
  `content_hash` UNIQUE rejects a duplicate; `journal_mode=wal` is active on a file-backed
  DB; `alembic upgrade head` → `downgrade base` is clean; a UnitOfWork round-trip proves
  agent-event redaction; `redact()` unit-tested on secret + PDF-byte inputs.
- **Pitfalls:** Production runs `alembic upgrade head`; tests use `init_schema` — don't
  conflate them. Keep `alembic.ini` matching HEC (a harmless `path_separator`
  DeprecationWarning is expected — leave it rather than diverging).

#### Step 2.3 — Accounting-quarter logic (`period_for`)
- **What:** Pure, I/O-free `extraction/period.py`:
  `period_for(invoice_date, fy_start_month=1, fy_label_prefix="") -> (fy_label, quarter)`.
  Calendar quarters by default; offset fiscal years via `fy_start_month` (the FY is
  labelled by its **start year**, so Jan–Mar fall in **Q4 of the prior label** when
  `fy_start_month=4`). Validate `fy_start_month ∈ 1..12`.
- **Why:** Drive folder layout depends entirely on this; it must be provably correct.
- **Files:** `extraction/period.py`, `tests/unit/test_period.py`.
- **Acceptance:** thorough tests including a **hypothesis** property (quarter always
  Q1–Q4; label is the start year; the fiscal-start month is always Q1): every calendar
  month, every month of an April fiscal year, boundary months, year-boundary rollover,
  spot checks, prefix variants (`""`→`2026`, `"FY"`→`FY2026`).
- **Pitfalls:** No I/O in this module. Must pass before any caller uses it.

#### Step 2.4 — Logging (structlog) + clock helper
- **What:** `monitoring/logging_config.py` `configure_logging(LoggingConfig)` mirroring
  HEC: structlog→stdlib pipeline, **two root handlers** (rotating JSON file
  `logs/billtobox_agent.log` + stderr `ConsoleRenderer`), shared processors with a
  local-tz (Europe/Brussels) ISO timestamper, **idempotent handler-t