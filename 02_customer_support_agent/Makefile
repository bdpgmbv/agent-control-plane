# Common tasks. Run "make help" to see them.
.DEFAULT_GOAL := help
PYTHON := ./.venv/bin/python
PORT ?= 8020

help:  ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the virtual environment and install dependencies
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "Done. Paste your OpenAI key into .env, then run: make run"

check-key:  ## Verify the OpenAI key in .env with one tiny call
	$(PYTHON) scripts/check_key.py

run:  ## Start the agent with the web UI on http://localhost:8020
	$(PYTHON) -m uvicorn support_agent.layer7_api.main:app --reload --port $(PORT)

seed:  ## Reset the database to the demo data
	$(PYTHON) scripts/seed_demo.py

test:  ## Run every test (offline, no API key needed)
	$(PYTHON) -m pytest

eval:  ## Run the golden scenarios and print the report
	$(PYTHON) -m support_agent.layer8_evaluation.run_eval

eval-offline:  ## Run the scenarios with no API key at all
	LLM_PROVIDER=offline $(PYTHON) -m support_agent.layer8_evaluation.run_eval

attack:  ## Try to break the agent's safety rules, and show what stopped it
	$(PYTHON) scripts/try_to_break_it.py

clean:  ## Delete the database and evaluation results
	rm -rf data eval_results .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +

.PHONY: help install check-key run seed test eval eval-offline attack clean lint typecheck format

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff check . --fix
