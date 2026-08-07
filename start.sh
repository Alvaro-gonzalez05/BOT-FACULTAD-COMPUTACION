#!/usr/bin/env bash
# Sets up the virtualenv if needed, then plays with the control panel open.
#
#   ./start.sh                reads .env, opens the panel in your browser
#   ./start.sh <TOKEN>        use a token directly
#   ./start.sh --no-panel     console only
#
# .env is read automatically, so once your token is in there this is the whole
# command. Never put a token on the command line of a shared machine: it lands
# in the shell history.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8720
PANEL=1
TOKEN=""
REST=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-panel) PANEL=0; shift ;;
    --port) PORT="$2"; shift 2 ;;
    -*) REST+=("$1"); shift ;;
    *) if [ -z "$TOKEN" ]; then TOKEN="$1"; else REST+=("$1"); fi; shift ;;
  esac
done

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
TOKEN="${TOKEN:-${CODECHALLENGE_TOKEN:-${ALVARINHO_TOKEN:-}}}"

if [ -z "$TOKEN" ]; then
  echo "No bot token."
  echo
  echo "  Put it in .env as   CODECHALLENGE_TOKEN=eyJ..."
  echo "  or pass it:         ./start.sh <YOUR_BOT_TOKEN>"
  echo
  echo "Get it from the site: My Bots -> Show token."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "creating .venv..."
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  ./.venv/bin/python -m pip install --quiet -r requirements.txt
fi
PY=./.venv/bin/python
[ -x "$PY" ] || PY=./.venv/Scripts/python.exe

if [ "$PANEL" = "0" ]; then
  exec "$PY" run.py play "$TOKEN" ${REST+"${REST[@]}"}
fi

( sleep 2; (xdg-open "http://127.0.0.1:$PORT" || open "http://127.0.0.1:$PORT") >/dev/null 2>&1 ) &
echo
echo "  control panel: http://127.0.0.1:$PORT"
echo "  press Ctrl+C to stop"
echo
exec "$PY" run.py play "$TOKEN" --quiet-board --dashboard "$PORT" ${REST+"${REST[@]}"}
