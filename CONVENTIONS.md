# CONVENTIONS.md — BillToBoxAgent

> **Purpose.** This file records the exact conventions used by the existing
> **HomeEnergyCenter** project, so the BillToBoxAgent can mirror them rather than
> invent new patterns. It is the output of WORKPLAN task 1 and the reference for
> every subsequent task. **Read this before writing any scaffolding.**
>
> **Provenance.** Derived by inspecting
> `C:\Users\AlexVinckier\Dropbox (Personal)\Python\HomeEnergyCenter\HomeEnergyCenter`
> on **2026-06-19** (package `energy_orchestrator`, version 0.1.0).
>
> **Governing rule** (from CLAUDE.md Step 0): *"Do not introduce any pattern not
> already present in homeenergycenter unless it is strictly unavoidable."* Where
> the BillToBox CLAUDE.md / WORKPLAN / handover brief assume a pattern that
> HomeEnergyCenter does **not** actually use, this document follows the real
> HomeEnergyCenter convention and flags the divergence in **§13**. The few genuinely
> unavoidable additions (scheduled worker, two-process split, WAL) are justified
> there too.

---

## 1. At-a-glance summary

| Area | HomeEnergyCenter (HEC) actual convention | BillToBoxAgent application |
|---|---|---|
| Dependency manager | **venv + pip**, editable install (`pip install -e .`). No lockfile; ranges pinned in `pyproject.toml`. | Same. |
| Build backend | `hatchling`, wheel packages = `["src/<pkg>"]` | `["src/billtobox_agent"]` |
| Layout | **src layout**: `src/energy_orchestrator/`, domain sub-packages, each `__init__.py` re-exports with explicit `__all__` | `src/billtobox_agent/` |
| Entry point | `main.py` at repo root → `uvicorn.run(...)` | `main.py` (dashboard) **+** a worker entry (see §10) |
| Config | **YAML** (`config.yaml`, gitignored) + `config.example.yaml`, validated by **Pydantic v2**. Secrets are `SecretStr` *inside* the YAML. **No `.env`.** | Same YAML pattern (see §6, §13-A) |
| Config path injection | One env var `EO_CONFIG` points at the file | `BTB_CONFIG` |
| Logging | **structlog + stdlib**, two handlers: rotating JSON file + stderr console; `configure_logging(LoggingConfig)`, idempotent | Same; log file `billtobox_agent.log` |
| Database | **SQLAlchemy 2.0 async (aiosqlite) ORM** + **Alembic** migrations | Same (see §8) |
| WAL mode | **Not set in HEC** | **Add** (two processes — see §8, §13-D) |
| Testing | pytest + pytest-asyncio (`asyncio_mode=auto`), `tests/unit` + `tests/integration` + `tests/fixtures` | Same |
| Lint/format/type | ruff + black + mypy `--strict`, line length **100**, target **py311** | Same |
| systemd | **One** `Type=simple` long-lived unit, inline `Environment=`, dedicated service user, hardening block | Timer+oneshot worker **+** dashboard service (see §10, §13-B) |
| Remote access | **`tailscale cert` + uvicorn-native TLS** (or Caddy reverse proxy). **No `tailscale serve`.** | Join the planned Caddy multi-app scheme (see §11, §13-C) |
| MagicDNS scheme | Single node `homecenter.<tailnet>.ts.net`; apps differ by **port** (8000, 8001, 8002…) | Claim next free port (see §11, §13-E) |

---

## 2. Dependency management & packaging

- **Tool:** plain `venv` + `pip`. Install with `pip install -e .` (or `pip install -e ".[dev]"`).
  Production deploy on the Pi: `python3 -m venv .venv && source .venv/bin/activate && pip install -e .`.
- **No lockfile** (`poetry.lock` / `requirements.txt` / `uv.lock` are absent). Dependencies are
  declared in `pyproject.toml` with **bounded ranges**, e.g. `fastapi>=0.104,<1.0`.
- **`pyproject.toml` structure** (mirror this):
  - `[build-system]` → `requires = ["hatchling"]`, `build-backend = "hatchling.build"`.
  - `[project]` → `name`, `version = "0.1.0"`, `requires-python = ">=3.11"`,
    `authors = [{ name = "Alex Vinckier", email = "alex.vinckier@kintsunai.com" }]`,
    `license = { text = "MIT" }`, then `dependencies = [...]`.
  - `[project.optional-dependencies]` → `dev = [...]` (pytest stack + ruff/black/mypy/pre-commit),
    and optionally `docs = [...]`.
  - `[tool.hatch.build.targets.wheel]` → `packages = ["src/billtobox_agent"]`.
