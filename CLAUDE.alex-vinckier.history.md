# Work history — alex-vinckier

## 2026-06-19 — WORKPLAN task 1: Inspect HomeEnergyCenter & document conventions

Completed WORKPLAN task 1. Inspected the HomeEnergyCenter project
(`...\Python\HomeEnergyCenter\HomeEnergyCenter`, package `energy_orchestrator`)
and wrote `CONVENTIONS.md` in the BillToBoxAgent repo root as the reference for
all subsequent tasks.

Documented: venv+pip / hatchling / no-lockfile dependency management; src layout
with domain sub-packages and `__all__` re-exports; structlog+stdlib logging (rotating
JSON file + stderr console, idempotent); **YAML config** (Pydantic v2 + `SecretStr`,
not `.env`); SQLAlchemy 2.0 async + Alembic migrations; ruff/black/mypy-strict/pytest
tooling; systemd field conventions; and the `tailscale cert` + Caddy multi-app
networking model.

Surfaced three patterns where the BillToBox planning docs (CLAUDE.md/WORKPLAN/handover)
assumed conventions HEC does **not** use, and got two confirmed by the user:
- **Config:** YAML, not `.env` — **confirmed (use YAML)**. Reshapes task 4 (config.example.yaml
  + Pydantic AppConfig; no EnvironmentFile=).
- **Tailscale:** `tailscale cert` + Caddy multi-app reverse proxy, not `tailscale serve` —
  **confirmed (join Caddy scheme)**. Reshapes task 23.
- Unavoidable additions (no decision needed): systemd timer+oneshot worker split (task 22),
  SQLite WAL for the two-process design (task 5), dashboard port (proposed public :8003 →
  127.0.0.1:9003, to confirm in task 2 / decisions.md).

Repo is not yet a git repository — `git init` happens in task 3.

## 2026-06-19 — WORKPLAN task 2: Answer open questions → decisions.md

Worked through the CLAUDE.md open-questions list with the owner and wrote
`decisions.md`. Confirmed: calendar-year quarters (`FISCAL_YEAR_START_MONTH=1`),
plain `2026` folder labels, quarter driven by **invoice date**, **no** local PDF
backup (Drive is system of record), poll **Gmail + Outlook** (Doccle stubbed),
Billtobox send via **SMTP app password**, **Billtobox** (not Banqup) account,
timer **daily 07:00** (`Persistent=true`), agent **creates a new `Invoices` Drive
folder** on first run, confidence threshold 0.85, dashboard **`:8003` → 127.0.0.1:9003**.

