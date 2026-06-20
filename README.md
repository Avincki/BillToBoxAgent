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

## Remote access (Tailscale + Caddy)

The dashboard is reached over the tailnet at **`https://homecenter.<tailnet>.ts.net:8003/`**.
It binds **`127.0.0.1:9003`** only (loopback — `web.host`/`web.port` in config) and **never** a
public interface; **Caddy** terminates TLS on `:8003` and reverse-proxies to it, reusing the
node's shared `tailscale cert`. We do **not** use `tailscale serve` or `tailscale funnel`
(CONVENTIONS.md §11; decisions.md C2/#12). The Pi is the same Tailscale node as HomeEnergyCenter
(hostname `homecenter`); co-located apps are told apart by port (`:8000`→HEC, `:8003`→BillToBox).

**Prerequisites** (one-time, mostly already done for HEC): Tailscale up on the Pi; **MagicDNS +
HTTPS certificates enabled** in the Tailscale admin console; Caddy installed with a shared
`/etc/caddy/certs/` directory (root-owned, readable by the `caddy` user).

**1. Issue / refresh the cert** (writes `<host>.crt` + `<host>.key`):

```bash
cd /etc/caddy/certs
sudo tailscale cert homecenter.<tailnet>.ts.net      # creates homecenter.<tailnet>.ts.net.{crt,key}
sudo chown caddy:caddy homecenter.<tailnet>.ts.net.*
```

**2. Add the Caddy block.** Append [`deploy/caddy/billtobox.Caddyfile`](deploy/caddy/billtobox.Caddyfile)
to the shared `/etc/caddy/Caddyfile` (or `import` it), replacing `<tailnet>` with your tailnet name:

```caddyfile
homecenter.<tailnet>.ts.net:8003 {
	tls /etc/caddy/certs/homecenter.<tailnet>.ts.net.crt /etc/caddy/certs/homecenter.<tailnet>.ts.net.key
	reverse_proxy 127.0.0.1:9003
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

**3. Renewal.** Tailscale certs are short-lived; refresh them on a schedule. HEC already runs a
weekly **root cron** that re-issues the shared cert and reloads Caddy — BillToBox reuses the same
cert, so no extra job is needed. If setting it up fresh:

```cron
# /etc/cron.d/tailscale-cert  (weekly, as root)
0 4 * * 0  root  cd /etc/caddy/certs && tailscale cert homecenter.<tailnet>.ts.net && chown caddy:caddy homecenter.<tailnet>.ts.net.* && systemctl reload caddy
```

**Verify** from another tailnet device: `https://homecenter.<tailnet>.ts.net:8003/` returns the
invoice list over the real cert. Plain HTTP to `:8003` and any attempt to reach `127.0.0.1:9003`
from off-box are both refused (the dashboard listens only on loopback; Caddy serves HTTPS only).

This README is fleshed out into the full operations reference in task 26.
