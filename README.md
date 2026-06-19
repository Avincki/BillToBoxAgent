# BillToBoxAgent

A supervised Python invoice-processing **agent** that runs as a systemd service on a Raspberry Pi
(alongside the existing HomeEnergyCenter project). It collects invoices from **Gmail** and **Outlook**,
extracts key fields with **Claude**, files the PDFs to **Google Drive** organised by accounting
quarter, and — with human approval — forwards them to **Billtobox** by email. A FastAPI dashboard
(served over Tailscale) provides review, steering, and a live view of the agent's actions.

> **Status:** scaffolding (Phase 1). No application code yet — see [`WORKPLAN.md`](WORKPLAN.md).

## Project documents

- [`CONVENTIONS.md`](CONVENTIONS.md) — coding/tooling/deployment conventions (mirrors HomeEnergyCenter).
- [`decisions.md`](decisions.md) — confirmed project decisions and configuration choices.
- [`WORKPLAN.md`](WORKPLAN.md) — the build plan (25+ tasks across 6 phases).
- [`docs/python-setup.md`](docs/python-setup.md) — local Python / venv setup on Windows (activation, deps, troubleshooting).
- `docs/raspberry-pi-setup.md` — start-to-finish Pi deployment guide *(task 24)*.

## Quick start (development)

```bash
py -3.12 -m venv .venv            # Windows; macOS/Linux: python3.12 -m venv .venv
# Windows (Dropbox!): mark the venv unsynced before installing —
#   PowerShell:  Set-Content -Path .venv -Stream com.dropbox.ignored -Value 1
# Activate — PowerShell: .\.venv\Scripts\Activate.ps1  |  Git Bash: source .venv/Scripts/activate
pip install -e ".[dev]"
pre-commit install
pytest
```

Full activation/troubleshooting (and the Dropbox-sync caveat) is in
[`docs/python-setup.md`](docs/python-setup.md).

Configuration is a YAML file (`config.yaml`, gitignored) validated by Pydantic; copy
`config.example.yaml` *(task 4)* and fill it in. The config path is read from `BTB_CONFIG`.

This README is fleshed out into the full operations reference in task 26.
