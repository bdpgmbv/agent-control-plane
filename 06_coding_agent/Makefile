# AI Coding Agent
#
#   make install    create the virtual environment and install dependencies
#   make run        start the server on http://127.0.0.1:8060
#   make test       run the test suite (offline, free, no key needed)
#   make eval       run the benchmark offline - measures the HARNESS
#   make eval-live  run the benchmark with the real model - measures the AGENT
#   make repeat     run the live benchmark 3 times and show the spread
#   make attack     try to get a dishonest patch past the verifier
#   make check      everything that must pass before this is worth trusting
#   make clean      remove workspaces and caches

# pydantic has no wheel for 3.14 yet, and the whole series is built on 3.12.
PYTHON_VERSION := python3.12
PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
PORT   ?= 8060

.PHONY: install run test eval eval-live repeat attack check clean help lint typecheck format

help:
	@grep -E '^#   ' Makefile | sed 's/^#   //'

install:
	@command -v $(PYTHON_VERSION) >/dev/null 2>&1 || { \
	  echo "$(PYTHON_VERSION) is not installed."; \
	  echo "This project needs it: pydantic has no prebuilt wheel for 3.14,"; \
	  echo "so a newer python tries to compile Rust and fails."; exit 1; }
	$(PYTHON_VERSION) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo ""
	@echo "Ready. 'make run' starts the server; 'make test' needs no API key."

run:
	$(PYTHON) -m uvicorn coding_agent.layer9_api.app:app --host 127.0.0.1 --port $(PORT)

test:
	$(PYTHON) -m pytest tests/ -q

eval:
	$(PYTHON) scripts/evaluate.py

eval-live:
	$(PYTHON) scripts/evaluate.py --live

repeat:
	$(PYTHON) scripts/repeat_live.py 3

attack:
	$(PYTHON) scripts/try_to_break_it.py

# The three things that decide whether this harness can be trusted to apply a
# patch: does it pass its own tests, does it handle a correct patch end to end,
# and can a dishonest one get through.
check: test eval attack
	@echo ""
	@echo "All three passed."

clean:
	rm -rf workspaces data
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	@echo "cleaned"

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff check . --fix
