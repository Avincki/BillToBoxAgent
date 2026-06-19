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

Next up: task 3 (scaffold the project structure + `git init`).
