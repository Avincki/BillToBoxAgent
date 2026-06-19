# Billtobox Invoice Agent — Raspberry Pi Build Brief (for Claude Code)

_Handover document. Target: a Python, supervised **agent** running as a service on a
Raspberry Pi, alongside the existing **homeenergycenter** project, reusing its setup
conventions (e.g. Tailscale for remote access)._

_Stored invoices live on **Google Drive**, organised per accounting quarter, accessible
from the user's PC. This supersedes the earlier generic spec (`invoice-agent-spec.md`)._

---

## 0. Instructions for Claude Code (read first)

1. **Inspect the existing `homeenergycenter` project before writing anything.** Ask the user
   for its path on the Pi (and repo, if separate). Match its conventions:
   - dependency manager (venv / poetry / uv / pip-tools — use the same),
   - directory layout and module style,
   - logging setup and log destinations,
   - configuration/secrets pattern (env file, `.env`, config module),
   - **systemd** unit conventions (naming, `User=`, `WorkingDirectory=`, restart policy),
   - **Tailscale** setup (hostname / MagicDNS, how the energy app is reached remotely).
   Reuse these rather than introducing new patterns.
2. **Do not invent external API details.** Doccle endpoints and the Billtobox inbound API
   are unconfirmed (see §6). Where unknown, stub the interface, mark `# TODO: confirm`, and
   ask the user for the spec/credentials instead of guessing.
3. **Keep a human approval gate on the Billtobox upload** (the irreversible, money-adjacent
   step). The agent may do everything else autonomously.
4. **Storage target is Google Drive, not the local disk** (see §6.6 / §9-bis). Folders are
   organised per accounting quarter.
5. Build in the phases in §11; get phase 1 working end-to-end before adding autonomy.

---

## 1. Context

The user already runs **homeenergycenter** — a Python app on a Raspberry Pi, installed as a
systemd service, reached remotely via **Tailscale**. It is a deterministic service, not an
agent.

This project adds a second workload on the same Pi: an **invoice agent** that collects
invoices from Gmail, Outlook and Doccle, extracts key fields with Claude, files the PDFs to
**Google Drive** (organised per accounting quarter), and (later) forwards approved invoices
to Billtobox. It is a learning exercise in building a "true" agent, so it should be genuinely
agentic (tool-calling loop + autonomy + a steering dashboard), not just another pipeline.

The two projects stay **independent** (separate systemd units, separate virtualenvs) but
**share host conventions** (Tailscale access, logging style, config approach).

---

## 2. What "agent" means here

A supervised agent, not a fixed script:
- The model is given a goal ("process new invoices since the last run") and **calls tools**
  (search mail, download PDF, extract, file to Drive, check duplicate, queue upload) in a loop
  until done.
- It applies **policy** (auto-file high-confidence invoices from known vendors; escalate the
  rest) instead of asking the human about every item.
- It **self-corrects** (retries failed downloads/uploads, re-queries low-confidence extractions).
- The human supervises **by exception** through a web dashboard, and approves the Billtobox upload.

Implementation options for the loop (Python):
- **Claude Agent SDK** (agent loop + tool orchestration out of the box), **or**
- a **tool-use loop on the Messages API** using the `anthropic` Python SDK (more manual, fewer deps).
Pick whichever aligns with homeenergycenter's dependency footprint; either is fine.

---

## 3. Architecture (two services on the Pi)

```
            ┌─────────────────────────────┐
   timer →  │  agent-worker (systemd svc) │  fetch → extract → upload-to-Drive → queue upload
            └──────────────┬──────────────┘
                           │ read/write
                  ┌────────▼─────────┐        ┌─────────────────────────────┐
                  │  SQLite (WAL)    │        │  Google Drive               │
                  │  (index/audit)   │  ────► │  Invoices/<FY>/<Qn>/*.pdf   │  ◄── PC (Drive
                  └────────▲─────────┘        └─────────────────────────────┘      for Desktop)
                           │ read/write
            ┌──────────────┴──────────────┐
  you   ←   │  dashboard (systemd svc)    │  view + steer, served over Tailscale
            └─────────────────────────────┘
```

