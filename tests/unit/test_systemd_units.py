"""Regression check on the systemd units (task 22).

``systemd-analyze verify`` runs on the Pi (task 25); this keeps the unit files
from drifting from the locked field conventions (CONVENTIONS.md §10 / §13-B) in
CI on any platform.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_UNITS = _ROOT / "deploy" / "systemd"

_HARDENING = (
    "NoNewPrivileges=true",
    "PrivateTmp=true",
    "ProtectSystem=strict",
    "ProtectHome=true",
    "ReadWritePaths=/opt/billtobox/BillToBoxAgent",
)


def _read(name: str) -> str:
    return (_UNITS / name).read_text(encoding="utf-8")


def test_worker_service_is_oneshot_and_timer_driven() -> None:
    text = _read("billtobox-agent.service")
    assert "Type=oneshot" in text
    assert "User=billtobox" in text
    assert "Group=billtobox" in text
    assert "WorkingDirectory=/opt/billtobox/BillToBoxAgent" in text
    assert "Environment=BTB_CONFIG=/opt/billtobox/BillToBoxAgent/config.yaml" in text
    assert (
        "ExecStart=/opt/billtobox/BillToBoxAgent/.venv/bin/python -m billtobox_agent.worker" in text
    )
    # Started by the timer, not enabled directly — no [Install] section header.
    assert not any(line.strip() == "[Install]" for line in text.splitlines())
    for directive in _HARDENING:
        assert directive in text


def test_timer_schedule_and_install() -> None:
    text = _read("billtobox-agent.timer")
    assert "OnCalendar=*-*-* 07:00:00" in text
    assert "Persistent=true" in text
    assert "Unit=billtobox-agent.service" in text
    assert "WantedBy=timers.target" in text


def test_dashboard_service_is_long_lived() -> None:
    text = _read("billtobox-dashboard.service")
    assert "Type=simple" in text
    assert "User=billtobox" in text
    assert "WorkingDirectory=/opt/billtobox/BillToBoxAgent" in text
    assert "Environment=BTB_CONFIG=/opt/billtobox/BillToBoxAgent/config.yaml" in text
    assert "ExecStart=/opt/billtobox/BillToBoxAgent/.venv/bin/python main.py" in text
    assert "Restart=on-failure" in text
    assert "RestartSec=5" in text
    assert "TimeoutStopSec=15" in text
    assert "WantedBy=multi-user.target" in text
    for directive in _HARDENING:
        assert directive in text


def test_install_script_links_all_units() -> None:
    text = (_ROOT / "scripts" / "install-units.sh").read_text(encoding="utf-8")
    for unit in (
        "billtobox-agent.service",
        "billtobox-agent.timer",
        "billtobox-dashboard.service",
    ):
        assert unit in text
    assert "ln -sf" in text
    assert "daemon-reload" in text
