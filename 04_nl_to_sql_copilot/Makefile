.DEFAULT_GOAL := help
PYTHON := ./.venv/bin/python
PORT ?= 8040

help:  ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the virtual environment, install dependencies, build the warehouse
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env
	$(PYTHON) scripts/seed_database.py
	@echo "Done. Paste your OpenAI key into .env, then run: make run"

seed:  ## Rebuild the analytics warehouse from scratch
	$(PYTHON) scripts/seed_database.py

run:  ## Start the copilot with the web UI on http://localhost:8040
	$(PYTHON) -m uvicorn sql_copilot.layer9_api.main:app --reload --port $(PORT)

test:  ## Run every test (offline, no API key needed)
	$(PYTHON) -m pytest

eval:  ## Run the NL-to-SQL benchmark and the safety suite
	$(PYTHON) -m sql_copilot.layer10_evaluation.run_eval

eval-offline:  ## Run the benchmark with no API key at all
	LLM_PROVIDER=offline $(PYTHON) -m sql_copilot.layer10_evaluation.run_eval

attack:  ## Try to break it, and show which layer stopped each attempt
	$(PYTHON) scripts/try_to_break_it.py

clean:  ## Delete the warehouse and evaluation results
	rm -rf data eval_results .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +

.PHONY: help install seed run test eval eval-offline attack clean lint typecheck format

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff check . --fix