Designed the observability/logging system the owner asked for (decision #11):
**two layers** — (A) mirror HEC's structlog JSON file + `/api/logs/stream` SSE +
`/logs`/`/debug`, and (B) a new structured **`agent_events`** SQLite table (every
tool call/result/decision, secrets+PDF bytes redacted) surfaced as a dashboard
"Agent activity" timeline + per-invoice audit trail and exposed to the agent as a
read-only **`get_agent_events`** tool so Claude can inspect its own history and
self-correct. Documented in `decisions.md` §2 and CONVENTIONS.md §7. Shapes tasks
5 (table+migration), 7 (log/SSE), 18 (views), 21 (tool).

Values still to provide at config/send time (placeholdered, non-blocking):
Anthropic key, Google + Microsoft OAuth client creds, Billtobox mailbox + accepted
sender address(es), SMTP creds, Pi path/repo URL. Doccle remains out of scope.

Then revised `WORKPLAN.md` to match the locked decisions: fixed the tasks written
on wrong assumptions (task 4 YAML not `.env`; task 5 Alembic + WAL + the new
`agent_events`/`source_status` tables + `redact()`; task 22 inline `Environment=`
not `EnvironmentFile=`; task 23 Caddy + `tailscale cert` not `tailscale serve`),
wove the two-layer observability scope across tasks 5/7/18/21 (10th tool
`get_agent_events`), marked tasks 1–2 done, and added per-task acceptance criteria +
explicit dependencies/phases. Also expanded task 3 to set up pre-commit + CI and
tasks 8/9 to include the one-time OAuth consent bootstrap scripts.

On request, added **task 24 — an HEC-style `docs/raspberry-pi-setup.md` deployment
guide**, sequenced right after networking (its minimum prerequisites are the runnable
worker/dashboard + systemd units + Caddy exposure, i.e. tasks 18/22/23); renumbered the
on-Pi smoke-run → 25 (now follows the guide and feeds fixes back) and the ops README →
26 (slimmed to defer install steps to the guide). Total ≈20d.

## 2026-06-19 — WORKPLAN task 3: Scaffold project structure + git init

Scaffolded per CONVENTIONS.md: `pyproject.toml` (hatchling, `billtobox-agent`,
runtime + `[dev]` deps, ruff/black/mypy/pytest/coverage config mirroring HEC),
`.gitignore` (+ OAuth token files, PDF cache, TLS material), `.pre-commit-config.yaml`,
`.github/workflows/ci.yml` (matrix 3.11/3.12), minimal `README.md`, the
`src/billtobox_agent/` package with domain sub-packages (config, data, monitoring,
mail, extraction, drive, billtobox, agent, web, utils — each a documented
`__init__.py`), `tests/{unit,integration,fixtures}` with a smoke test, and
`scripts/`+`docs/` placeholders.

`git init -b main`; added remote `origin` → https://github.com/Avincki/BillToBoxAgent.git;
committed and **pushed 30 files** to `origin/main`. The CI workflow
(`.github/workflows/ci.yml`) was held back: the owner's `gh` OAuth token has
`gist/read:org/repo` but not `workflow`, and `gh auth refresh -s workflow` did not grant
it (interactive approval did not complete / OAuth-app scope restricted). `ci.yml` stays on
disk (untracked) — add it via GitHub's web "Add file" editor (web commits bypass the
token-scope rule) or commit it once a `workflow`-scoped token is available.

Verification: created `.venv` with **Python 3.12.1** (the machine already had 3.12 at
`...\Programs\Python\Python312`; the default `python` on the Git Bash PATH is an unrelated
3.8 — a PATH-order quirk, not a missing interpreter). Because the project lives in Dropbox,
the venv install first hit Dropbox sync file-locks (WinError 32) and a half-upgraded pip —
fixed by marking `.venv` with the `com.dropbox.ignored` NTFS stream (a venv must never be
synced), then repairing pip via `ensurepip` and `pip install -e ".[dev]"`. The **full
toolchain now passes**: ruff ✓, ruff-format ✓, black ✓, mypy ✓ (11 files), pytest ✓ (2
passed). Task 3 acceptance met. (mypy notes the google/msal override sections are unused
for now — they activate once tasks 8/9/14 import those libraries.)

Also wrote `docs/python-setup.md` (local venv setup, activation per shell/IDE, the
Dropbox-ignore caveat, and troubleshooting) and linked it from the README.

## 2026-06-19 — WORKPLAN task 4: Configuration + secrets (YAML + Pydantic)

Implemented the config layer (CONVENTIONS.md §6): `config/models.py` (frozen,
extra-forbid `AppConfig` tree; `SecretStr` for API keys/secrets; `EmailStr` for the
Billtobox addresses; defaults per decisions.md — confidence 0.85, calendar quarters,
`127.0.0.1:9003`, polling `[gmail, outlook]`); `config/loader.py`
(`load_config` / `load_config_from_env` / `resolve_config_path`, `ConfigError` with
formatted messages, `$BTB_CONFIG` path); `config/__init__.py` re-exports; and
`config.example.yaml` (REPLACE_ME placeholders, no `.env`). 18 tests: defaults, secret
redaction, validation (missing section, extra key, out-of-range, bad email,
empty/unknown source, frozen) and loader error paths (missing/empty/bad-YAML/non-mapping)
plus example-file validity.

Toolchain green under the 3.12 venv: ruff ✓, black ✓, mypy ✓ (13 files), pytest ✓ (20).

## 2026-06-19 — WORKPLAN task 5: SQLite schema + WAL + Alembic

