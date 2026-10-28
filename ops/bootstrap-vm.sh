#!/usr/bin/env bash
set -euo pipefail

# Run as the deployment user after copying the project onto an Ubuntu 24.04 VM.
cd -- "$(dirname -- "$0")/.."
if [[ "$(id -u)" == 0 ]]; then
  echo 'Run this as the deployment user; sudo is used only for system packages.' >&2
  exit 1
fi

sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3.12-venv rsync
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .

mkdir -p var/browsers credentials
chmod 700 credentials
export PLAYWRIGHT_BROWSERS_PATH="$PWD/var/browsers"
.venv/bin/python -m playwright install --with-deps chromium

if [[ -f .env ]]; then
  chmod 600 .env
fi
for credential in credentials/*.json; do
  if [[ -f "$credential" ]]; then
    chmod 600 "$credential"
  fi
done

echo 'Installation complete. Verify Cloud access and retrieval before starting the worker.'
