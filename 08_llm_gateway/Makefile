# LLM Gateway and Evaluation Platform
#
#   make install    create the virtual environment and install dependencies
#   make run        start the gateway on http://127.0.0.1:8080
#   make test       run the test suite (offline, free, no key needed)
#   make lint       ruff
#   make typecheck  mypy
#   make eval       measure it offline
#   make eval-live  measure it with real models
#   make threshold  measure the semantic cache threshold, do not guess it
#   make check      everything that must pass before this is worth trusting
#   make clean      remove the database and caches

PYTHON_VERSION := python3.12
PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
PORT   ?= 8080

.PHONY: install run test lint typecheck format eval eval-live threshold check clean help

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
	@echo "Ready. 'make run' starts the gateway; 'make test' needs no API key."

run:
	$(PYTHON) -m uvicorn llm_gateway.layer9_api.app:app --host 127.0.0.1 --port $(PORT)

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

threshold:
	$(PYTHON) scripts/tune_threshold.py

check: test lint typecheck eval
	@echo ""
	@echo "All of it passed."

clean:
	rm -rf data .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo "cleaned"
