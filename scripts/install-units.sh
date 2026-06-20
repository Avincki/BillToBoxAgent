#!/usr/bin/env bash
# Install the BillToBox systemd units by symlinking them into
# /etc/systemd/system/ and reloading systemd (WORKPLAN task 22).
#
# Run as root on the Pi:
#   sudo bash scripts/install-units.sh
#
# Units assume the app lives at /opt/billtobox/BillToBoxAgent with a venv at
# .venv/ and config.yaml present (see docs/raspberry-pi-setup.md). They are
# symlinked, so `git pull` keeps them current with no reinstall.
set -euo pipefail

UNIT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/systemd" && pwd)"
SYSTEMD_DIR=/etc/systemd/system
UNITS=(billtobox-agent.service billtobox-agent.timer billtobox-dashboard.service)

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "error: must run as root (use sudo)" >&2
  exit 1
fi

for unit in "${UNITS[@]}"; do
  src="$UNIT_SRC/$unit"
  if [[ ! -f "$src" ]]; then
    echo "error: missing unit file $src" >&2
    exit 1
  fi
  ln -sf "$src" "$SYSTEMD_DIR/$unit"
  echo "linked $SYSTEMD_DIR/$unit -> $src"
done

systemctl daemon-reload
echo
echo "Units installed. Enable them with:"
echo "  sudo systemctl enable --now billtobox-agent.timer        # schedules the worker"
echo "  sudo systemctl enable --now billtobox-dashboard.service  # starts the dashboard"
echo
echo "Verify:"
echo "  systemd-analyze verify ${UNITS[*]}"
echo "  systemctl start billtobox-agent.service   # run one cycle now"
echo "  systemctl list-timers billtobox-agent.timer"