- **HEC dev dependency set** (carry over the testing/quality stack): `pytest`, `pytest-asyncio`,
  `pytest-mock`, `pytest-cov`, `httpx`, `hypothesis`, `black`, `ruff`, `mypy`, `pre-commit`,
  `types-pyyaml`.

## 3. Directory layout & module style

```
<repo root>/
  main.py                      # entry point (uvicorn for the dashboard)
  pyproject.toml
  config.example.yaml          # committed template
  config.yaml                  # gitignored, real values
  alembic.ini
  alembic/
    env.py
    script.py.mako
    versions/0001_initial.py   # NNNN_slug.py
  src/billtobox_agent/
    __init__.py
    config/   { __init__.py, loader.py, models.py }
    data/     { __init__.py, database.py, models.py, repositories.py, unit_of_work.py }
    monitoring/ { __init__.py, logging_config.py }
    web/      { __init__.py, app.py, api.py, views.py, dependencies.py,
                static/, templates/ }
    utils/    { __init__.py, clock.py, ... }
    ...one sub-package per domain (mail sources, drive, extraction, billtobox)
  tests/
    __init__.py
    unit/        { __init__.py, test_*.py }
    integration/ { __init__.py, test_*.py }
    fixtures/    { __init__.py, *.yaml }
  docs/
```

Module-style rules observed throughout HEC:
- **`from __future__ import annotations`** is the first import in essentially every module.
- Each sub-package `__init__.py` **re-exports** the package's public names and declares a sorted
  **`__all__`** (see `config/__init__.py`, `data/__init__.py`).
- Domain code is grouped into sub-packages by responsibility, not by layer. Base classes live in
  `base.py`, error types in `errors.py`, a registry in `registry.py` within the relevant package.
- Type hints are mandatory and checked under mypy `--strict` (see §5).

## 4. Naming map (HEC → BillToBox)

| Thing | HEC value | BillToBox value |
|---|---|---|
| PyPI/project name | `energy-orchestrator` | `billtobox-agent` |
| Import package | `energy_orchestrator` | `billtobox_agent` |
| Env var prefix | `EO_` | `BTB_` |
| Config path var | `EO_CONFIG` | `BTB_CONFIG` |
| DB url/path vars (Alembic) | `EO_DB_URL`, `EO_SQLITE_PATH` | `BTB_DB_URL`, `BTB_SQLITE_PATH` |
| TLS env vars | `EO_SSL_KEYFILE`, `EO_SSL_CERTFILE` | `BTB_SSL_KEYFILE`, `BTB_SSL_CERTFILE` |
| Log filename | `energy_orchestrator.log` | `billtobox_agent.log` |
| Default DB path | `data/orchestrator.db` | `data/billtobox.db` |
| Service user / home | `homecenter` / `/opt/homecenter` | `billtobox` / `/opt/billtobox` (proposed) |
| App dir on Pi | `/opt/homecenter/HomeEnergyCenter` | `/opt/billtobox/BillToBoxAgent` (proposed) |
| systemd unit base | `homeenergycenter.service` | `billtobox-agent.{service,timer}`, `billtobox-dashboard.service` |

## 5. Code style, linting, typing, CI

All configured in `pyproject.toml`:
- **`[tool.black]`** — `line-length = 100`, `target-version = ["py311"]`.
- **`[tool.ruff]`** — `line-length = 100`, `target-version = "py311"`, `src = ["src", "tests"]`.
  - `[tool.ruff.lint] select` = `E,W,F,I,B,UP,SIM,C4,RET,ASYNC,PL,RUF`.
  - `ignore` = `PLR0913,PLR2004,SIM117`.
  - `[tool.ruff.lint.per-file-ignores]` → `"tests/**" = ["PLR", "PLC0415", "S101"]`.
- **`[tool.mypy]`** — `python_version = "3.11"`, `strict = true` plus the explicit strict flags HEC
  lists, `plugins = ["pydantic.mypy"]`. Per-module overrides for libraries with bad stubs.
- **`[tool.pytest.ini_options]`** — `minversion = "7.4"`, `testpaths = ["tests"]`,
  `asyncio_mode = "auto"`, `addopts = ["--strict-markers", "--strict-config", "-ra"]`.
