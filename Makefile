# The targets CI runs (.github/workflows/ci.yml), so a laptop and CI check the same things.
.PHONY: sync lint format-check test examples links check

sync:  ## install the package with its dev extras, exactly as uv.lock pins them
	uv sync --all-extras --locked

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

test:
	uv run pytest -q

examples:  ## run every numbered example; each is pure and offline
	@for f in examples/[0-9]*.py; do \
		echo "== $$f"; uv run python "$$f" || exit 1; \
	done

links:  ## fail on a broken relative link or anchor in any Markdown file
	python3 scripts/check_links.py .

check: lint format-check test examples links
