# CLAUDE.md — Billtobox Invoice Agent

## Project Overview

A supervised Python invoice-processing agent running as a systemd service on a Raspberry Pi, alongside the existing **homeenergycenter** project. The agent collects invoices from Gmail, Outlook, and Doccle; extracts key fields with Claude AI; files PDFs to Google Drive organised by accounting quarter; and (with human approval) forwards invoices to Billtobox via email. This is a learning exercise in building a genuinely agentic system with tool-calling loops, policy-based autonomy, and a steering dashboard.

---

## Step 0 — Inspect HomeEnergyCenter First

Before writing any code, read the existing homeenergycenter project at:

```
C:\Users\AlexVinckier\Dropbox (Personal)\Python\HomeEnergyCenter\HomeEnergyCenter
```

Extract and match the following conventions exactly:

- **Dependency manager** — detect whether the project uses venv+pip, pip-tools, poetry, or uv; use the same tool for this project
- **Directory layout** — match module naming style, src layout vs flat layout, test placement
- **Logging** — match the logging setup: handler types, formatters, log destinations, log level configuration
- **Configuration and secrets** — match the pattern for env files, `.env` loading, config modules, and how secrets are kept out of source control
- **systemd units** — match the `User=`, `WorkingDirectory=`, `EnvironmentFile=`, restart policy, and naming conventions
- **Tailscale** — match how the energy app is served: which `tailscale serve` flags, the MagicDNS hostname scheme, and port conventions

Do not introduce any pattern not already present in homeenergycenter unless it is strictly unavoidable.

---

## Architecture

Two independent systemd services on the Pi, sharing host conventions but not code or virtualenvs:

- **agent-worker** — the agent loop, triggered by a systemd timer; fetches, extracts, and files invoices
- **dashboard** — a FastAPI + Jinja2 web app for review and steering, served over Tailscale

**SQLite (WAL mode)** is the index, metadata store, and audit log. It holds Drive file IDs, not PDFs. **Google Drive** is the system of record for PDFs, organised under `Invoices/<fy_label>/<quarter>/`. A short-lived local cache may hold PDFs during active processing only.

---

## Build Phases

Implement strictly in order. Get each phase working end-to-end before starting the next.

### Phase 1 — Plumbing
Project scaffold matching homeenergycenter conventions. SQLite schema creation and migration. Config and secrets loading. Gmail read-only fetch of candidate invoices. Outlook read-only fetch of candidate invoices. No model calls yet. Verify watermark tracking and deduplication by source message ID.

### Phase 2 — Extraction and Storage
Add Claude extraction returning structured fields. Compute content hash for deduplication. Compute accounting period from invoice date. Upload PDFs to the correct Google Drive quarter folder (find-or-create). Record Drive file ID and path in SQLite. Apply confidence threshold to gate auto-approval.

### Phase 3 — Dashboard
Read-only views first: invoice list, run history, Drive links, exceptions queue. Then add steering actions: approve, reject, edit fields, re-run extraction, trigger manual run, approve Billtobox upload.

### Phase 4 — Agent Loop
Convert the fixed pipeline into a tool-calling loop using either the Claude Agent SDK or the Messages API tool-use pattern. Add policy-based auto-approval for high-confidence known-vendor invoices. Add self-correction for failed downloads and uploads. Wire the systemd timer.

### Phase 5 — Doccle
Integrate only after the user provides the API reference and developer credentials. Stub the interface until then.

### Phase 6 — Billtobox Send
Queue approved invoices. On human approval via the dashboard, email the PDF to the configured Billtobox upload mailbox. Mark `uploaded` with timestamp immediately after a successful send. Never send without the human-approval flag set.

---

## Agent Worker — Responsibilities Per Run

1. For each enabled source, fetch items new since the last watermark
2. Pre-filter candidates using cheap heuristics (sender domain, subject keywords, PDF attachment presence) before any model call
3. Resolve the PDF from the message attachment or a link in the body
4. Compute SHA-256 content hash; skip if already seen
5. Call Claude to classify and extract fields
6. Apply policy: auto-approve high-confidence known-vendor items; flag the rest for review
7. Upload approved PDFs to the correct Google Drive quarter folder; record Drive file ID in SQLite
8. Queue approved invoices for Billtobox upload; do not send without the human-approval flag

