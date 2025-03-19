# Common tasks. Run "make help" to see them.
.DEFAULT_GOAL := help
PYTHON := ./.venv/bin/python
PORT ?= 8010

help:  ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the virtual environment and install dependencies
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "Done. Paste your OpenAI key into .env, then run: make run"

run:  ## Start the server with the web UI on http://localhost:8010
	$(PYTHON) -m uvicorn rag_assistant.layer7_api.main:app --reload --port $(PORT)

check-key:  ## Verify the OpenAI key in .env with two tiny calls
	$(PYTHON) scripts/check_key.py

reindex:  ## Rebuild all vectors after changing the embedding model
	$(PYTHON) scripts/reindex.py

seed:  ## Load the four sample documents into the knowledge base
	$(PYTHON) scripts/seed_demo.py

test:  ## Run every test (offline, no API key needed)
	$(PYTHON) -m pytest

eval:  ## Run the evaluation suite and print the report
	$(PYTHON) -m rag_assistant.layer8_evaluation.run_eval

tune:  ## Sweep the relevance threshold and show the tradeoff
	$(PYTHON) scripts/tune_threshold.py

infra-up:  ## Start Postgres (pgvector) and Redis for the production path
	docker compose up -d

infra-down:  ## Stop the infrastructure containers
	docker compose down

clean:  ## Delete the database, cache and evaluation results
	rm -rf data eval_results .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +

.PHONY: help install check-key reindex run seed test eval tune infra-up infra-down clean lint typecheck format

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff check . --fix
