install:
	pip install -e ".[dev]" --break-system-packages

test:
	python -m pytest tests/ -v

test-unit:
	python -m pytest tests/unit/ -v

test-integration:
	python -m pytest tests/integration/ -v -s -m integration --timeout=120

test-all:
	python -m pytest tests/ -v --timeout=120

test-quick:
	python -m pytest tests/unit/ -v

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + && rm -rf .pytest_cache .ruff_cache *.egg-info

verify:
	python -c "from src.config import get_settings; s=get_settings(); print(f'{s.app.name} v{s.app.version} - Config OK')"

cli-check:
	python -m src.cli.cli check

cli-config:
	python -m src.cli.cli config

cli-features:
	python -m src.cli.cli features

# API & Database
db-up:
	docker compose -f docker/docker-compose.dev.yml up -d

db-down:
	docker compose -f docker/docker-compose.dev.yml down

db-reset:
	docker compose -f docker/docker-compose.dev.yml down -v
	docker compose -f docker/docker-compose.dev.yml up -d

db-migrate:
	alembic upgrade head

db-migration:
	alembic revision --autogenerate -m "$(msg)"

api-dev:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

api-test:
	GID_API_DATABASE_URL=sqlite+aiosqlite:///./data/test.db python -m pytest tests/unit/test_api_*.py -v

api-smoke:
	python scripts/api_smoke_test.py

# Desktop
desktop:
	python -m desktop

desktop-debug:
	GID_DEBUG=true python -m desktop

# Desktop packaging (PyInstaller)
build-desktop:
	python build/build.py

build-clean:
	python build/build.py --clean

build-all:
	python build/build.py --clean

# Web (Next.js)
web-test:
	cd web && npm test