- **`[tool.coverage.run]`** — `source = ["src/billtobox_agent"]`, `branch = true`;
  `[tool.coverage.report] exclude_lines` = `pragma: no cover`, `raise NotImplementedError`,
  `if TYPE_CHECKING:`.
- **`.pre-commit-config.yaml`** — pre-commit-hooks (trailing-whitespace, end-of-file-fixer,
  check-yaml, check-toml, check-added-large-files, check-merge-conflict, mixed-line-ending `--fix=lf`),
  `ruff` (`--fix --exit-non-zero-on-fix`) + `ruff-format`, `black`, `mypy` (files `^src/`).
- **`.github/workflows/ci.yml`** — on push/PR to `main`; matrix `python-version: ["3.11", "3.12"]`
  on `ubuntu-latest`; steps: `pip install -e ".[dev]"` → `ruff check .` → `ruff format --check .` →
  `black --check .` → `mypy src` → `pytest --cov --cov-report=term-missing`.

## 6. Configuration & secrets  ⚠️ see §13-A

HomeEnergyCenter does **not** use `.env`. The pattern is:
- A single **YAML** file, `config.yaml`, **gitignored**; a committed `config.example.yaml` template
  with `REPLACE_ME` placeholders.
- Located via `Path(os.environ.get("EO_CONFIG", "config.yaml"))` in `main.py`; the systemd unit sets
  `Environment=EO_CONFIG=/opt/.../config.yaml`.
- Loaded by `config/loader.py::load_config(path) -> AppConfig`: `path.read_text()` →
  `yaml.safe_load` → `AppConfig.model_validate(data)`, raising a custom `ConfigError` with a
  formatted message for missing file / parse error / validation error.
- `config/models.py` defines a tree of **Pydantic v2** models. Conventions there:
  - Base class `_StrictModel(BaseModel)` with `model_config = ConfigDict(extra="forbid", frozen=True)`.
  - Reusable `Annotated` types (`Host`, `Port`, `Percent`) with validators.
  - **Secrets are `pydantic.SecretStr`** fields *within* the config (e.g. `auth_token`, `api_key`,
    `client_secret`), so they never print and stay in the gitignored `config.yaml` — **not** in
    environment variables.
  - Defaults via `Field(default=...)`; cross-field rules via `@model_validator(mode="after")`.
  - The root model `AppConfig` composes section models (`storage`, `logging`, `web`, …) with
    `Field(default_factory=...)` for optional sections.
- For BillToBox, map the CLAUDE.md key list into YAML sections (proposed shape):
  ```yaml
  anthropic:   { api_key: REPLACE_ME }
  google:      { client_id: ..., client_secret: REPLACE_ME, token_path: data/google_token.json }
  microsoft:   { client_id: ..., client_secret: REPLACE_ME, tenant_id: ..., token_path: data/ms_token.json }
  drive:       { root_folder_id: ... }
  billtobox:   { mailbox_address: ..., sender_address: ... }
  smtp:        { host: ..., port: 587, username: ..., password: REPLACE_ME }
  extraction:  { confidence_threshold: 0.85 }
  accounting:  { fiscal_year_start_month: 1, quarter_date_field: invoice_date, fy_label_prefix: "" }
  storage:     { sqlite_path: data/billtobox.db }
  sources:     { polling: [gmail, outlook] }     # doccle stubbed
  web:         { host: 127.0.0.1, port: 8003 }   # see §11/§13-E
  logging:     { log_dir: logs, level: INFO, retention_days: 30 }
  ```
- **Keep out of source control:** `config.yaml`, OAuth token files, `*.db`, `logs/`, TLS `*.key`/`*.crt`,
  the PDF cache dir. See §12 `.gitignore`.

## 7. Logging

From `monitoring/logging_config.py` — `configure_logging(config: LoggingConfig) -> None`:
- **structlog feeding stdlib** via `structlog.stdlib.LoggerFactory()` + `ProcessorFormatter`, so
  library loggers (uvicorn, sqlalchemy) and app loggers share one pipeline. App code may use either
  `logging.getLogger(__name__)` or `structlog.stdlib.get_logger(__name__)`.
