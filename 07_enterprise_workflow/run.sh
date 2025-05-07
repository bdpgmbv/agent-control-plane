#!/usr/bin/env bash
# Start the workflow server.
#
#   ./run.sh              -> http://127.0.0.1:8070
#   PORT=9000 ./run.sh    -> a different port
#
# The API runs a worker on a background thread so you can watch runs advance
# without starting anything else. That is a convenience for looking at it, not
# the deployment shape - in production the workers are separate processes:
#
#   ./run.sh &
#   python scripts/worker.py &
#   python scripts/worker.py &
#
# They share the database and the work divides between them, with no
# coordination beyond the claim.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "No virtual environment found. Run: make install"
  exit 1
fi

if [ ! -f .env ]; then
  echo "No .env file. Copying the example (everything runs offline without a key):"
  cp .env.example .env
fi

PORT="${PORT:-8070}"
echo "Enterprise Workflow  ->  http://127.0.0.1:${PORT}"
exec .venv/bin/uvicorn enterprise_workflow.layer9_api.app:app --host 127.0.0.1 --port "${PORT}"
