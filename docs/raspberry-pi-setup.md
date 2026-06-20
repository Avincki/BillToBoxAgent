# BillToBoxAgent — Raspberry Pi deployment

A start-to-finish walkthrough to deploy BillToBoxAgent on a Raspberry Pi. It is **headless** — there
is no attached screen, no kiosk. The app runs as **two** systemd units: a timer-driven worker
(`billtobox-agent.timer` → `billtobox-agent.service`) and a long-lived dashboard
(`billtobox-dashboard.service`).

BillToBox is designed to run as a **co-located app on the same Pi as HomeEnergyCenter** — the
`homecenter` Tailscale node, sharing one `tailscale cert` and one Caddy front door (CONVENTIONS.md
§11). If HEC already runs on this Pi, the OS/Tailscale/Caddy steps (§2, §3, most of §6) are already
done — skip them and jump to **§4**. The guide is still complete enough to deploy from a blank SD
card if BillToBox is the first app on the box.

Every path, user, unit name, port, and env var here matches `CONVENTIONS.md` / `decisions.md`:

| Thing | Value |
|---|---|
| Service user | `billtobox` (system user, home `/opt/billtobox`) |
| App directory | `/opt/billtobox/BillToBoxAgent` |
| Config | `config.yaml` in the app dir; path read from `$BTB_CONFIG` |
| Database | `data/billtobox.db` (under the app dir) |
| Logs | `logs/billtobox_agent.log` (under the app dir) |
| Worker | `python -m billtobox_agent.worker` (oneshot; `--dry-run` to preview) |
| Dashboard | `python main.py` → binds `127.0.0.1:9003` |
| Public URL | `https://homecenter.<tailnet>.ts.net:8003/` (Caddy → `127.0.0.1:9003`) |

---

## 1. What you need

**Hardware:** a Raspberry Pi 4/5 (the `homecenter` node) running **Raspberry Pi OS Lite (64-bit)**,
on Ethernet (preferred). No monitor/keyboard needed after flashing.

**Accounts & credentials** (gather before you start):

- **Anthropic** API key (`sk-ant-…`).
- **Google** OAuth client (Desktop app) with Gmail + Drive enabled — `client_id` + `client_secret`.
- **Microsoft** app registration (public client) for Outlook — `client_id` (+ optional `tenant_id`).
- **Billtobox** upload mailbox address + a sender address Billtobox accepts for it.
- **SMTP** host + an **app password** for the sender (not the normal login password).
- A **second machine with a browser** for the one-time Google consent — refresh tokens are **never
  minted on the headless Pi** (§4.3).

---

## 2. (Shared) Flash Raspberry Pi OS Lite — *skip if HEC is already on this Pi*

1. Install **Raspberry Pi Imager** on your laptop and launch it.
2. **Device:** Raspberry Pi 5 (or 4). **OS:** **Raspberry Pi OS Lite (64-bit)** — Lite is enough;
   BillToBox is headless (no desktop).
3. **Storage:** your microSD / SSD.
4. **Next → Edit Settings:**
   - **Hostname:** `homecenter` (the shared node name).
   - **Username/password:** your admin account (e.g. `alex`) + a real password — **not** `pi` /
     `raspberry`. The dedicated `billtobox` service user is created later (§4.0).
   - **Wireless LAN:** only if you can't use Ethernet.
   - **Locale:** time zone `Europe/Brussels`.
   - **Services → Enable SSH** (public-key preferred).
5. **Save → Write.** Insert the card, connect Ethernet, power on. First boot takes a minute or two.

---

## 3. (Shared) First login, update, packages — *skip if HEC already did this*

```bash
ssh alex@homecenter.local
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
    python3 python3-venv python3-pip python3-dev \
    git build-essential libssl-dev libffi-dev \
    sqlite3 curl ca-certificates
python3 --version          # 3.11+ (the project requires >= 3.11)
sudo reboot                # only if the kernel/firmware was updated
```

---

## 4. Install BillToBox

