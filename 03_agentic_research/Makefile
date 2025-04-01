.DEFAULT_GOAL := help
PYTHON := ./.venv/bin/python
PORT ?= 8030

help:  ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Create the virtual environment and install dependencies
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "Done. Paste your OpenAI key into .env, then run: make run"

run:  ## Start the server with the web UI on http://localhost:8030
	$(PYTHON) -m uvicorn research_agent.layer8_api.main:app --reload --port $(PORT)

test:  ## Run every test (offline, no API key needed)
	$(PYTHON) -m pytest

eval:  ## Run the golden questions and print the report
	$(PYTHON) -m research_agent.layer9_evaluation.run_eval

eval-offline:  ## Run the golden questions with no API key at all
	LLM_PROVIDER=offline $(PYTHON) -m research_agent.layer9_evaluation.run_eval

calibrate:  ## Measure where the retrieval coverage threshold should sit
	$(PYTHON) scripts/calibrate_coverage.py

budget-sweep:  ## Show what shrinking the budget costs you
	$(PYTHON) scripts/budget_sweep.py

clean:  ## Delete evaluation results and caches
	rm -rf data eval_results .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +

.PHONY: help install run test eval eval-offline calibrate budget-sweep clean lint typecheck format

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff check . --fix
