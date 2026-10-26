UV ?= uv
GO ?= go
NPM ?= npm
COMPOSE ?= docker compose
TERN ?= tern
PYTEST_ARGS ?= -q

.PHONY: help setup python-deps go-deps frontend-deps test test-python test-go test-observability \
	build build-frontend up down restart logs ps config postgres migrate-up \
	migrate-down migrate-status migrate-new lint docker-build backup

help: ## Show available targets
	@echo Available targets:
	@echo   setup             Install Python, Go, and frontend dependencies
	@echo   test              Run Python, Go gateway, and observability tests
	@echo   test-python       Run Python tests
	@echo   test-go           Run Go gateway tests
	@echo   test-observability Run Go observability tests
	@echo   build             Build the React frontend
	@echo   up                Build and start Docker Compose services
	@echo   down              Stop services without deleting volumes
	@echo   restart           Restart Docker Compose services
	@echo   logs              Follow Docker Compose service logs
	@echo   ps                Show Docker Compose service status
	@echo   config            Validate Docker Compose configuration
	@echo   postgres          Start the local PostgreSQL service and wait until ready
	@echo   migrate-up        Apply pending Tern migrations (users, workspaces, memberships)
	@echo   migrate-down      Roll back one PostgreSQL migration using Tern
	@echo   migrate-status    Show PostgreSQL migration status using Tern
	@echo   migrate-new       Create a migration; set MIGRATION_NAME=...
	@echo   lint              Run Python and Go static checks
	@echo   docker-build      Build all application Docker images
	@echo   backup            Back up PostgreSQL, metadata, uploads, Qdrant, and telemetry

setup: python-deps go-deps frontend-deps ## Install Python, Go, and frontend dependencies

python-deps: ## Sync locked Python dependencies
	$(UV) sync --frozen --group dev

go-deps: ## Download Go gateway dependencies
	$(GO) -C gateway mod download

frontend-deps: ## Install frontend dependencies from the lockfile
	cd frontend-react && $(NPM) ci

test: test-python test-go test-observability ## Run Python, gateway, and observability tests

test-python: ## Run Python tests
	$(UV) run --frozen --group dev pytest tests/ $(PYTEST_ARGS)

test-go: ## Run Go gateway tests
	$(GO) -C gateway test ./...

test-observability: ## Run observability service tests
	$(GO) -C observability-service test ./...

lint: ## Run Python and Go static checks
	$(UV) run --frozen --group dev ruff check storage.py scripts
	$(UV) run --frozen --group dev ruff format --check storage.py scripts
	$(GO) -C gateway vet ./...
	$(GO) -C observability-service vet ./...

build: build-frontend ## Build the React frontend

build-frontend: ## Type-check and build the React frontend
	cd frontend-react && $(NPM) run build

docker-build: ## Build all application Docker images
	docker build -f Dockerfile.backend -t omniretrive-ai-backend .
	docker build -f Dockerfile.gateway -t omniretrive-gateway .
	docker build -f Dockerfile.frontend -t omniretrive-frontend .
	docker build -f Dockerfile.observability -t omniretrive-observability .

up: ## Build and start all Docker Compose services
	$(COMPOSE) up --build

down: ## Stop Docker Compose services without deleting volumes
	$(COMPOSE) down

restart: ## Restart Docker Compose services
	$(COMPOSE) restart

logs: ## Follow Docker Compose service logs
	$(COMPOSE) logs -f

ps: ## Show Docker Compose service status
	$(COMPOSE) ps

config: ## Validate Docker Compose configuration
	$(COMPOSE) config --quiet

postgres: ## Start PostgreSQL and wait until healthy
	$(COMPOSE) up -d --wait postgres

migrate-up: postgres ## Apply all pending PostgreSQL migrations using Tern
	$(UV) run --frozen --group dev python scripts/tern_migrate.py up --tern "$(TERN)"

migrate-down: postgres ## Roll back one PostgreSQL migration; require MIGRATE_DOWN_CONFIRM=yes
	$(UV) run --frozen --group dev python scripts/tern_migrate.py down --confirm "$(MIGRATE_DOWN_CONFIRM)" --tern "$(TERN)"

migrate-status: postgres ## Show PostgreSQL migration status using Tern
	$(UV) run --frozen --group dev python scripts/tern_migrate.py status --tern "$(TERN)"

migrate-new: ## Create a Tern migration; set MIGRATION_NAME=...
	$(UV) run --frozen --group dev python scripts/tern_migrate.py new --name "$(MIGRATION_NAME)" --tern "$(TERN)"

backup: ## Back up all persistent local data to backups/
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup.ps1
