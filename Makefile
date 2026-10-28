# Local development. Assumes Postgres is running on localhost.
DB ?= charter990
export DATABASE_URL ?= postgresql://localhost/$(DB)
export DAGSTER_HOME := $(CURDIR)/pipeline/.dagster_home

.PHONY: db pipeline dagster web test

db:  ## Create the database and apply migrations (safe to rerun)
	@psql -lqt | cut -d'|' -f1 | grep -qw $(DB) || createdb $(DB)
	@for f in db/migrations/*.sql; do psql -q -v ON_ERROR_STOP=1 -d $(DB) -f $$f; done

pipeline:  ## Run every asset once, end to end
	@mkdir -p $(DAGSTER_HOME) && touch $(DAGSTER_HOME)/dagster.yaml
	cd pipeline && uv run dagster asset materialize -m charter990.definitions --select '*'

dagster:  ## Open the Dagster UI at localhost:3001
	@mkdir -p $(DAGSTER_HOME) && touch $(DAGSTER_HOME)/dagster.yaml
	cd pipeline && uv run dagster dev -m charter990.definitions -p 3001

web:  ## Run the lookup site at localhost:3000
	cd web && npm run dev

test:
	cd pipeline && uv run pytest -q
