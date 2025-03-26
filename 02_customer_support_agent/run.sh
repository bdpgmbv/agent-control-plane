#!/usr/bin/env bash
# Start the customer support agent.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating the virtual environment..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
  echo "No .env found - copying .env.example. Paste your OpenAI key into it."
  cp .env.example .env
fi

PORT="${PORT:-8020}"
echo ""
echo "  Open http://localhost:${PORT}"
echo ""
exec ./.venv/bin/uvicorn support_agent.layer7_api.main:app --host 0.0.0.0 --port "${PORT}" --reload
