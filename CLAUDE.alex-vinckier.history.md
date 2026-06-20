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

## 2026-06-20 — CI workflow live on GitHub

Got `.github/workflows/ci.yml` onto `origin/main` and green. It had been held back
since task 3 because the `gh` token lacked the `workflow` scope. The owner first
tried the GitHub web editor, but the paste mangled the YAML indentation (twice —
`concurrency`/`jobs` ended up nested under `pull_request`, tokens split mid-line),
so the three resulting runs failed at 0s (unparseable). Fix: rebased the local task-11
commit onto the two web commits, overwrote the broken `ci.yml` with the correct
on-disk content, validated it parses (`yaml.safe_load`), committed. The push was
still rejected for the missing scope, so the owner ran `gh auth refresh -s workflow`
(this time it granted — the earlier failure was an incomplete interactive flow, not a
hard restriction). Pushed; CI ran clean on Python 3.11 **and** 3.12 (ruff, ruff-format,
black, mypy, pytest), ~40s each. Lesson for next time: **upload the file, don't paste**
it into the web editor (or just push with the workflow-scoped token). Only annotation
is GitHub's Node-20-deprecation notice on checkout@v4/setup-python@v5 — cosmetic;
bump the action majors when convenient.

## 2026-06-20 — WORKPLAN task 12: Content-hash deduplication

Added the `pipeline` package (new home for the linear-worker steps that aren't owned
by an external-service domain — distinct from `agent/`, the future LLM tool-loop):
`pipeline/dedup.py` with `compute_content_hash(pdf_bytes)` (SHA-256 hex via the
existing `utils.hashing.sha256_hex`) and `async check_duplicate(uow, content_hash,
*, run_id=None, step=0) -> bool`. The query plumbing already existed from task 5
(`InvoicesRepository.get_by_content_hash`/`exists_content_hash` + the UNIQUE
`content_hash` constraint); task 12 wraps it as the pipeline gate: on a hit it emits
a redacted `DECISION` agent-event (`tool="check_duplicate"`, linked to the invoice
that already holds the hash, outputs `{content_hash, duplicate_of_invoice_id}`) and
returns True so the caller skips before any model call; on a miss it returns False and
writes nothing. Full pipeline wiring lands in task 17 (`run_once`).

3 tests (`tests/integration/test_dedup.py`): hash is content-addressed + stable;
unseen hash → False with no event; seen hash → True, audited once, no re-insert, and
a different hash still → False — covering the acceptance criteria exactly. Toolchain
green: ruff ✓, ruff-format ✓, black ✓, mypy ✓ (33 files), pytest ✓ (103, up from 100).

Next up: task 13 (Claude extraction) — the first model call, gated by `prefilter`
(task 11) and `check_duplicate` (task 12).

## 2026-06-20 — WORKPLAN task 13: Claude invoice extraction

The first model call. Read the `claude-api` skill first (CLAUDE.md mandates it for any
Claude/Anthropic work) for the current model id, the base64 `document` block shape, and
the transient-error classes. Added `extraction/extract.py`:

