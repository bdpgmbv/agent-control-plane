#!/usr/bin/env bash
# Start the gateway.
#
#   ./run.sh              -> http://127.0.0.1:8080
#   PORT=9000 ./run.sh    -> a different port
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "No virtual environment found. Run: make install"
  exit 1
fi

if [ ! -f .env ]; then
  echo "No .env file. Copying the example (the offline models work without a key):"
  cp .env.example .env
fi

PORT="${PORT:-8080}"
echo "LLM Gateway  ->  http://127.0.0.1:${PORT}"
exec .venv/bin/uvicorn llm_gateway.layer9_api.app:app --host 127.0.0.1 --port "${PORT}"
