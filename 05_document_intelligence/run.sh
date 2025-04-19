#!/usr/bin/env bash
# Start the document intelligence server.
#
#   ./run.sh              -> http://127.0.0.1:8050
#   PORT=9000 ./run.sh    -> a different port
#
# Runs from this script's own directory, so it works from anywhere.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "No virtual environment found. Run this first:"
  echo "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [ ! -f .env ]; then
  echo "No .env file. Copying the example (the pipeline runs offline without a key):"
  cp .env.example .env
fi

PORT="${PORT:-8050}"
echo "Document Intelligence Pipeline  ->  http://127.0.0.1:${PORT}"
exec .venv/bin/uvicorn doc_intelligence.layer9_api.app:app --host 127.0.0.1 --port "${PORT}"