---

## Tools Exposed to the Agent

Implement these as callable functions registered as tools in the agent loop. Names are indicative; match homeenergycenter's naming style.

| Tool | Input | Output |
|------|-------|--------|
| `search_mail` | source, since | list of message refs |
| `get_pdf` | source, message_ref | PDF bytes + provenance |
| `extract_invoice` | pdf_bytes | structured fields dict |
| `check_duplicate` | content_hash | bool |
| `ensure_quarter_folder` | invoice_date | Drive folder ID |
| `store_pdf_to_drive` | pdf_bytes, fields, folder_id | Drive file ID + path |
| `queue_billtobox_upload` | invoice_id | enqueues for review only, does not send |
| `email_to_billtobox` | invoice_id | sends email to Billtobox mailbox (only after human approval flag is set) |
| `flag_for_review` | invoice_id, reason | marks item in SQLite |

---

## SQLite Schema

Use WAL mode with a single writer (the worker). The dashboard performs mostly reads and small status writes.

### `invoices` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `source` | TEXT | gmail, outlook, or doccle |
| `source_message_id` | TEXT | original message or document ID |
| `content_hash` | TEXT | SHA-256 of PDF bytes; unique constraint for dedup |
| `vendor` | TEXT | extracted; user-editable |
| `invoice_date` | TEXT | ISO 8601; extracted; user-editable |
| `amount` | REAL | extracted; user-editable |
| `currency` | TEXT | extracted; user-editable |
| `fy_label` | TEXT | computed, e.g. 2026 |
| `quarter` | TEXT | computed, e.g. Q2 |
| `confidence` | REAL | from Claude (0.0–1.0) |
| `status` | TEXT | new, reviewed, approved, stored, upload_approved, uploaded, rejected |
| `drive_file_id` | TEXT | Google Drive file ID of stored PDF |
| `drive_path` | TEXT | logical path, e.g. Invoices/2026/Q2/KPN_2026-05-31_149.95.pdf |
| `run_id` | INTEGER | FK to runs table |
| `created_at` | TEXT | ISO 8601 timestamp |
| `updated_at` | TEXT | ISO 8601 timestamp |
| `uploaded_at` | TEXT | ISO 8601, nullable; set on the Billtobox send (task 20, migration 0002) |

### `runs` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `started_at` | TEXT | ISO 8601 |
| `ended_at` | TEXT | ISO 8601, nullable |
| `items_fetched` | INTEGER | |
| `items_extracted` | INTEGER | |
| `items_stored` | INTEGER | |
| `items_flagged` | INTEGER | |
| `error_summary` | TEXT | nullable |

---

## Extraction Schema

Claude must return JSON only. Validate against this schema before writing to SQLite.

```
is_invoice: boolean
confidence: float 0.0–1.0
vendor: string
invoice_date: string YYYY-MM-DD
amount: float
currency: string ISO 4217
```

Items where `confidence` is below the configured threshold are not auto-approved; they go to `flag_for_review`.

---

## Accounting Quarter Logic

Provide a single pure function `period_for(invoice_date) -> (fy_label: str, quarter: str)` and unit-test it thoroughly.

Configuration:
- `FISCAL_YEAR_START_MONTH` — integer 1–12, default 1 (calendar year)
- `QUARTER_DATE_FIELD` — invoice_date or received_date, default invoice_date
- `FY_LABEL_PREFIX` — empty string (yields `2026`) or a prefix like `FY` (yields `FY2026`), default empty

With calendar year default: Jan–Mar → Q1, Apr–Jun → Q2, Jul–Sep → Q3, Oct–Dec → Q4. With `FISCAL_YEAR_START_MONTH=4`: Apr–Jun → Q1, Jul–Sep → Q2, Oct–Dec → Q3, Jan–Mar → Q4 of the prior FY label.

Drive folder layout:
```
<root folder id>/
  <fy_label>/
    Q1/
    Q2/
    Q3/
    Q4/
```

The `ensure_quarter_folder` tool must find-or-create each level so reruns never create duplicate folders. Use Drive API `files.list` with `name` and `parents` filters before calling `files.create`.