- **Two handlers on the root logger:**
  1. `RotatingFileHandler(log_dir / "<pkg>.log", maxBytes=10 MiB, backupCount=max(1, retention_days), encoding="utf-8")` with a **JSON** renderer.
  2. `StreamHandler(sys.stderr)` with `structlog.dev.ConsoleRenderer(colors=False)`.
- **Shared processors:** `merge_contextvars`, `add_log_level`, `add_logger_name`, a custom
  local-timezone timestamper (ISO, millisecond precision, with UTC offset — not `Z`),
  `StackInfoRenderer`, `format_exc_info`.
- **Idempotent:** handlers are tagged (`_<pkg>_handler` attribute) and stripped before re-adding, so
  repeated calls don't duplicate output and foreign handlers (pytest caplog) survive. Both `main.py`
  and the web app factory's lifespan call it.
- Root level set from `config.level`; noisy third-party loggers are quieted explicitly
  (`logging.getLogger("...").setLevel(...)`).
- `LoggingConfig`: `log_dir: Path = "logs"`, `level: Literal["DEBUG"|...|"CRITICAL"] = "INFO"`,
  `retention_days: int = 30`.
- Logs live under `logs/` and are **gitignored**.

**Web log inspection + agent activity (BillToBox observability — confirmed, see `decisions.md` §2).**
HEC surfaces logs in the dashboard via a **Server-Sent-Events stream**: `GET /api/logs/stream`
tails the rotating JSON file (replaying the current session, or `?replay_hours=N`, reopening across
rotation — `web/api.py::stream_logs` / `_tail_log_sse`), rendered live on `/logs` and reused by
`/debug`. BillToBox **mirrors this** (Layer A) **and adds** a structured **`agent_events`** SQLite
table (Layer B): every tool call / result / decision / error, with secrets and PDF bytes redacted,
shown as a dashboard "Agent activity" timeline + per-invoice audit trail, and exposed to the agent
itself as a read-only **`get_agent_events(invoice_id?, run_id?, limit)`** tool so it can review its
own history and self-correct. `agent_events` is the agent analogue of HEC's `decisions` table; the
worker is its sole writer. Full schema + rationale in `decisions.md` §2.

## 8. Database & migrations  ⚠️ see §13-D

- **SQLAlchemy 2.0 async ORM** over **aiosqlite**. `data/database.py` provides:
  - `make_sqlite_url(path)` → `sqlite+aiosqlite:///<abs posix path>` (or `:memory:`).
  - `create_engine(sqlite_path, *, echo=False)` → creates parent dirs, `create_async_engine(..., future=True)`.
  - `create_session_factory(engine)` → `async_sessionmaker(engine, expire_on_commit=False)`.
  - `init_schema(engine)` → `Base.metadata.create_all` (**fresh installs and tests only**).
  - `drop_schema(engine)` → tests only.
- **ORM models** in `data/models.py`: `class Base(DeclarativeBase)`, models use
  `Mapped[...]` + `mapped_column(...)`. Timestamps are `DateTime(timezone=True)` defaulted via a
  `_utcnow()` helper (`datetime.now(UTC)`). Status/enum columns back onto Python `StrEnum`s.
- **Repositories** (`data/repositories.py`) + a **`UnitOfWork`** (`data/unit_of_work.py`) wrap session
  access; the web/worker layers go through these, not raw sessions.
- **Migrations: Alembic.** `alembic.ini` → `script_location = alembic`, `prepend_sys_path = . src`,
  `file_template = %%(rev)s_%%(slug)s`, `timezone = UTC`. `alembic/env.py` resolves the DB URL from
  `EO_DB_URL` → `EO_SQLITE_PATH` → `data/orchestrator.db`, uses `render_as_batch=True` for SQLite,
  and runs async online migrations. Versions are numbered `0001_initial.py`, `0002_*.py`, … with
  `revision`/`down_revision` strings. **Production runs `alembic upgrade head`**; tests use `init_schema`.
- **WAL:** HEC does **not** set `journal_mode=WAL` anywhere. BillToBox **must** add it (two
  processes share the DB — §13-D) via a SQLAlchemy `connect`/`PRAGMA` event:
  `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;` on each connection. Cover it in an integration
  test (WORKPLAN task 5). The `content_hash` UNIQUE constraint and the `invoices`/`runs` tables come
  from CLAUDE.md §SQLite Schema.

## 9. Testing

- Layout: `tests/unit/` (fast, isolated) and `tests/integration/` (DB/web), plus `tests/fixtures/`
  (e.g. `config_minimal.yaml`). Every test dir has `__init__.py`.
