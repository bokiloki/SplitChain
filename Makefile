.PHONY: install test lint simulate node
install:
	python -m pip install -e '.[dev]'
test:
	pytest -q
lint:
	ruff check .
simulate:
	scplit simulate --seed 42 --steps 500
node:
	splitd --host 127.0.0.1 --port 8765

