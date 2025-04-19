# Document Intelligence Pipeline
#
#   make install   create the virtual environment and install dependencies
#   make run       start the server on http://127.0.0.1:8050
#   make test      run the test suite (offline, free, no key needed)
#   make eval      measure the pipeline against the golden dataset
#   make eval-live measure it with the real model, using the key in .env
#   make attack    try to get a wrong value through the pipeline
#   make check     everything that must pass before this is worth trusting
#   make clean     remove the database and caches

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
PORT   ?= 8050

.PHONY: install run test eval eval-live attack letterhead check clean help lint typecheck format

help:
	@grep -E '^#   ' Makefile | sed 's/^#   //'

install:
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo ""
	@echo "Ready. 'make run' starts the server; 'make test' needs no API key."

run:
	$(PYTHON) -m uvicorn doc_intelligence.layer9_api.app:app --host 127.0.0.1 --port $(PORT)

test:
	$(PYTHON) -m pytest tests/ -q

eval:
	$(PYTHON) scripts/evaluate.py

eval-live:
	$(PYTHON) scripts/evaluate.py --live

attack:
	$(PYTHON) scripts/try_to_break_it.py

letterhead:
	$(PYTHON) scripts/check_letterhead.py

# The three things that decide whether this pipeline can be trusted with money:
# does it pass its tests, does it read the corpus correctly, and can anything
# get a wrong value approved.
check: test eval attack
	@echo ""
	@echo "All three passed."

clean:
	rm -f data/documents.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	@echo "cleaned"

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff check . --fix
