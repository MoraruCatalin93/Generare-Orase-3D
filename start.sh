#!/usr/bin/env bash
# terragen-web — pornire într-un singur pas (macOS / Linux)
#   ./start.sh            pornește aplicația pe http://127.0.0.1:8777
#   ./start.sh --port 8780
set -euo pipefail
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "✖ Nu găsesc python3. Instalează Python 3.10+ (https://www.python.org/downloads/)."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "▸ creez mediul virtual (.venv)…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f .venv/.deps-installed ] || [ requirements.txt -nt .venv/.deps-installed ]; then
  echo "▸ instalez dependențele…"
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt
  touch .venv/.deps-installed
fi

exec python -m terragen serve "$@"