---

## File Naming

Template (configurable): `{source}_{invoice_date}_{amount}.pdf`

Example: `KPN_2026-05-31_149.95.pdf`

Rules:
- Sanitize vendor name: strip characters illegal in Drive filenames, collapse whitespace, trim
- Date in ISO format YYYY-MM-DD
- Amount as decimal with dot separator, no currency symbol, two decimal places
- Source is the lowercase source identifier
- On filename collision within a folder, append `_2`, `_3`, and so on before the extension

---

## Data Sources and APIs

### Gmail
- OAuth 2.0; scope `gmail.readonly` (plus `gmail.send` only if SMTP is not used for Billtobox sending)
- Search query: `has:attachment filename:pdf (invoice OR factuur OR rekening OR BTW)`
- Walk `payload.parts` for PDF attachments; use `users.messages.attachments.get` to download
- Python packages: `google-api-python-client`, `google-auth-oauthlib`

### Outlook (Microsoft Graph)
- OAuth 2.0 via MSAL; scope `Mail.Read`
- Filter: `hasAttachments eq true` combined with keyword search
- Retrieve `fileAttachment.contentBytes` (base64) from the attachments endpoint
- Python packages: `msal`, and either `msgraph-sdk` or plain `requests`

### Doccle
- API is unconfirmed. Stub the interface entirely, mark all Doccle-related stubs with `# TODO: confirm Doccle API`, and do not implement until the user provides the OpenAPI spec and developer credentials
- Entry point for docs: `https://doccle.nl/voor-bedrijven/technical-documentation/`

### Claude API
- Use the `anthropic` Python SDK
- Send the PDF as a base64 `document` block in the Messages API
- Instruct JSON-only output
- For the agent loop, use either the Claude Agent SDK or the Messages API tool-use pattern — match whichever has lower dependency overhead relative to homeenergycenter

### Google Drive
- Drive API v3 via `google-api-python-client`
- Reuse the Google OAuth credentials established for Gmail; add scope `https://www.googleapis.com/auth/drive.file` (least privilege — the app can only see and manage files it creates)
- Key operations: `files.create` with folder mimeType to create quarter folders; `files.create` with media upload to store PDFs under `parents=[folder_id]`
- Find-or-create logic is mandatory to prevent duplicate folders across reruns
- Do not use rclone or FUSE mount unless homeenergycenter already does so

### Billtobox (Email Upload Method)
- Send approved PDFs as email attachments to the configured Billtobox upload mailbox
- One invoice per email; invoice filename in the subject line for traceability
- Preferred transport: SMTP with an app password (keeps mail read scopes read-only)
- Alternative transports: Gmail API `users.messages.send` (requires `gmail.send` scope) or Graph `sendMail` (requires `Mail.Send`)
- Send only from an address that Billtobox has authorized for that mailbox account
- Mark `status = uploaded` and record timestamp immediately after confirmed send
- Never send the same invoice twice — check `status` and `uploaded_at` before sending
- The Banqup REST API is a future fallback only; do not implement in initial phases

---

## Configuration and Secrets

Match homeenergycenter's pattern exactly. Likely an `.env` file loaded at startup, never committed to source control. Expected configuration keys:

- `ANTHROPIC_API_KEY`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_TOKEN_PATH`
- `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID`, `MS_TOKEN_PATH`
- `DRIVE_ROOT_FOLDER_ID`
- `BILLTOBOX_MAILBOX_ADDRESS`
- `BILLTOBOX_SENDER_ADDRESS`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
- `CONFIDENCE_THRESHOLD` (float, default 0.85)
- `FISCAL_YEAR_START_MONTH` (int, default 1)
- `QUARTER_DATE_FIELD` (invoice_date or received_date)
- `FY_LABEL_PREFIX` (default empty)
- `DB_PATH`
- `POLLING_SOURCES` (comma-separated: gmail,outlook,doccle)
- `DASHBOARD_PORT`

OAuth tokens (Google and Microsoft) must be obtained once interactively, stored as refresh tokens on disk, and renewed automatically by the worker without any interactive prompt.

---

## systemd Units

Match homeenergycenter's unit conventions (User, WorkingDirectory, EnvironmentFile, restart policy, naming scheme). The project requires three units:

- **billtobox-agent.service** — Type=oneshot, runs one complete agent cycle, exits when done
- **billtobox-agent.timer** — triggers the service on a schedule (default 07:00 daily); set `Persistent=true` so a missed run fires on next boot
- **billtobox-dashboard.service** — Type=simple, long-lived, runs the FastAPI dashboard

The timer schedule and the dashboard port must be configurable, not hardcoded in the unit files.

---

## Networking and Tailscale

Match homeenergycenter's Tailscale setup exactly. The dashboard must not bind to a public interface. Bind to localhost and expose via `tailscale serve` on the tailnet, using HTTPS within the tailnet. Use the same MagicDNS hostname scheme as the energy app so both services sit logically adjacent.

Do not use `tailscale funnel` in initial phases. Funnel is only relevant if webhook support is added later.

---

## Dashboard — FastAPI + Jinja2

### Views (read-only first)
- Invoice list with columns: source, vendor, invoice date, amount, currency, confidence, status, Drive link, quarter folder
- Exceptions queue: low-confidence items, failed Drive uploads, items needing review
- Run history: start time, end time, counts, errors

### Steering Actions (add after read-only views work)
- Approve or reject an invoice
- Edit extracted fields and re-save (if quarter changes, trigger Drive file move via the Drive API)
- Re-run Claude extraction on a specific invoice
- Trigger a manual agent run immediately
- Approve the Billtobox email send for selected invoices (sets the human-approval flag; the worker then sends on next run or immediately if triggered)

Dashboard is single-user, tailnet-only. No authentication beyond Tailscale network membership is required.

---

## Security Constraints

- All credentials and OAuth tokens stay out of source control and out of the web layer
- The `drive.file` scope is the maximum Google Drive scope; do not request broader scopes
- Mail read scopes remain read-only; add send scopes only if SMTP is not used
- Idempotency is enforced via `content_hash` for uploads and `uploaded_at` timestamp for Billtobox sends
- The human-approval flag for Billtobox send must be set explicitly via the dashboard; the worker must never send without it regardless of automation state

---

## Key Invariants

- A PDF already stored in Drive (matching `content_hash`) is never uploaded again
- A Billtobox email is never sent twice for the same invoice
- The agent-worker is the sole SQLite writer; the dashboard only writes status fields
- The dashboard approval action only sets the flag; the actual email send happens in the worker
- The Doccle integration remains fully stubbed until API credentials and spec are provided

---

## Open Questions — Collect Answers Before Proceeding

The following must be confirmed by the user before the corresponding code is written:

1. Path to homeenergycenter on the Pi (not the Windows Dropbox path) and any separate repo URL, so conventions can be matched in the deployment environment
2. Fiscal year start month (January = calendar quarters, or a different month)
3. Folder label format: `2026` or `FY2026`
4. Which date drives the quarter: invoice date or received date
5. Google Drive root folder (provide an existing folder ID or confirm the agent should create it)
6. Confirm storage is Google Drive (not Google Cloud Storage)
7. Whether a local backup copy of PDFs should be kept on the Pi alongside the Drive upload
8. Billtobox upload mailbox address and which sender address(es) it accepts
9. Preferred send transport for Billtobox: SMTP app password (recommended), Gmail API send, or Graph sendMail
10. Whether the account is Billtobox or Banqup (migration happened June 2026 for new accounts)
11. Number of mailboxes and their types (one Gmail, one Outlook, or multiple)
12. Desired polling schedule for the timer
13. Doccle developer account and API reference when available

---

## References

- Gmail API — https://developers.google.com/gmail/api
- Google Drive API v3 — https://developers.google.com/workspace/drive/api
- Google Drive for Desktop — https://support.google.com/a/users/answer/13022292
- Microsoft Graph mail API — https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview
- Anthropic Claude API — https://docs.claude.com
- Doccle technical documentation — https://doccle.nl/voor-bedrijven/technical-documentation/
- Billtobox help center — https://support.billtobox.com/
- Banqup developer portal — https://www.banqup.com
- python-billtobox-api community wrapper — https://pypi.org/project/python-billtobox-api/
- Tailscale serve and funnel — https://tailscale.com/kb/