Built the data layer (CONVENTIONS.md §8): `data/models.py` (SQLAlchemy 2.0 `Mapped`/
`mapped_column`, tz-aware UTC timestamps) with the `invoices` (unique `content_hash`),
`runs`, `source_status` (watermark + health), and `agent_events` tables; `data/database.py`
(engine / session factory / `init_schema`, plus a `connect` listener enabling **WAL** +
`foreign_keys=ON` per connection — the two-process need from decisions.md §13-D);
`data/repositories.py` + `data/unit_of_work.py`; and `utils/redact.py` + `utils/hashing.py`
(`redact()` scrubs secrets and replaces PDF bytes with hash+length at the `agent_events`
write boundary; `sha256_hex()` for content hashing). Alembic set up (`alembic.ini`, async
`env.py` resolving `BTB_DB_URL`/`BTB_SQLITE_PATH`, `0001_initial` for all four tables).

10 new tests: schema tables/columns, WAL + FK pragmas active (file-backed DB), `content_hash`
UNIQUE rejects a duplicate, a UnitOfWork round-trip proving agent-event redaction, redact unit
tests, and an Alembic upgrade→downgrade integration test. Toolchain green: ruff ✓, black ✓,
mypy ✓ (19 files), pytest ✓ (30). One harmless alembic `path_separator` DeprecationWarning
(kept alembic.ini matching HEC).

## 2026-06-19 — WORKPLAN task 6: Accounting-quarter logic (`period_for`)

Implemented the pure `period_for(invoice_date, fy_start_month=1, fy_label_prefix="")
-> (fy_label, quarter)` in `extraction/period.py` (no I/O). Calendar quarters by default;
offset fiscal years via `fy_start_month` (the FY is labelled by its start year, so Jan-Mar
fall in Q4 of the prior label). Validates `fy_start_month` is 1-12. 33 tests including a
hypothesis property (quarter always Q1-Q4, label is the start year, the fiscal-start month
is always Q1): every calendar month, every month of an April fiscal year, boundary months,
year-boundary rollover, July-FY spot checks, and prefix variants. Toolchain green
(ruff/black/mypy 20 files/pytest 63).

## 2026-06-19 — WORKPLAN task 7: Logging (structlog) + clock helper

Added `monitoring/logging_config.py` `configure_logging(LoggingConfig)` mirroring HEC:
structlog→stdlib pipeline, two root handlers (rotating JSON file `logs/billtobox_agent.log`
+ stderr ConsoleRenderer), shared processors with a local-tz (Europe/Brussels) ISO
timestamper, idempotent handler-tagging, and WARNING-quieting for chatty HTTP libs. Added
`utils/clock.py` (`LOCAL_TZ`, `now_local`, `to_local`). The JSON file is what task 18's
`/logs` SSE stream will tail. 7 tests mirror HEC's (file/dir creation, ISO+level round-trip,
contextvars, stdlib routing, idempotency, foreign-handler preservation, level threshold).
Toolchain green (ruff/black/mypy 22 files/pytest 70).

The `configure_logging` call gets wired into the entry points when those are built — the
worker (tasks 17/21) and the dashboard `main.py` (task 18).

## 2026-06-19 — WORKPLAN task 8: Gmail read-only fetch + OAuth bootstrap

Built the Gmail connector and the shared mail layer: `mail/base.py` (`MailMessageRef`,
`FetchedPdf`, `MailConnector` Protocol — the shape every source returns); `mail/google_auth.py`
(least-privilege scopes gmail.readonly + drive.file, `load_credentials` with auto-refresh,
`run_consent_flow`, `save_credentials`, `GoogleAuthError`); `mail/gmail.py` (`GmailConnector`:
query `has:attachment filename:pdf (invoice OR factuur OR rekening OR BTW)`, pagination,
metadata refs, recursive `payload.parts` walk + base64url attachment download; injectable
service + `from_config` factory); `mail/fetch.py` (`fetch_new_pdfs`: reads the source_status
watermark, adds Gmail `after:` epoch, dedups by source_message_id via new
`InvoicesRepository.exists_source_message_id`, downloads survivors in a thread, advances the
watermark). One-time consent script `scripts/auth_google.py`. Extended the mypy override for
google-auth (`follow_imports=skip`, since google-auth ships partial types).

