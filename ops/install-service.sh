#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "$0")/.."
if [[ "$PWD" != "$HOME/Desktop/senpilot-agent" ]]; then
  echo 'The service template expects this project at ~/Desktop/senpilot-agent.' >&2
  exit 1
fi
if [[ ! -f .env || ! -f credentials/gmail-token.json || ! -x .venv/bin/python ]]; then
  echo 'Install dependencies and configure mailbox credentials before starting the service.' >&2
  exit 1
fi

install -D -m 644 ops/worker.service "$HOME/.config/systemd/user/senpilot-worker.service"
sudo loginctl enable-linger "$(id -un)"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user daemon-reload
systemctl --user enable --now senpilot-worker.service
systemctl --user is-active senpilot-worker.service
