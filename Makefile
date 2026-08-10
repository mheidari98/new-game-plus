# Canonical commands. AGENTS.md points here so there is one place to change.
.PHONY: setup test check crawl site clean

setup:
	pip install -r crawler/requirements-dev.txt
	cd site && npm ci

check:                      ## invariants only, ~1s
	python scripts/check_invariants.py

test: check                 ## everything, no network required
	python -m pytest -q
	cd site && npx vitest run

crawl:                      ## live crawl against the real store
	python crawler/main.py --once -v

site:                       ## build the static site
	cd site && npm run build

clean:
	rm -rf site/dist site/node_modules .pytest_cache data/cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
