# Local development. Assumes Postgres is running on localhost.
DB ?= charter990
export DATABASE_URL ?= postgresql://localhost/$(DB)
export DAGSTER_HOME := $(CURDIR)/pipeline/.dagster_home
DAGSTER_PORT ?= 3070

.PHONY: db pipeline dagster web test correct unlink no-nonprofit corrections retire

db:  ## Create the database and apply each migration once, in order
	@psql -lqt | cut -d'|' -f1 | grep -qw $(DB) || createdb $(DB)
	@psql -q -d $(DB) -c "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
	@for f in db/migrations/*.sql; do \
	  n=$$(basename $$f); \
	  if [ -z "$$(psql -qAt -d $(DB) -c "SELECT 1 FROM schema_migrations WHERE name = '$$n'")" ]; then \
	    echo "applying $$n"; \
	    psql -q -v ON_ERROR_STOP=1 -1 -d $(DB) -f $$f -c "INSERT INTO schema_migrations (name) VALUES ('$$n')" || exit 1; \
	  fi; \
	done

pipeline:  ## Run every asset once, end to end
	@mkdir -p $(DAGSTER_HOME) && touch $(DAGSTER_HOME)/dagster.yaml
	cd pipeline && uv run dagster asset materialize -m charter990.definitions --select '*'

dagster:  ## Open the Dagster UI at localhost:$(DAGSTER_PORT)
	@mkdir -p $(DAGSTER_HOME) && touch $(DAGSTER_HOME)/dagster.yaml
	cd pipeline && uv run dagster dev -m charter990.definitions -p $(DAGSTER_PORT)

web:  ## Run the lookup site at localhost:3000
	cd web && npm run dev

test:
	cd pipeline && uv run pytest -q

# Hand corrections. Each one is applied on every run, so it survives reruns.
CORRECT = @mkdir -p $(DAGSTER_HOME) && touch $(DAGSTER_HOME)/dagster.yaml && cd pipeline && uv run python -m charter990.correct

correct:  ## This nonprofit runs this school: make correct SCHOOL=<ncessch> EIN=<ein> NOTE="why"
	$(CORRECT) add --action link --school "$(SCHOOL)" --ein "$(EIN)" --note "$(NOTE)"

unlink:  ## This nonprofit has nothing to do with this school: make unlink SCHOOL=… EIN=… NOTE="why"
	$(CORRECT) add --action unlink --school "$(SCHOOL)" --ein "$(EIN)" --note "$(NOTE)"

no-nonprofit:  ## This school has no nonprofit: make no-nonprofit SCHOOL=… NOTE="why"
	$(CORRECT) add --action none --school "$(SCHOOL)" --note "$(NOTE)"

corrections:  ## List active corrections
	$(CORRECT) list

retire:  ## Undo a correction: make retire ID=<id>
	$(CORRECT) retire --id "$(ID)"