- **agent-worker** — the agent loop; triggered by a **systemd timer** (polling; see §9).
- **dashboard** — a small FastAPI (preferred) or Flask web app for review and steering.
- **Google Drive** is the system of record for the PDFs; **SQLite** holds the index, metadata
  and audit log (it stores Drive file IDs, not the PDFs themselves). An optional local cache
  directory may hold a short-lived copy during processing/backup.

---

## 4. Agent worker

Responsibilities each run:
1. For each enabled source, fetch items new since the last successful run (track a watermark).
2. Pre-filter to likely invoices (cheap heuristics) before calling the model.
3. Resolve the PDF (attachment, or download from a link in the body / Doccle).
4. Call Claude to classify + extract fields.
5. De-duplicate via content hash.
6. Apply policy: auto-approve high-confidence/known-vendor items; mark the rest for review.
7. **Upload approved PDFs to Google Drive** into the correct accounting-quarter folder
   (create the folder if missing); record the Drive file ID in SQLite.
8. Queue approved invoices for Billtobox upload (v2) — but **do not upload without the
   human-approval flag set** (see §0.3 and dashboard).

### Tools the agent can call
Expose these as functions/tools to the model (names indicative):
- `search_mail(source, since)` → list of message refs
- `get_pdf(source, message_ref)` → PDF bytes (+ provenance)
- `extract_invoice(pdf_bytes)` → structured fields (see §7)
- `check_duplicate(content_hash)` → bool
- `ensure_quarter_folder(invoice_date)` → Drive folder id (find-or-create)
- `store_pdf_to_drive(pdf_bytes, fields, folder_id)` → Drive file id + path
- `queue_billtobox_upload(invoice_id)` → enqueues for review (does not send)
- `email_to_billtobox(invoice_id)` → e-mails the PDF to the Billtobox mailbox (only after human approval)
- `flag_for_review(invoice_id, reason)`

---

## 5. Web dashboard (review & steering)

A small local web app, served over Tailscale (see §10), **not** exposed to the public internet.

Displays:
- All invoices with: source, vendor, invoice date, amount, currency, confidence, status,
  **Drive link / quarter folder**.
- An **exceptions queue** (low-confidence or failed items, including failed Drive uploads).
- Run **history / audit log**.

Steering actions:
- Approve / reject an invoice.
- Edit an extracted field and re-save (and, if the quarter changes, move the Drive file).
- Re-run extraction on an item.
- **Trigger a manual run now**.
- **Approve the Billtobox upload** for selected invoices.

Suggested stack: FastAPI + Jinja2 (or a minimal JS front end). Single-user LAN/tailnet tool.

---

## 6. Data sources & APIs

### 6.1 Gmail
- OAuth 2.0; scope `gmail.readonly`.
- `users.messages.list` with a search query, e.g.
  `has:attachment filename:pdf (invoice OR factuur OR rekening OR BTW)`.
- `users.messages.get` → walk `payload.parts` for PDF attachments;
  `users.messages.attachments.get` to download.
- Python: `google-api-python-client`, `google-auth-oauthlib`.
- Docs: https://developers.google.com/gmail/api

### 6.2 Outlook (Microsoft Graph)
- OAuth 2.0 via MSAL; scope `Mail.Read`. Works for outlook.com and Microsoft 365.
- `GET /me/messages` with `$search`/`$filter` (e.g. `hasAttachments eq true` + keywords).
- `GET /me/messages/{id}/attachments` → `fileAttachment.contentBytes` (base64).
- Python: `msal`, `msgraph-sdk` (or plain `requests`).
- Docs: https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview

### 6.3 Doccle — **TO CONFIRM**
- Docs render client-side and could not be read directly. Public listings suggest a partner
  **REST (+ GraphQL)** API with **OAuth 2.0**, a sandbox, and a developer account requirement.
- **Action for the user:** obtain a developer account and provide the real API reference /
  OpenAPI spec so document-list and document-download calls can be wired. Until then, stub.