5 integration tests against a hand-rolled fake Gmail service: query built (with/without
`after:`), refs parsed + sorted, attachment bytes decoded, watermark advances, and dedup by
source_message_id (an already-invoiced message is skipped). Toolchain green (ruff/black/mypy
26 files/pytest 75).

## 2026-06-19 — WORKPLAN task 9: Outlook / Microsoft Graph fetch (read-only)

Built the Outlook connector matching the Gmail shape: `mail/ms_auth.py` (MSAL public-client
device-code flow, Mail.Read, `SerializableTokenCache` at microsoft.token_path, silent
acquire/refresh, `MicrosoftAuthError`); `mail/graph.py` (`GraphClient` over requests + Bearer
token, `GraphHttp` Protocol for injection); `mail/outlook.py` (`OutlookConnector`: `$filter`
hasAttachments + receivedDateTime, pagination via `@odata.nextLink`, fileAttachment PDF
download with base64 `contentBytes`; injectable client + `from_config`). Reuses the shared
`fetch_new_pdfs` (watermark + source_message_id dedup). One-time consent script
`scripts/auth_ms.py`. Made `microsoft.client_secret` optional (public-client device-code
needs none).

5 integration tests vs a fake Graph client: `$filter` built (with/without receivedDateTime),
refs parsed, non-PDF attachment skipped + bytes decoded, watermark advances, dedup by
source_message_id. Output type-matches Gmail (`FetchedPdf`). Toolchain green (ruff/black/mypy
29 files/pytest 80).

## 2026-06-19 — WORKPLAN task 10: Doccle stub

Added `mail/doccle.py` — `DoccleConnector` matching the `MailConnector` shape (source=
"doccle", `search`/`download_pdfs`/`from_config`), every method raising `NotImplementedError`
with a `# TODO: confirm Doccle API` marker pending the API spec + credentials. Default
`sources.polling` excludes doccle so the worker never builds it. 4 unit tests (module
importable at the expected path; search/download/from_config raise). Toolchain green
(ruff/black/mypy 30 files/pytest 84). Phase 1 plumbing connectors complete.

Next up: add the CI workflow; then task 11 (pre-filter heuristics — sender/subject/PDF magic
bytes).

## 2026-06-20 — WORKPLAN task 11: Pre-filter heuristics

Added `mail/prefilter.py` — `prefilter(ref, pdf_bytes, config) -> bool`, the cheap
model-free gate that runs before any Claude call so junk never reaches the paid
extraction. Three signals: (1) PDF magic bytes (`%PDF-` prefix; empty/non-PDF →
reject), (2) sender domain allow/blocklist, (3) subject keywords. Semantics chosen
for an invoice agent whose vendors can't be enumerated in advance: **blocklist wins**
(hard reject), **allowlist is trust/bypass** (accept regardless of subject), and any
other sender must carry a keyword (`invoice`/`factuur`/`rekening`/`btw`) in the
subject. Domain match is dot-anchored suffix (`kpn.com` matches `mail.kpn.com` but
not `evil-kpn.com`); sender parsed via `email.utils.parseaddr` so display-name forms
(`KPN <billing@kpn.com>`) work. All three lists are config-driven via a new
`PrefilterConfig` (added to `AppConfig`, documented in `config.example.yaml`).

16 unit tests (`tests/unit/test_prefilter.py`): PDF/non-PDF/empty bytes, keyword
case-insensitivity + substring + config override, allowlist bypass + subdomain +
display-name + the evil-kpn anti-match, blocklist hard-reject + blocklist-beats-
allowlist + subdomain, and unparseable-sender fall-through. Toolchain green:
ruff ✓, ruff-format ✓, black ✓, mypy ✓ (31 files), pytest ✓ (100, up from 84).

Next up: add the CI workflow; then task 12 (content-hash dedup) — `prefilter` and the
hash check are the two cheap gates before extraction in the task-17 pipeline.
