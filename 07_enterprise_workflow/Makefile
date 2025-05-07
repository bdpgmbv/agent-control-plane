# Enterprise Multi-Agent Workflow
#
#   make install    create the virtual environment and install dependencies
#   make run        start the server on http://127.0.0.1:8070
#   make worker     start one more worker process against the same database
#   make test       run the test suite (offline, free, no key needed)
#   make lint       ruff
#   make typecheck  mypy
#   make eval       run every scenario offline
#   make eval-live  run them with the real model
#   make crash      kill real worker processes and check nothing happens twice
#   make check      everything that must pass before this is worth trusting
#   make clean      remove the database and caches

PYTHON_VERSION := python3.12
PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
PORT   ?= 8070

.PHONY: install run worker test lint typecheck format eval eval-live crash check clean help

help:
	@grep -E '^#   ' Makefile | sed 's/^#   //'

install:
	@command -v $(PYTHON_VERSION) >/dev/null 2>&1 || { \
	  echo "$(PYTHON_VERSION) is not installed."; \
	  echo "pydantic has no prebuilt wheel for 3.14, so a newer python tries to"; \
	  echo "compile Rust and fails."; exit 1; }
	$(PYTHON_VERSION) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo ""
	@echo "Ready. 'make run' starts the server; 'make test' needs no API key."

run:
	$(PYTHON) -m uvicorn enterprise_workflow.layer9_api.app:app --host 127.0.0.1 --port $(PORT)

# A second, third, fourth worker. They share the database and the work divides
# between them. Nothing needs to be told that another one exists.
worker:
	$(PYTHON) scripts/worker.py

test:
	$(PYTHON) -m pytest tests/ -q

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff check . --fix

eval:
	$(PYTHON) scripts/evaluate.py

eval-live:
	$(PYTHON) scripts/evaluate.py --live

crash:
	$(PYTHON) scripts/crash_test.py

# The four things that decide whether this engine can be trusted with a
# workflow that touches money.
check: test lint typecheck eval crash
	@echo ""
	@echo "All of it passed."

clean:
	rm -rf data .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo "cleaned"