- Entry point: https://doccle.nl/voor-bedrijven/technical-documentation/?doc=/developer-documentation/api-basics/

### 6.4 Anthropic Claude API (extraction)
- Messages API; send the PDF as a base64 `document` block; instruct **JSON-only** output.
- Python: `anthropic` SDK (and optionally the Claude Agent SDK for the loop).
- Docs: https://docs.claude.com

### 6.5 Billtobox / Banqup (Version 2) — send to the configured mailbox
- **Chosen method: e-mail the approved PDF to the Billtobox upload mailbox.** The user has a
  configured Billtobox mailbox address; Billtobox ingests the attachment and OCR-recognizes it.
  This is the primary, simplest integration — no OAuth client credentials or REST wiring needed.
  (The earlier help article `.../402247-...-online-api-verbinding` is the *outward* bookkeeping
  export, not this inbound path — ignore it for ingestion.)
- **Send transport (pick one):**
  - **SMTP** with a dedicated sender account / app password — simplest, and keeps the mail
    *read* scopes (Gmail/Graph) read-only. Recommended.
  - **Gmail API `users.messages.send`** — reuses Google OAuth but requires adding the
    `gmail.send` scope (broader than read-only).
  - **Graph `sendMail`** — requires `Mail.Send`.
- **Sender authorization (confirm!):** Billtobox typically only accepts documents from
  approved sender address(es) and attributes them to the matching account. Send **from an
  address Billtobox recognizes** for that mailbox, or uploads may be silently rejected.
- **One invoice per e-mail**, attaching the stored PDF; put the filename in the subject for
  traceability. On success mark the invoice `uploaded` with a timestamp so reruns never
  double-send (idempotency).
- **Optional future alternative — Banqup/Billtobox REST API (OAuth2)** via the Developer Portal
  (CLIENT_ID/CLIENT_SECRET); the community `python-billtobox-api` wrapper shows the flow but is
  old/limited. Only worth it if you outgrow the mailbox method.
- **Migration note:** from June 2026 new accounts onboard to **Banqup** (same platform/APIs).
- Portal: https://www.banqup.com · Wrapper: https://pypi.org/project/python-billtobox-api/

### 6.6 Google Drive (PDF storage)
> Assumed to be **Google Drive** (My Drive / Workspace), since the user wants PC access — not
> Google Cloud **Storage** (GCS buckets). Confirm if that assumption is wrong.

- **Write from the Pi via the Google Drive API** (headless upload). Recommended.
  - **Reuse the existing Google OAuth** set up for Gmail — just add the Drive scope. Prefer the
    least-privilege scope **`https://www.googleapis.com/auth/drive.file`** (the app can only see
    and manage files/folders it creates), which is sufficient because the agent creates the
    invoice folders and files itself.
  - Python: `google-api-python-client` (Drive v3). Key calls: `files.create` with
    `mimeType=application/vnd.google-apps.folder` to create quarter folders, and `files.create`
    with media upload to add the PDF under the right `parents=[folder_id]`.
  - **Find-or-create** each quarter folder under a configurable **root folder ID** so re-runs
    don't create duplicates.
- **Alternative:** mount Drive on the Pi with **rclone** (`rclone copy`/`mount` to a Drive
  remote) and write as if local. Workable, but a direct API upload is more robust for a service
  than a FUSE mount — use rclone only if it matches homeenergycenter's existing approach.
- **PC access:** install **Google Drive for Desktop** on the PC; the `Invoices/<FY>/<Qn>/`
  tree then appears in Explorer/Finder and stays in sync. (Or just use drive.google.com.)
- Docs: https://developers.google.com/workspace/drive/api

---

## 7. Extraction logic
- Cheap pre-filter (sender, subject/body keywords, PDF presence) narrows candidates first.
- Claude both confirms invoice-ness and extracts fields. Output schema:
  ```json
  {
    "is_invoice": true,
    "confidence": 0.0,
    "vendor": "string",
    "invoice_date": "YYYY-MM-DD",
    "amount": 0.00,
    "currency": "EUR"
  }
  ```