- `pytest` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed). Mock external clients
  with `pytest-mock`; `httpx` for FastAPI; `hypothesis` for property tests (good fit for
  `period_for` in WORKPLAN task 6).
- Test files named `test_<module>.py` mirroring the source module. Fixtures use `tmp_path`; an
  `autouse` fixture resets global state (e.g. root-logger handlers) between tests — see
  `tests/unit/test_monitoring_logging.py` for the idiom.

## 10. systemd  ⚠️ see §13-B

**HEC reality (`docs/raspberry-pi-setup.md` §5):** a single unit `homeenergycenter.service`,
`Type=simple`, long-lived. Fields to mirror exactly:

```ini
[Unit]
Description=...
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=homecenter
Group=homecenter
WorkingDirectory=/opt/homecenter/HomeEnergyCenter
Environment=EO_CONFIG=/opt/homecenter/HomeEnergyCenter/config.yaml
ExecStart=/opt/homecenter/HomeEnergyCenter/.venv/bin/python main.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/homecenter/HomeEnergyCenter

[Install]
WantedBy=multi-user.target
```

Key facts:
- **Config is injected with inline `Environment=`, NOT `EnvironmentFile=`** (there is no env file —
  config is YAML). The handover brief's `EnvironmentFile=/home/pi/.../.env` example does **not**
  match HEC.
