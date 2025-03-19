#!/usr/bin/env bash
# Start the RAG assistant. Creates the virtual environment on first run.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating the virtual environment..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
  echo "No .env found - copying .env.example."
  echo "Paste your OpenAI key into .env to get real answers."
  cp .env.example .env
fi

PORT="${PORT:-8010}"
echo ""
echo "  Open http://localhost:${PORT}"
echo ""
exec ./.venv/bin/uvicorn rag_assistant.layer7_api.main:app --host 0.0.0.0 --port "${PORT}" --reload