- Items below a confidence threshold → `flag_for_review`, not auto-approved.

---

## 8. Quarter organisation (accounting period)

PDFs are filed into per-quarter folders that match the user's **accounting period**.

- **Configurable fiscal-year start month** (`FISCAL_YEAR_START_MONTH`, default `1` = calendar
  year → calendar quarters). If the accounting year is offset (e.g. starts in April), quarters
  shift accordingly.
- The **invoice date** drives the quarter (configurable; could alternatively be received date).
- Folder layout on Drive:
  ```
  <root folder>/
    2026/            # or FY2026 if fiscal-year labelling is preferred
      Q1/ Q2/ Q3/ Q4/
  ```
- Mapping examples (calendar default): Jan–Mar → Q1, Apr–Jun → Q2, Jul–Sep → Q3, Oct–Dec → Q4.
  With `FISCAL_YEAR_START_MONTH=4`: Apr–Jun → Q1, … Jan–Mar → Q4 (of the prior FY label).
- Provide a single pure function `period_for(invoice_date) -> (fy_label, quarter)` and unit-test it.

---

## 9. Data model (SQLite, WAL mode)
One row per invoice, for de-dup, status tracking, audit, and Drive linkage.

| Field | Notes |
|-------|-------|
| `id` | primary key |
| `source` | gmail \| outlook \| doccle |
| `source_message_id` | original mail/document id |
| `content_hash` | SHA-256 of PDF bytes (de-dup / idempotency) |
| `vendor`, `invoice_date`, `amount`, `currency` | extracted, user-editable |
| `fy_label`, `quarter` | computed accounting period (e.g. `2026`, `Q2`) |
| `confidence` | from Claude |
| `status` | new \| reviewed \| approved \| stored \| upload_approved \| uploaded \| rejected |
| `drive_file_id` | Google Drive file id of the stored PDF |
| `drive_path` | logical path, e.g. `Invoices/2026/Q2/KPN_2026-05-31_149.95.pdf` |
| `run_id` | which agent run produced/last-touched it |
| `created_at`, `updated_at` | timestamps |

Keep a `runs` table (start, end, counts, errors) for the dashboard history. Use **WAL mode**
with a **single writer** (the worker); the dashboard mostly reads and makes small status writes.

---

## 9-bis. Triggering on the Pi
- **Default: systemd timer + polling.** A `*.timer` runs the worker on a schedule (e.g. hourly
  or each morning); each run asks "anything new since the watermark?" Simple and robust on a
  home network.
- **Manual:** the dashboard "Run now" button starts a one-off run.
- **Webhooks (optional, later):** Gmail (Pub/Sub) and Graph subscriptions need a public HTTPS
  endpoint. On a home Pi that means a tunnel — which **Tailscale Funnel** can provide (see §10)
  without port-forwarding. Future enhancement, not phase 1.

