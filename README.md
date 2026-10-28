# Charter school 990 lookup

Links every US charter school (NCES Common Core of Data) to the nonprofit that files
its IRS Form 990, and extracts who leads it, what they are paid, its finances, and
whether a management company runs it. Every fact links to the filing it came from,
and the pipeline refreshes as the IRS posts new e-file batches.

Status: phase 2. Every state. Schools are matched to filers by website, name and ZIP,
name and city, charter district name, phone, and verified schools in the same charter
district; each school is verified, likely or unmatched with a reason. Among the
nonprofits linked to a school, ordered rules decide which one runs it and which is its
management organization, a support entity or the same network in another state. Hand
corrections override the matcher and survive every rerun. Running locally.

## Run it locally

Needs Postgres 17 on localhost, [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
make db        # create the charter990 database and apply migrations
make pipeline  # download sources and run every Dagster asset
make web       # lookup site at http://localhost:3000
```

`make dagster` opens the Dagster UI at http://localhost:3070. `make test` runs the
pipeline's unit tests. `STATE_FILTER=NY make pipeline` runs a single state, which is
much faster.

## Fixing a match

A correction is stored in Postgres and applied last on every run, so it always wins
and survives reruns. Each command rebuilds the matches (about 15 seconds).

```bash
make correct SCHOOL=<NCES id> EIN=<EIN> NOTE="why"       # this nonprofit runs this school
make unlink SCHOOL=<NCES id> EIN=<EIN> NOTE="why"        # this nonprofit has nothing to do with it
make no-nonprofit SCHOOL=<NCES id> NOTE="why"            # this school has no nonprofit
make corrections                                         # list active corrections
make retire ID=<id>                                      # undo one
```

Add `BY="Your Name"` to record who made the fix (default: your login). The NCES id
is on each school page. The school page shows a "Set by hand" badge with
the note, and the `corrections_hold` Dagster check fails if a correction stops applying.

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
