# Charter school 990 lookup

Links every US charter school (NCES Common Core of Data) to the nonprofit that files
its IRS Form 990, and extracts who leads it, what they are paid, its finances, and
whether a management company runs it. Every fact links to the filing it came from,
and the pipeline refreshes as the IRS posts new e-file batches.

Status: in progress.

## Layout

- `pipeline/` ingest, extract, match (Python, DuckDB, Dagster)
- `web/` rep-facing lookup UI (Next.js)
- `docs/` one-pager and design notes