- `extract_fields(client, pdf_bytes, *, config, confidence_threshold) -> ExtractionResult`
  — the sync core: base64 `document` block (`standard_b64encode`, no newlines) + a
  JSON-only instruction → parse the text block (tolerating a stray ```json fence) →
  validate against a Pydantic `ExtractedInvoice` (is_invoice, confidence 0-1, vendor,
  invoice_date as date, amount, currency ISO-4217) → apply the confidence gate
  (`auto_approve = is_invoice and confidence >= threshold`). Malformed/invalid responses
  raise `ExtractionError`.
- `extract_invoice(...)` — async pipeline tool: runs `extract_fields` in a worker thread
  (anthropic SDK is sync, like the gmail/graph connectors) and writes redacted
  `agent_events` (`tool_call` with the PDF bytes → hash+len, `tool_result` with the
  fields, or `error`). Never logs the PDF bytes or the API key.
- Retry: chose `tenacity` (already a dep) over the SDK's built-in retries — disabled the
  SDK's (`max_retries=0` in `build_anthropic_client`) so our loop is the single, testable
  source of truth and each attempt is auditable. Retries only the transient classes
  (`APIConnectionError`, `RateLimitError`, `InternalServerError`); a 400 propagates at once.

Config: added `model` (default **`claude-opus-4-8`** per the reference — the owner can drop
to `claude-sonnet-4-6`/`claude-haiku-4-5` in config.yaml since cost is their call),
`max_tokens` (1024), and `max_attempts` (3) to `AnthropicConfig`; documented in
config.example.yaml. Used the instruction+Pydantic approach rather than
`output_config.format` to stay robust across the SDK range and to keep validation in our
own (testable) code. Installed SDK is anthropic 0.111.0.

15 tests (`tests/integration/test_extraction.py`) against a fake client: valid parse +
base64 document block sent + model id; low-confidence and non-invoice both → not
auto-approved; six malformed payloads rejected; code-fence tolerance; retry fires once on
a transient error, not retried on a 400, reraises after exhaustion; and the async path
emits redacted events (no PDF bytes, no api key) plus an error event on failure. Toolchain
green: ruff ✓, ruff-format ✓, black ✓, mypy ✓ (34 files), pytest ✓ (118, up from 103).
Phase 2 has begun.

Next up: task 14 (Drive quarter-folder management) — `ensure_quarter_folder` via Drive v3,
reusing the task-8 Google creds + `period_for` (task 6).

## 2026-06-20 — WORKPLAN task 14: Google Drive quarter-folder management

Built the Drive folder layer, mirroring the connector + async-tool split used for mail
and extraction:

- `drive/connector.py` — `DriveConnector`, the sync Drive v3 wrapper (like `GmailConnector`):
  `from_config(google, drive)` reuses the **task-8 Google credentials** + the shared
  `drive.file` scope already in `GOOGLE_SCOPES` and builds the v3 service. **Find-before-create**
  primitives — `find_folder` (a `files.list` filtered on name + parent + `mimeType=folder` +
  `trashed=false`), `create_folder`, `find_or_create_folder` — plus `ensure_root_folder()`
  (creates the root `Invoices` folder on first run, memoised) and `ensure_quarter_path(fy_label,
  quarter)` which walks/creates `<root>/<fy_label>/<quarter>/` and returns the leaf id.
  Single-quote escaping on the Drive `q` string; `DriveError` on a malformed create response.
- `drive/folders.py` — `ensure_quarter_folder(...)`, the async pipeline tool (like
  `extract_invoice`/`check_duplicate`): computes `(fy_label, quarter)` via **`period_for`**
  (task 6), runs the sync Drive calls off-thread (`asyncio.to_thread`), and writes redacted
  `agent_events` (`tool_call` → `tool_result`, or `error` then re-raise on failure).

Root-folder persistence (decisions.md #9, "records its ID"): because the `drive.file` scope
only ever lists the app's *own* folders, find-or-create-by-name re-finds the same root on
every later run, so idempotency holds **without** a separate id store (there's no general
key-value table, and `source_status` is per-mail-source). The optional `drive.root_folder_id`
config field lets the operator pin it to skip the root lookup. Flagged to the owner as a
possible follow-up (an `app_state` table) if a hard-persisted id is wanted later.

9 tests (`tests/integration/test_drive_folders.py`) against a hand-rolled fake Drive service
that resolves the connector's `q` string so find-before-create behaves like real Drive:
full-tree creation with find-before-create at every level, twice-same-quarter creates nothing,
a **fresh connector** (new process, same Drive) reuses rather than recreates, a second quarter
reuses root+year, pinned-root skips the root lookup, quote escaping, and the async tool's
calendar + fiscal-offset date mapping, audit trail, and error path. Hit (and fixed) the
same `DetachedInstanceError` trap as the dedup test — read ORM attributes into plain locals
*inside* the `async with` before the session closes. Toolchain green under the 3.12 venv:
ruff ✓, ruff-format ✓, black ✓, mypy ✓ (36 files), pytest ✓ (127, up from 118).

Next up: task 15 (PDF upload to Drive) — `store_pdf_to_drive` under the folder id this task
returns, with filename sanitisation + collision suffixing, writing `drive_file_id`/`drive_path`
back to the invoice row.

## 2026-06-20 — WORKPLAN task 15: PDF upload to Google Drive

Built the upload step on top of task 14's `DriveConnector`, same connector + async-tool split:

- `drive/connector.py` — added the file methods: `upload_pdf(name, pdf_bytes, parent_id)`
  (a `files.create` media upload via `MediaInMemoryUpload`, `mimetype=application/pdf`),
  `find_file` (the non-folder sibling of `find_folder`, for collision detection), and
  `store_pdf(base_name, pdf_bytes, parent_id) -> (file_id, final_name)` which resolves a name
  collision by appending `_2`/`_3`/... to the stem (before `.pdf`) until `find_file` returns
  nothing, then uploads.
- `drive/upload.py` — `InvoiceFileFields` (source, invoice_date, amount), `build_filename`
  (`{source}_{YYYY-MM-DD}_{amount}.pdf`; amount dot-decimal 2dp, `unknown` when missing; each
  part sanitised — illegal `<>:"/\|?*`+control chars stripped, whitespace collapsed), and the
  async `store_pdf_to_drive(connector, pdf_bytes, fields, *, folder_id, folder_path, uow,
  invoice_id, ...)` tool: builds the name, uploads off-thread (`asyncio.to_thread`), composes
  the logical `drive_path = <folder_path>/<final_name>`, records it on the invoice, and writes
  redacted `agent_events` (`tool_call` → `tool_result`, or `error` then re-raise; the PDF bytes
  are redacted to hash+len, never stored).
- `data/repositories.py` — `InvoicesRepository.mark_stored(invoice_id, *, drive_file_id,
  drive_path)`: sets the two Drive fields and transitions status `new`→**`stored`** (the
  `InvoiceStatus.STORED` slot that was unused until now). Updates go through the repo, not raw
  SQL in the tool, matching the codebase's write-boundary convention.

Design notes: replaced the task's opaque `fields` arg with the typed `InvoiceFileFields`
dataclass; the tool takes both `folder_id` (the upload target from `ensure_quarter_folder`) and
`folder_path` (the human-readable prefix `ensure_quarter_folder` already computes) so the stored
`drive_path` is a real path rather than an opaque id. Collision lookup is per-candidate via
`find_file` (cheap; folders hold few files).

9 tests (`tests/integration/test_drive_upload.py`) against a fake Drive service that captures
the uploaded media bytes through the real `MediaInMemoryUpload`: filename formatting (2dp,
missing-amount `unknown`, illegal-char/whitespace sanitisation); connector collision suffixing
(`inv.pdf`→`inv_2.pdf`→`inv_3.pdf`) and same-name-different-folder non-collision; and the async
tool — normal upload (metadata + media round-trip + `(file_id, drive_path)` return + the invoice
row updated to `stored` + redacted audit with no raw bytes), collision path end-to-end, and the
error path (row left untouched at `new`, error event recorded). Toolchain green under the 3.12
venv: ruff ✓, ruff-format ✓, black ✓, mypy ✓ (37 files), pytest ✓ (136, up from 127).

Next up: task 16 (`flag_for_review` + `queue_billtobox_upload`) — the two SQLite-only status
tools (`reviewed` / `upload_approved`), each emitting an `agent_events` row, no external I/O.
These plus tasks 13–15 are the pieces the task-17 linear pipeline wires together.

## 2026-06-20 — WORKPLAN task 16: flag_for_review + queue_billtobox_upload

The two terminal disposition tools, both SQLite-only. Added `pipeline/status.py`:

- `flag_for_review(uow, invoice_id, reason, *, run_id=None, step=0)` — sets status →
  `reviewed` and records the reason in a redacted `DECISION` `agent_events` row
  (`outputs={status, reason}`).
- `queue_billtobox_upload(uow, invoice_id, *, run_id=None, step=0)` — sets status →
  `upload_approved` (the human-approved-send flag task 20 guards on) and **sends nothing**;
  emits a `DECISION` event (`outputs={status}`).

Both mirror `check_duplicate`'s shape (DECISION event at INFO). Added a generic
`InvoicesRepository.set_status(invoice_id, status)` (raises if the row is missing) as the
write boundary — the tools never touch the ORM directly.

Key decision — **no schema change.** The WORKPLAN phrasing "records the reason (notes/
exceptions field)" implied a column, but the *locked* `invoices` schema (decisions.md /
CLAUDE.alex-vinckier.md) has no notes column by design: per the Layer-B observability model
(decisions.md §2), the per-invoice reason lives in the `agent_events` audit trail, and the
dashboard's exceptions queue keys on `status` while the invoice-detail audit trail shows the
reason. So the reason goes into the event, not a new column — keeping the schema/migration
untouched and consistent with the locked design.

3 tests (`tests/integration/test_status.py`): flag_for_review flips status + records the exact
reason in a single DECISION event linked to the invoice; queue_billtobox_upload flips status +
audits (and sends nothing — no SMTP/Drive client is even in the signature, which is how "no
external I/O" is guaranteed); and the missing-invoice guard raises. Toolchain green under the
3.12 venv: ruff ✓, ruff-format ✓, black ✓, mypy ✓ (38 files), pytest ✓ (139, up from 136).

Phase 2's tool set is now complete (extract 13, ensure_quarter_folder 14, store_pdf_to_drive 15,
flag_for_review/queue 16). Next up: **task 17** — the linear `run_once()` that wires fetch →
prefilter → dedup → extract → confidence-gate → (ensure_quarter_folder + store_pdf_to_drive |
flag_for_review) → advance watermark → close the run, with a `--dry-run` mode and full
`agent_events` per step.
