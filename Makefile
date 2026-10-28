# Local development. Assumes Postgres is running on localhost.
DB ?= charter990
export DATABASE_URL ?= postgresql://localhost/$(DB)
export DAGSTER_HOME := $(CURDIR)/pipeline/.dagster_home
# Settings come from here, not a pipeline/.env file: the Dagster CLI loads .env
# over the environment, which would send NEON=1 corrections to the local database.
DAGSTER_PORT ?= 3070

.PHONY: db pipeline dagster web test correct unlink no-nonprofit corrections retire neon-load neon-check neon-reader neon-roles neon-rematch server-sync

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
# direct owner connection string, in double quotes: unquoted, the & in Neon's
# URLs ends the assignment and the variable comes out empty. Every target
# refuses to run unless it got a Neon URL, so psql never falls back to the
# local database by accident.
NEON_ENV = set -a && . $(CURDIR)/.env.neon && set +a && \
	case "$$NEON_DATABASE_URL" in *neon.tech*) ;; \
	  *) echo "NEON_DATABASE_URL missing or not a Neon URL: check .env.neon (quote the value)" >&2; exit 1 ;; esac
CHECK_TABLES = schools bmf_orgs irs_index zip_members filings filing_people school_matches school_status match_corrections

# The first load only. Once live, the scheduled pipeline keeps Neon current and
# hand corrections are made there, so a reload would replace them with the
# local copy and briefly break the site. FORCE=1 overrides.
neon-load:  ## First load: copy the local database into an empty Neon database
	@$(NEON_ENV) && \
	  live=$$(psql -qAt "$$NEON_DATABASE_URL" -c "SELECT CASE WHEN to_regclass('public.schools') IS NULL THEN -1 ELSE (SELECT count(*) FROM match_corrections WHERE retired_at IS NULL) END") && \
	  if [ "$$live" != "-1" ] && [ -z "$$FORCE" ]; then \
	    echo "Neon already has data ($$live active corrections). Refusing to replace it; FORCE=1 to override." >&2; exit 1; \
	  fi && \
	  pg_dump --no-owner --no-acl --no-comments --clean --if-exists "$(DATABASE_URL)" \
	    | psql -q -v ON_ERROR_STOP=1 -o /dev/null "$$NEON_DATABASE_URL" && \
	  echo "loaded; run make neon-check"

neon-check:  ## Row counts, local versus Neon
	@$(NEON_ENV) && for t in $(CHECK_TABLES); do \
	  printf "%-18s local %8s  neon %8s\n" $$t \
	    "$$(psql -qAt "$(DATABASE_URL)" -c "SELECT count(*) FROM $$t")" \
	    "$$(psql -qAt "$$NEON_DATABASE_URL" -c "SELECT count(*) FROM $$t")"; \
	done

# Create web_reader in SQL, not in the Neon console or CLI: Neon makes those
# roles members of neon_superuser, which can write every table. Rerunning
# rotates the password. The password goes to psql on stdin (printf is a shell
# builtin), so it never appears in the process list.
neon-reader:  ## Create web_reader or rotate its password; prints its pooled URL for Vercel
	@$(NEON_ENV) && PW=$$(openssl rand -hex 24) && \
	  printf "SELECT 'CREATE ROLE web_reader LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'web_reader') \\gexec\nALTER ROLE web_reader WITH LOGIN PASSWORD '%s';\n" "$$PW" \
	    | psql -q -v ON_ERROR_STOP=1 -o /dev/null "$$NEON_DATABASE_URL" && \
	  if [ -n "$$(psql -qAt "$$NEON_DATABASE_URL" -c "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = 'web_reader' AND r.rolname = 'neon_superuser'")" ]; then \
	    echo "web_reader belongs to neon_superuser (made in the console or CLI), so it can write. Delete it and rerun." >&2; exit 1; \
	  fi && \
	  psql -q -v ON_ERROR_STOP=1 "$$NEON_DATABASE_URL" -f db/roles/web_reader.sql && \
	  echo "DATABASE_URL for Vercel (pooled, read-only):" && \
	  echo "$$NEON_DATABASE_URL" | sed -E "s#//[^@]+@([^.]+)#//web_reader:$$PW@\1-pooler#"

neon-roles:  ## Re-apply web_reader's read-only grants
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