### 4.0 Create the `billtobox` service user

```bash
sudo useradd --system --create-home --home-dir /opt/billtobox --shell /bin/bash billtobox
sudo -u billtobox -i           # you're now billtobox, in /opt/billtobox
```

> The app runs under its own dedicated account with `/opt/billtobox` as its home (**not**
> `/home/billtobox` — it's a system user with an explicit `--home-dir`). This keeps the agent
> separate from your normal user data and makes the systemd hardening (§5) tighter.

### 4.1 Get the code → `/opt/billtobox/BillToBoxAgent`

As the `billtobox` user, either clone:

```bash
cd ~                            # = /opt/billtobox
git clone <your-repo-url> BillToBoxAgent     # → /opt/billtobox/BillToBoxAgent
cd BillToBoxAgent
```

…or rsync from your PC (from WSL/Git Bash), excluding the venv, caches, and DB:

```bash
rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='*.db' \
  ./BillToBoxAgent/ billtobox@homecenter.local:/opt/billtobox/BillToBoxAgent/
```

### 4.2 Virtualenv + install (runtime only)

```bash
cd ~/BillToBoxAgent             # = /opt/billtobox/BillToBoxAgent
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e .                # runtime deps only (no [dev] on the Pi)
```

### 4.3 One-time OAuth consent — *on a machine with a browser*

The headless Pi can't open a browser, so mint the refresh tokens elsewhere and copy them over.

**On your laptop/PC** (a checkout of the repo with the venv installed), create a `config.yaml` with
at least the `google:` and `microsoft:` blocks filled in (the same client credentials you'll use on
the Pi), then:

```bash
export BTB_CONFIG=./config.yaml
python scripts/auth_google.py       # opens a browser → Gmail (read-only) + Drive consent
                                    # writes data/google_token.json
python scripts/auth_ms.py           # prints a URL + code to enter in a browser (Outlook Mail.Read)
                                    # writes data/ms_token.json
```

**Copy both token files to the Pi** (into the app's `data/` dir) and fix ownership:

```bash
ssh billtobox@homecenter.local 'mkdir -p /opt/billtobox/BillToBoxAgent/data'
scp data/google_token.json data/ms_token.json \
    billtobox@homecenter.local:/opt/billtobox/BillToBoxAgent/data/
ssh billtobox@homecenter.local 'chmod 600 /opt/billtobox/BillToBoxAgent/data/*_token.json'
```

> The Microsoft device-code flow can alternatively be run **on the Pi** (it only prints a URL + code
> to open in any browser). Google's flow needs a browser on the same machine, so it always runs off
> the Pi.

### 4.4 Configure (`config.yaml`)

On the Pi, as `billtobox`:

```bash
cd ~/BillToBoxAgent
cp config.example.yaml config.yaml
nano config.yaml                # fill anthropic / google / microsoft / billtobox / smtp
chmod 600 config.yaml           # it holds secrets
```

The token paths (`google.token_path` / `microsoft.token_path`) default to the files you copied in
§4.3. Leave `web.host`/`web.port` at `127.0.0.1` / `9003` (Caddy fronts TLS — §6).

### 4.5 Initialise the database

```bash
source .venv/bin/activate
alembic upgrade head            # creates/migrates data/billtobox.db
```

> Alembic resolves the DB from `$BTB_DB_URL`, then `$BTB_SQLITE_PATH`, else `data/billtobox.db` (the
> default `storage.sqlite_path`). If you changed `storage.sqlite_path` in config, export
> `BTB_SQLITE_PATH=<that path>` before running alembic.

### 4.6 Smoke-test by hand

```bash
export BTB_CONFIG=/opt/billtobox/BillToBoxAgent/config.yaml

# Worker: dry-run first — logs intended actions, makes no Drive/DB writes.
python -m billtobox_agent.worker --dry-run
# Then a real cycle (fetches, extracts, files):
python -m billtobox_agent.worker

# Dashboard: start it, then curl it from another shell.
python main.py &
curl -sI http://127.0.0.1:9003/      # expect HTTP/1.1 200 OK
kill %1
```

If the worker can't reach Gmail/Drive/Graph, re-check the tokens (§4.3) and the `google`/`microsoft`
config; if extraction fails, check the Anthropic key.

---

## 5. Run automatically (systemd)

The three units live in the repo at `deploy/systemd/`. Install them with the helper, which symlinks
them into `/etc/systemd/system/` and reloads systemd:

```bash
exit                                              # back to your admin (sudo) shell
sudo bash /opt/billtobox/BillToBoxAgent/scripts/install-units.sh
```

> **Path warning:** every path in the units must exist on disk. Verify before enabling:
>
> ```bash
> getent passwd billtobox                                          # 6th field = /opt/billtobox
> ls -d /opt/billtobox/BillToBoxAgent/.venv/bin/python             # must succeed
> ls /opt/billtobox/BillToBoxAgent/config.yaml                     # must succeed
> ```
>
> If systemd later logs `Failed to set up mount namespacing … status=226/NAMESPACE`, a path in the
> unit doesn't exist — fix it, `sudo systemctl daemon-reload`, and restart.

Verify and enable:

```bash
sudo systemd-analyze verify \
  /etc/systemd/system/billtobox-agent.service \
  /etc/systemd/system/billtobox-agent.timer \
  /etc/systemd/system/billtobox-dashboard.service          # prints nothing if OK

sudo systemctl enable --now billtobox-agent.timer          # schedules the daily worker
sudo systemctl enable --now billtobox-dashboard.service    # starts the dashboard

# Run one worker cycle now (the timer otherwise fires daily at 07:00):
sudo systemctl start billtobox-agent.service
journalctl -u billtobox-agent.service -n 50 --no-pager     # expect it to exit 0
systemctl list-timers billtobox-agent.timer                # shows the next run
systemctl status billtobox-dashboard.service               # expect "active (running)"
```

To change the schedule, use a drop-in instead of editing the unit:

```bash
sudo systemctl edit billtobox-agent.timer
# [Timer]
# OnCalendar=
# OnCalendar=*-*-* 06:30:00
sudo systemctl daemon-reload && sudo systemctl restart billtobox-agent.timer
```

---

## 6. Remote access (shared Caddy + Tailscale cert)

The dashboard binds `127.0.0.1:9003` only; **Caddy** terminates TLS on public **`:8003`** over the
tailnet, reusing the `homecenter` node's shared `tailscale cert`. We do **not** use `tailscale
serve`/`funnel`. Full prose + the renewal cron are in the repo `README.md` ("Remote access"); the
short version:

**If HEC already runs here** (cert + Caddy exist): just add the BillToBox block and reload.

```bash
sudo tee -a /etc/caddy/Caddyfile < /opt/billtobox/BillToBoxAgent/deploy/caddy/billtobox.Caddyfile
sudo sed -i 's/<tailnet>/YOUR-TAILNET/g' /etc/caddy/Caddyfile     # e.g. tailnet-abc
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

**If this is a fresh Pi** (no HEC): install Caddy, enable HTTPS certs in the Tailscale admin console,
issue the cert into the shared dir, then add the block:

```bash
sudo apt install -y caddy
TAILNET_HOST=$(tailscale status --self --json \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
echo "$TAILNET_HOST"                                  # e.g. homecenter.tailnet-abc.ts.net
sudo mkdir -p /etc/caddy/certs && cd /etc/caddy/certs
sudo tailscale cert "$TAILNET_HOST"
sudo chown caddy:caddy "$TAILNET_HOST".crt "$TAILNET_HOST".key
# then add the deploy/caddy/billtobox.Caddyfile block (replace <tailnet>) and reload caddy.
```

**Verify** from another tailnet device: `https://homecenter.<tailnet>.ts.net:8003/` returns the
invoice list over the real cert. Plain HTTP to `:8003` and any off-box attempt to reach
`127.0.0.1:9003` are refused.

---

## 7. Day-to-day operations

**Force a run now** (instead of waiting for 07:00): `sudo systemctl start billtobox-agent.service`,
or use the dashboard's **Run now** button on `/runs`.

**Watch what it's doing:** the dashboard `/logs` page streams the live JSON log, and **Agent
activity** (`/activity`) plus each invoice's audit trail show every tool call. From the shell:
`journalctl -u billtobox-agent.service -f` and `journalctl -u billtobox-dashboard.service -f`.

**Approve / steer:** the **Exceptions** queue lists low-confidence and failed items; edit fields,
re-extract, approve, or approve the Billtobox send from the invoice detail page.

**Update to new code** — one-shot from your dev machine after `git push`:

```bash
ssh -t alex@homecenter.local "sudo -v && sudo systemctl stop billtobox-dashboard billtobox-agent.timer && sudo -u billtobox bash -lc 'cd ~/BillToBoxAgent && git pull --ff-only && source .venv/bin/activate && pip install -e . && alembic upgrade head' && sudo systemctl start billtobox-agent.timer billtobox-dashboard && sudo systemctl status billtobox-dashboard --no-pager | head -20"
```

…or step-by-step on the Pi:

```bash
sudo systemctl stop billtobox-dashboard billtobox-agent.timer
sudo -u billtobox -i
cd ~/BillToBoxAgent
git status                      # clean? (config.yaml is gitignored, won't show)
git pull --ff-only
source .venv/bin/activate
pip install -e .
alembic upgrade head            # apply any new migrations
exit
sudo systemctl start billtobox-agent.timer billtobox-dashboard
```

> Keep all settings in `config.yaml` (gitignored) — never edit tracked source on the Pi, or
> `git pull` will refuse. If you installed via rsync, replace step `git pull` with the same `rsync …`
> from your PC; everything else is identical.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `alembic` errors / "no such table" | You skipped §4.5. Run `alembic upgrade head` (set `BTB_SQLITE_PATH` if you changed the path). |
| Service won't start, `status=226/NAMESPACE` | A path in the unit doesn't exist (§5 path warning). Fix the unit, `daemon-reload`, restart. |
| Service starts then exits non-zero | `journalctl -u billtobox-agent.service -n 100 --no-pager`. Usual causes: a new required `config.yaml` field (diff against `config.example.yaml`), or a forgotten `alembic upgrade head`. |
| Gmail/Drive/Graph auth fails | The OAuth tokens expired or are missing. Re-run the consent scripts (§4.3) on your browser machine and re-copy `data/*_token.json`. |
| Anthropic 401 / extraction fails | Check `anthropic.api_key`; a 429/5xx is retried automatically (`max_attempts`). |
| Billtobox send fails (SMTP auth) | Use an **app password**, not the normal login; verify `smtp.host`/`port`/`use_tls`. |
| `database is locked` (rare) | Transient WAL contention between the worker and dashboard. WAL + single-writer discipline serialize writers; it resolves on its own. Don't run two workers at once. |
| `Address already in use` on `:9003` | A stale `main.py` is running. `sudo systemctl restart billtobox-dashboard`, or find it with `ss -ltnp | grep 9003`. |
| Reachable on LAN but not over Tailscale | Caddy block missing/misconfigured (§6). `sudo caddy validate` + check the cert files exist and are `caddy`-readable. |
| Cert expired (HTTPS warning) | Re-issue: `sudo tailscale cert homecenter.<tailnet>.ts.net` into `/etc/caddy/certs/`, `chown caddy`, `systemctl reload caddy`. Add the weekly renewal cron (README "Remote access"). |
| Dashboard 200 but empty | No invoices processed yet. Force a run (§7) and watch `/activity`. |

---

This guide is the document the task-25 on-Pi smoke-run follows; any real-world deviations found
during that run are folded back in here. The ops/troubleshooting reference is expanded in the
project `README.md` (task 26).
