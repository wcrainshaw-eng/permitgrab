.PHONY: install test smoke lint format check deploy

install:
	pip install -r requirements.txt -r requirements-dev.txt

smoke:
	pytest tests/test_smoke.py tests/test_imports.py -q

test:
	pytest tests/ -q

test-full:
	pytest tests/ -v --tb=short

lint:
	ruff check .

format:
	ruff format .

check: lint smoke
	@echo "All pre-push checks passed."

# Only call this if `make check` is green
deploy:
	@make check
	git push origin main