Example units (adapt to homeenergycenter's conventions):
```ini
# /etc/systemd/system/billtobox-agent.service
[Unit]
Description=Billtobox invoice agent (one-shot run)
After=network-online.target
[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/billtobox-agent
ExecStart=/home/pi/billtobox-agent/.venv/bin/python -m agent.run
EnvironmentFile=/home/pi/billtobox-agent/.env
```
```ini
# /etc/systemd/system/billtobox-agent.timer
[Unit]
Description=Run the Billtobox agent on a schedule
[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true
[Install]
WantedBy=timers.target
```
The dashboard runs as a separate long-lived `Type=simple` service.

---

## 10. Networking & remote access (Tailscale)
Match how **homeenergycenter** is exposed. Assuming Tailscale is already on the Pi:
- **Do not bind the dashboard to a public interface.** Bind to localhost; expose it to your
  tailnet with **`tailscale serve`** (private, MagicDNS hostname, HTTPS within the tailnet),
  e.g. `tailscale serve --bg 8000`.
- Reuse the existing tailnet hostname scheme so it sits next to the energy app.
- Only if/when you add mail **webhooks**, consider **`tailscale funnel`** to expose a single
  HTTPS endpoint publicly (avoids router port-forwarding). Keep everything else tailnet-private.

---

## 11. Build phases
1. **Plumbing:** project scaffold matching homeenergycenter; SQLite schema; config/secrets;
   Gmail + Outlook read-only fetch of candidate invoices (no model yet).
2. **Extraction + storage:** add Claude extraction → structured fields; de-dup; compute the
   accounting period; **upload PDFs to the correct Google Drive quarter folder**; populate SQLite.
3. **Dashboard:** read-only views first (list + history + Drive links), then steering actions;
   serve over Tailscale.
4. **Agent loop:** convert the fixed pipeline into a tool-calling loop (Agent SDK or Messages
   API); add policy-based auto-approval and self-correction; wire the systemd timer.
5. **Doccle:** integrate once the API spec/credentials are available.
6. **v2 — Billtobox:** queue + human-approved send by **e-mailing the PDF to the configured
   Billtobox mailbox** (the chosen method; REST API only if needed later).

---

## 12. File naming & path
- Filename template (configurable): `{source}_{yyyy-MM-dd}_{amount}.pdf`
  → e.g. `KPN_2026-05-31_149.95.pdf`
- Full Drive path: `<root>/<fy_label>/<quarter>/<filename>`
  → e.g. `Invoices/2026/Q2/KPN_2026-05-31_149.95.pdf`
- Sanitize vendor (strip illegal chars, collapse whitespace); ISO date; normalized amount
  (no currency symbol in the name; currency kept in metadata). On collision, append `_2`, `_3`, …

---

## 13. Security & secrets
- Reuse homeenergycenter's secrets approach. Keep the Anthropic key and OAuth tokens out of
  source control and out of the web layer.
- **Google OAuth:** reuse the Gmail consent and add the **`drive.file`** scope (least privilege).
- **Headless OAuth:** perform the Google/Graph consent **once interactively**, store the refresh
  token, then the worker runs unattended.
- Dashboard is tailnet-only; read-only mail scopes; idempotent uploads via `content_hash`
  (don't re-upload an invoice already present in Drive).
- **Billtobox send:** prefer SMTP with an app password (keeps mail read scopes read-only); if
  using Gmail/Graph send instead, add only `gmail.send` / `Mail.Send`. Always send from an
  authorized sender address, and mark `uploaded` with a timestamp to prevent double-sends.

---

## 14. Open questions for the user
1. Path to the `homeenergycenter` project (to mirror conventions) and its dependency manager.
2. **Accounting period:** does the fiscal year start in January (calendar quarters) or another
   month? And should folders be labelled `2026` or `FY2026`?
3. Which date drives the quarter — invoice date (default) or received/payment date?
4. Google Drive **root folder** for invoices (provide the folder, or let the agent create it).
   Confirm it's Google Drive (not Cloud Storage). Keep a local backup copy on the Pi, yes/no?
5. Doccle developer account + API reference / OpenAPI spec.
6. Billtobox **upload mailbox address**, and which **sender address(es)** it accepts; preferred
   send transport (SMTP app password vs Gmail/Graph send). Account type (Billtobox vs Banqup).
7. How many mailboxes (one Gmail + one Outlook, or several); polling schedule.

---

## 15. References
- Gmail API — https://developers.google.com/gmail/api
- Google Drive API (v3) — https://developers.google.com/workspace/drive/api
- Google Drive for Desktop — https://support.google.com/a/users/answer/13022292
- Microsoft Graph mail API — https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview
- Anthropic Claude API — https://docs.claude.com
- Doccle technical documentation — https://doccle.nl/voor-bedrijven/technical-documentation/
- Billtobox help center — https://support.billtobox.com/
- Banqup developer portal — https://www.banqup.com
- python-billtobox-api (community) — https://pypi.org/project/python-billtobox-api/
- Tailscale serve/funnel — https://tailscale.com/kb/
