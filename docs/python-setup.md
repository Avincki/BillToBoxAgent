# Local Python setup — BillToBoxAgent

How to get (and keep) a working local Python environment on Windows for this project.
The project targets **Python ≥ 3.11**; we use **3.12**. Mirrors HomeEnergyCenter's
venv+pip approach.

> **Note:** you only need this when running the app/tests **yourself** locally. The
> deployment environment (Raspberry Pi) is covered separately in
> `docs/raspberry-pi-setup.md`.

---

## TL;DR — the environment is already set up

A virtualenv already exists at **`.venv`** (Python 3.12.1), is marked Dropbox-ignored, and
has all dependencies installed. To use it, just **activate it** (pick the one line that
matches your tool), from the project root
`C:\Users\AlexVinckier\Dropbox (Personal)\Python\BillToBoxAgent`:

| Tool | Command (run once per new terminal) |
|---|---|
| **PowerShell** | `.\.venv\Scripts\Activate.ps1` |
| **Git Bash** | `source .venv/Scripts/activate` |
| **cmd** | `.venv\Scripts\activate.bat` |
| **PyCharm / VS Code** | set the interpreter to `.venv\Scripts\python.exe` (set once — see below) |

A successful activation adds a **`(.venv)`** prefix to your prompt. Verify:

```
python --version      # -> Python 3.12.x
pytest                # -> passes
```

---

## Background: the "3.8 vs 3.12" confusion

- Python **3.12** is installed on this machine
  (`%LOCALAPPDATA%\Programs\Python\Python312`), and the `py` launcher defaults to it
  (`py -0` shows `-V:3.12 *`).
- In **Git Bash**, the bare `python` command happens to resolve to an old **3.8** that sits
  earlier on `PATH`. That is a PATH-order quirk, **not** a missing interpreter.
- **Takeaway:** never rely on the bare `python` on `PATH`. Either activate the venv, or call
  `py -3.12 ...` explicitly. Inside an activated venv, `python` is always 3.12.

---

## Setting the interpreter in an IDE (do this once)

**PyCharm** (the project already uses it — there's a `.idea` folder in HomeEnergyCenter):
1. Open the `BillToBoxAgent` folder as a project.
2. `File → Settings → Project: BillToBoxAgent → Python Interpreter → Add Interpreter →
   Add Local Interpreter → Existing`.
3. Select `C:\Users\AlexVinckier\Dropbox (Personal)\Python\BillToBoxAgent\.venv\Scripts\python.exe`.
4. PyCharm then uses it for **Run** and auto-activates it in its built-in Terminal — no
   manual `activate` needed.

**VS Code:** open the folder → `Ctrl+Shift+P` → *Python: Select Interpreter* → pick the
`.venv` entry.

---

## Installing / updating dependencies

From an activated venv (or prefix with `.\.venv\Scripts\python.exe -m`):

```
pip install -e ".[dev]"          # editable install of the package + dev tools
```

Re-run this whenever `pyproject.toml` dependencies change. Optional: `python -m pip install
--upgrade pip` to silence the pip-version notice (safe now that Dropbox ignores `.venv`).

---

## Running the same checks CI runs

```
ruff check .
ruff format --check .
black --check .
mypy src
pytest                 # or: pytest --cov --cov-report=term-missing
```

All five should pass. (mypy may print a harmless *"unused section(s)"* note for the
google/msal override blocks until those libraries are actually imported — tasks 8/9/14.)

---

## Recreating the venv from scratch

If `.venv` is ever missing or broken, from the project root:

```powershell
# 1. (PowerShell) remove any broken venv
Remove-Item .venv -Recurse -Force -ErrorAction SilentlyContinue

# 2. build a fresh 3.12 venv
py -3.12 -m venv .venv

# 3. CRITICAL on this machine — stop Dropbox from syncing it (see next section)
Set-Content -Path .venv -Stream com.dropbox.ignored -Value 1

# 4. install
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

---

## Important: Dropbox + virtualenvs

This project lives inside `Dropbox (Personal)`. A virtualenv **must not be synced** by
Dropbox — it's large, machine-specific, and already gitignored — and worse, *active syncing
locks files while pip writes them*, which breaks installs with `WinError 32 … being used by
another process`.

**Fix (already applied to this repo's `.venv`):** mark the folder with Dropbox's ignore
attribute (an NTFS alternate data stream), in **PowerShell**:

```powershell
Set-Content -Path .venv -Stream com.dropbox.ignored -Value 1
# verify (prints 1):
Get-Content -Path .venv -Stream com.dropbox.ignored
```

Do this **right after creating** the venv and **before** installing into it. The same applies
to HomeEnergyCenter's `.venv` if it ever misbehaves during an install.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `python --version` shows **3.8** | You're using the bare PATH `python`, not the venv. Activate the venv, or use `py -3.12`. |
| `WinError 32 … being used by another process` during `pip install` | Dropbox is syncing `.venv`. Apply the `com.dropbox.ignored` stream (above), then retry. |
| `ModuleNotFoundError: No module named 'pip._internal'` | Broken pip from an interrupted upgrade. Repair: `.\.venv\Scripts\python.exe -m ensurepip --upgrade`, then reinstall deps. |
| `Activate.ps1 … running scripts is disabled on this system` | PowerShell execution policy. Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` then activate again, or use `cmd` with `activate.bat`. |
| Wrong interpreter in PyCharm/VS Code | Re-point it at `.venv\Scripts\python.exe` (see *Setting the interpreter* above). |
