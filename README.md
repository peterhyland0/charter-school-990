# Charter school 990 lookup

Links every US charter school (NCES Common Core of Data) to the nonprofit that files
its IRS Form 990, and extracts who leads it, what they are paid, its finances, and
whether a management company runs it. Every fact links to the filing it came from,
and the pipeline refreshes as the IRS posts new e-file batches.

Status: phase 0. New York only, matched by website domain, running locally.

## Run it locally

Needs Postgres 17 on localhost, [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
make db        # create the charter990 database and apply migrations
make pipeline  # download sources and run every Dagster asset
make web       # lookup site at http://localhost:3000
```

`make dagster` opens the Dagster UI at http://localhost:3070. `make test` runs the
pipeline's unit tests.

## Layout

- `db/migrations/` Postgres schema
- `pipeline/` Dagster assets in Python: schools, candidate nonprofits, IRS index,
  zip locations, returns, parsed facts, matches
- `web/` Next.js lookup site
- `data/` downloaded files and cached returns (not committed)

## Data sources

- NCES Common Core of Data, SY 2025-26 school directory
- IRS Exempt Organizations Business Master File
- IRS Form 990 e-file index and monthly XML zips
- Filing links point to ProPublica Nonprofit Explorer
