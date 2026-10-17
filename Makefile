.PHONY: lint test test-all up down bench install venv

BACKEND_DIR := backend
UV ?= $(shell which uv 2>/dev/null || echo $(HOME)/.local/bin/uv)

# ── Environment & Install (managed via uv) ──────────────────────
venv:
	cd $(BACKEND_DIR) && $(UV) venv

install:
	cd $(BACKEND_DIR) && $(UV) pip install -e ".[dev]"

# ── Lint ─────────────────────────────────────────────────────────
lint: lint-ruff lint-mypy lint-imports

lint-ruff:
	cd $(BACKEND_DIR) && $(UV) run ruff check app/ tests/
	cd $(BACKEND_DIR) && $(UV) run ruff format --check app/ tests/

lint-mypy:
	cd $(BACKEND_DIR) && $(UV) run mypy app/

lint-imports:
	@echo "=== Import boundary check ==="
	@# core/ must not import fastapi or prometheus_client (except redis_store.py may import redis)
	@! grep -rn --include='*.py' \
		-e 'import fastapi' -e 'from fastapi' \
		-e 'import prometheus_client' -e 'from prometheus_client' \
		$(BACKEND_DIR)/app/core/ \
		|| echo "Import boundary: PASS (no FastAPI/Prometheus in core/)"
	@# Verify redis only in redis_store.py within core/
	@REDIS_IMPORTS=$$(grep -rn --include='*.py' \
		-e 'import redis' -e 'from redis' \
		$(BACKEND_DIR)/app/core/ 2>/dev/null \
		| grep -v 'redis_store.py' || true); \
	if [ -n "$$REDIS_IMPORTS" ]; then \
		echo "FAIL: Redis imported outside redis_store.py:"; \
		echo "$$REDIS_IMPORTS"; \
		exit 1; \
	else \
		echo "Import boundary: PASS (redis only in redis_store.py)"; \
	fi

# ── Test ─────────────────────────────────────────────────────────
test:
	cd $(BACKEND_DIR) && $(UV) run pytest -m "unit or contract or api" -v

test-all:
	cd $(BACKEND_DIR) && $(UV) run pytest -v

# ── Docker ───────────────────────────────────────────────────────
up:
	docker compose up -d --build

down:
	docker compose down -v

# ── Benchmarks ───────────────────────────────────────────────────
bench:
	./benchmarks/run.sh
