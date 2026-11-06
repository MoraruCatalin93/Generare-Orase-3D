.PHONY: help install run test lint fmt check clean dist

help:
	@echo "make install   — creează .venv și instalează dependențele"
	@echo "make run       — pornește aplicația (http://127.0.0.1:8777)"
	@echo "make test      — rulează testele (offline)"
	@echo "make lint      — ruff check + format --check"
	@echo "make fmt       — ruff format"
	@echo "make check     — verifică instalarea (python -m terragen check)"
	@echo "make clean     — șterge .venv, cache-uri, sites/"
	@echo "make dist      — împachetează ultimul oraș ca site static în dist/"

install:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements.txt pytest ruff

run:
	python3 -m terragen serve

test:
	python3 -m pytest tests -q

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

check:
	python3 -m terragen check

clean:
	rm -rf .venv sites dist .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

dist:
	@site=$$(ls -dt sites/*/ 2>/dev/null | head -1); \
	test -n "$$site" || { echo "niciun oraș generat în sites/"; exit 1; }; \
	python3 -m terragen build --site "$$site" --out dist