- Dedicated **system service user** (`homecenter`) with home = app parent, plus the hardening block
  (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths`).
- TLS cert paths, when used, are added as extra `Environment=` lines inside `[Service]` (§11).

**BillToBox extension (unavoidable — §13-B):** BillToBox needs a *scheduled* worker and a *separate*
long-lived dashboard, so it legitimately adds the timer/oneshot pattern that HEC lacks. Keep every
field convention above; the new shape is:
- `billtobox-agent.service` — `Type=oneshot`, `ExecStart=.../.venv/bin/python -m billtobox_agent.worker`
  (one cycle, exits). `Environment=BTB_CONFIG=...`. No `[Install]` (driven by the timer).
- `billtobox-agent.timer` — `OnCalendar=` sourced from config/var (default `*-*-* 07:00:00`),
  `Persistent=true`, `WantedBy=timers.target`.
- `billtobox-dashboard.service` — `Type=simple`, long-lived, `ExecStart=.../python main.py`, same
  hardening; this is the unit most directly analogous to HEC's single service.

## 11. Tailscale & networking  ⚠️ see §13-C / §13-E

**HEC reality (`docs/raspberry-pi-setup.md` §6):** HEC does **not** use `tailscale serve`. Remote
access is:
- One Tailscale node, MagicDNS name **`homecenter.<tailnet>.ts.net`**; short name `homecenter`.
- Per-tailnet HTTPS enabled in the admin console; a cert issued with **`sudo tailscale cert <host>`**
  (writes `<host>.crt` / `<host>.key`).
- TLS terminated **inside uvicorn**: `main.py` passes `ssl_keyfile=os.environ.get("EO_SSL_KEYFILE")`
  / `ssl_certfile=os.environ.get("EO_SSL_CERTFILE")` (both `None` → plain HTTP). Cert paths live in
  the systemd unit as `Environment=` lines, not in `config.yaml`.
- **Multiple apps on the Pi** (§6.6, "design only" but decided): one Tailscale node, apps told apart
  by **port**, with **Caddy** as a single TLS front door reverse-proxying to loopback-bound apps:
  - public `:8000 → 127.0.0.1:9000` HomeEnergyCenter
  - public `:8001 → 127.0.0.1:9001` GeoRiskMonitor (planned)
  - public `:8002 → 127.0.0.1:9002` FitnessCoach (planned)
  In that model each app binds `127.0.0.1:90xx` and does **not** terminate TLS itself; Caddy owns the
  shared cert in `/etc/caddy/certs/`, renewed by a weekly root cron running `tailscale cert`.

**BillToBox application:** the dashboard is a third app on the same node. The cleanest fit is to
**join the Caddy multi-app scheme** (§13-C): bind the dashboard to `127.0.0.1` on its own internal
port and add a Caddy block exposing it on the next free public port. Do **not** use `tailscale serve`
or `tailscale funnel`. Port allocation needs the user's pick (§13-E); proposal: public **`:8003`** →
internal `127.0.0.1:9003`. If the user prefers the single-app uvicorn-TLS path instead, mirror HEC's
`BTB_SSL_KEYFILE`/`BTB_SSL_CERTFILE` env-var approach.

## 12. `.gitignore` essentials

Mirror HEC's `.gitignore` (bytecode, `build/`/`dist/`/`*.egg-info/`, `.venv/`, test/coverage caches,
`.mypy_cache/`/`.ruff_cache/`, IDE dirs, OS cruft). Project-local entries to carry over and extend:
`*.db`, `*.sqlite*`, **`config.yaml`**, `config.local.yaml`, `*.bak`, `logs/`, `*.log*`,
`*.crt`/`*.key`/`*.pem`, `.claude/`, `*conflicted copy*`. **Add for BillToBox:** OAuth token files
(e.g. `data/google_token.json`, `data/ms_token.json` — or a `tokens/` dir) and the **PDF cache dir**.
HEC keeps `.env`/`.env.*` ignored (with `!.env.example`) even though it uses YAML — harmless to keep.

---

## 13. Deviations from CLAUDE.md / WORKPLAN / handover brief — decisions before scaffolding

The BillToBox planning docs were written *assuming* certain HEC conventions. Inspection shows some
differ. Per the governing rule, the resolution is to follow **actual HEC**. Items **A** and **C**
contradicted explicit text in CLAUDE.md and were **confirmed with the user on 2026-06-19** (both in
favour of matching HEC); **B**, **D**, **E** are unavoidable additions documented for transparency.

**A. Config format — YAML, not `.env`. ✅ CONFIRMED 2026-06-19 (use YAML, match HEC).** CLAUDE.md
§Configuration and the handover brief assume a flat `.env` file with keys like `ANTHROPIC_API_KEY`,
loaded at startup, referenced by `EnvironmentFile=`. HEC uses **YAML + Pydantic + `SecretStr`** with
no `.env`. **Decision:** use HEC's YAML pattern. This reshapes WORKPLAN task 4
(`config.example.yaml` instead of `.env.example`; a Pydantic `AppConfig` instead of flat env
accessors) and removes the need for `EnvironmentFile=`.

**B. systemd timer/oneshot split (unavoidable).** HEC has one `Type=simple` service. BillToBox needs
a scheduled worker + a separate dashboard, so the timer+oneshot+dashboard trio (WORKPLAN task 22) is
justified. All *field* conventions still mirror HEC; config is injected with inline `Environment=`,
not `EnvironmentFile=` (ties to A). No decision needed beyond A.

**C. Tailscale — `tailscale cert` + Caddy, not `tailscale serve`. ✅ CONFIRMED 2026-06-19 (join the
Caddy multi-app scheme).** CLAUDE.md §Networking and WORKPLAN task 23 say expose via
`tailscale serve`. HEC uses `tailscale cert` with uvicorn-native TLS today and a **Caddy** multi-app
reverse proxy as the decided multi-app plan. **Decision:** join the Caddy scheme — the dashboard
binds `127.0.0.1` on an internal port and a Caddy block exposes it on the next public port, sharing
the one Tailscale cert; drop `tailscale serve`. Reshapes WORKPLAN task 23.

**D. SQLite WAL (unavoidable addition).** CLAUDE.md mandates WAL; HEC never sets it (HEC runs a single
process). BillToBox runs **two** processes (timer worker + dashboard) against one DB, so WAL + a
single-writer discipline (worker writes; dashboard does reads + small status writes) is necessary.
Implement via a PRAGMA on connect and test it (task 5). No decision needed.

**E. Dashboard port. ✅ DECIDED 2026-06-19 (`:8003` → `127.0.0.1:9003`).** HEC reserves `:8000`; the
multi-app plan pencils in `:8001` (GeoRiskMonitor) and `:8002` (FitnessCoach). BillToBox claims the
next free port: **public `:8003` → internal `127.0.0.1:9003`** (dashboard binds `127.0.0.1`, Caddy
fronts TLS on `:8003`). Recorded in `decisions.md` §1/§4.

**F. Architecture note.** HEC starts its worker (tick loop) *inside* the dashboard's uvicorn process
(one service). BillToBox deliberately splits them (timer-driven worker vs. always-on dashboard) —
this is the reason D matters and why the two processes must coordinate only through SQLite + Google
Drive, never shared in-process state.
