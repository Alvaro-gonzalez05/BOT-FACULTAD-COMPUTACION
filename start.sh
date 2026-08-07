#!/usr/bin/env bash
# Sets up the virtualenv if needed, then plays.
#   ./start.sh <YOUR_BOT_TOKEN>
set -euo pipefail
cd "$(dirname "$0")"

TOKEN="${1:-${CODECHALLENGE_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  echo "Usage: ./start.sh <YOUR_BOT_TOKEN>   (or set CODECHALLENGE_TOKEN)" >&2
  exit 1
fi
shift || true

if [ ! -d .venv ]; then
  echo "creating .venv..."
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  ./.venv/bin/python -m pip install --quiet -r requirements.txt
fi

exec ./.venv/bin/python run.py play "$TOKEN" "$@"
