# Everything runs in Docker. On Windows without make, use scrape.ps1 (same commands).

COMPOSE   ?= docker compose
PY_IMAGE  := python:3.13-slim
PIP_TOOLS := pip-tools==7.6.2
LOCK      := --allow-unsafe --generate-hashes --strip-extras --quiet

.PHONY: help build scrape test lint format lock

help:  ## List commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-8s %s\n", $$1, $$2}'

build:  ## Build the scraper and test images
	$(COMPOSE) build

scrape:  ## Scrape a site: make scrape URL=https://example.co.za ARGS="--max-pages 100"
	@test -n "$(URL)" || (echo 'usage: make scrape URL=https://example.co.za [ARGS="..."]' && exit 1)
	$(COMPOSE) run --rm scrape $(URL) $(ARGS)

test:  ## Ruff + pytest (unit tests and the end-to-end browser crawl)
	$(COMPOSE) run --rm test

lint:  ## Ruff only
	$(COMPOSE) run --rm test sh -c "ruff check . && ruff format --check ."

format:  ## Ruff autofix and format
	$(COMPOSE) run --rm test sh -c "ruff check --fix . && ruff format ."

lock:  ## Re-lock requirements*.txt after editing a .in file
	docker run --rm -v "$(CURDIR):/req" -w /req $(PY_IMAGE) sh -c '\
		pip install --quiet --root-user-action=ignore $(PIP_TOOLS) && \
		pip-compile $(LOCK) --output-file=requirements.txt requirements.in && \
		pip-compile $(LOCK) --output-file=requirements-dev.txt requirements-dev.in'
