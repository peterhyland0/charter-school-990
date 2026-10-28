# Local development. Assumes Postgres is running on localhost.
DB ?= charter990
export DATABASE_URL ?= postgresql://localhost/$(DB)
export DAGSTER_HOME := $(CURDIR)/pipeline/.dagster_home
DAGSTER_PORT ?= 3070

.PHONY: db pipeline dagster web test correct unlink no-nonprofit corrections retire neon-load neon-check neon-roles neon-rematch server-sync

db:  ## Apply each migration once, in order, to $$DATABASE_URL (creates the local database if needed)
	@if [ "$(DATABASE_URL)" = "postgresql://localhost/$(DB)" ]; then \
	  psql -lqt | cut -d'|' -f1 | grep -qw $(DB) || createdb $(DB); \
	fi
	@psql -q "$(DATABASE_URL)" -c "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
	@for f in db/migrations/*.sql; do \
	  n=$$(basename $$f); \
	  if [ -z "$$(psql -qAt "$(DATABASE_URL)" -c "SELECT 1 FROM schema_migrations WHERE name = '$$n'")" ]; then \
	    echo "applying $$n"; \
	    psql -q -v ON_ERROR_STOP=1 -1 "$(DATABASE_URL)" -f $$f -c "INSERT INTO schema_migrations (name) VALUES ('$$n')" || exit 1; \
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

# Live database. .env.neon (never committed) holds NEON_DATABASE_URL, Neon's
# direct owner connection string. You run these targets; nothing here prints it.
NEON_ENV = set -a && . $(CURDIR)/.env.neon && set +a
CHECK_TABLES = schools bmf_orgs irs_index zip_members filings filing_people school_matches school_status match_corrections

neon-load:  ## Copy the local database into Neon, replacing what's there
	@$(NEON_ENV) && pg_dump --no-owner --no-acl --no-comments --clean --if-exists "$(DATABASE_URL)" \
	  | psql -q -v ON_ERROR_STOP=1 "$$NEON_DATABASE_URL"
	@echo "loaded; run make neon-check"

neon-check:  ## Row counts, local versus Neon
	@$(NEON_ENV) && for t in $(CHECK_TABLES); do \
	  printf "%-18s local %8s  neon %8s\n" $$t \
	    "$$(psql -qAt "$(DATABASE_URL)" -c "SELECT count(*) FROM $$t")" \
	    "$$(psql -qAt "$$NEON_DATABASE_URL" -c "SELECT count(*) FROM $$t")"; \
	done

neon-roles:  ## Give the web_reader role read-only access (create the role in the Neon console first)
	@$(NEON_ENV) && psql -q -v ON_ERROR_STOP=1 "$$NEON_DATABASE_URL" -f db/roles/web_reader.sql && echo "web_reader can read"

neon-rematch:  ## Rebuild only the matches in Neon from this Mac
	@mkdir -p $(DAGSTER_HOME) && touch $(DAGSTER_HOME)/dagster.yaml
	@$(NEON_ENV) && cd pipeline && DATABASE_URL="$$NEON_DATABASE_URL" \
	  uv run dagster asset materialize -m charter990.definitions --select school_matches

server-sync:  ## Copy the cached downloads to the server: make server-sync HOST=root@<ip>
	rsync -az data/raw/ "$(HOST):/opt/charter990/data/raw/"
	ssh "$(HOST)" chown -R charter990: /opt/charter990/data

# Hand corrections. Each one is applied on every run, so it survives reruns.
# Values are read from the environment ($$NOTE, not $(NOTE)) so a note with
# quotes or backticks reaches the tool as typed instead of being parsed by the
# shell. BY="Jane Doe" records who made the fix (default: your login).
# NEON=1 sends the correction to the live database (read from .env.neon).
CORRECT = @mkdir -p $(DAGSTER_HOME) && touch $(DAGSTER_HOME)/dagster.yaml && \
	if [ -n "$$NEON" ]; then $(NEON_ENV) && export DATABASE_URL="$$NEON_DATABASE_URL"; fi && \
	cd pipeline && CORRECTED_BY="$$BY" uv run python -m charter990.correct

correct:  ## This nonprofit runs this school: make correct SCHOOL=<ncessch> EIN=<ein> NOTE="why"
	$(CORRECT) add --action link --school "$$SCHOOL" --ein "$$EIN" --note "$$NOTE"

unlink:  ## This nonprofit has nothing to do with this school: make unlink SCHOOL=… EIN=… NOTE="why"
	$(CORRECT) add --action unlink --school "$$SCHOOL" --ein "$$EIN" --note "$$NOTE"

no-nonprofit:  ## This school has no nonprofit: make no-nonprofit SCHOOL=… NOTE="why"
	$(CORRECT) add --action none --school "$$SCHOOL" --note "$$NOTE"

corrections:  ## List active corrections
	$(CORRECT) list

retire:  ## Undo a correction: make retire ID=<id>
	$(CORRECT) retire --id "$$ID"
