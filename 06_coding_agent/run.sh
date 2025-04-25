#!/usr/bin/env bash
# Start the coding agent server.
#
#   ./run.sh              -> http://127.0.0.1:8060
#   PORT=9000 ./run.sh    -> a different port
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "No virtual environment found. Run this first:"
  echo "    make install"
  exit 1
fi

if [ ! -f .env ]; then
  echo "No .env file. Copying the example (the harness runs offline without a key):"
  cp .env.example .env
fi

PORT="${PORT:-8060}"
echo "AI Coding Agent  ->  http://127.0.0.1:${PORT}"
exec .venv/bin/uvicorn coding_agent.layer9_api.app:app --host 127.0.0.1 --port "${PORT}"
