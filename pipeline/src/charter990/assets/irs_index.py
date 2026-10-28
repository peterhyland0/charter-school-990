"""The IRS yearly e-file indexes, filtered to candidate nonprofits."""

import csv

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import INDEX_YEARS, IRS_XML_BASE, RAW_DIR
from ..util import download_cached
from .bmf import bmf_candidates

RETURN_TYPES = {"990", "990EZ"}
COLUMNS = (
    "object_id", "ein", "tax_period", "return_type", "taxpayer_name",
    "xml_batch_id", "return_id", "dln", "index_year",
)


@asset(group_name="phase0", deps=[bmf_candidates], description="Index rows for candidate EINs (990 and 990-EZ only), across index years.")
def irs_index(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        pool = {e for (e,) in conn.execute("SELECT ein FROM bmf_orgs").fetchall()}

    rows, seen, totals = [], set(), {}
    for year in INDEX_YEARS:
        url = f"{IRS_XML_BASE}/{year}/index_{year}.csv"
        # The IRS rewrites each year's index in place as new batches post.
        path = download_cached(url, RAW_DIR / "irs" / f"index_{year}.csv", refresh=True)
        totals[year] = 0
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            for r in csv.DictReader(f):
                totals[year] += 1
                ein = r["EIN"].strip().zfill(9)  # the index drops leading zeros
                object_id = r["OBJECT_ID"].strip()
                if r["RETURN_TYPE"] not in RETURN_TYPES or ein not in pool or object_id in seen:
                    continue
                seen.add(object_id)
                rows.append((
                    object_id, ein, r["TAX_PERIOD"].strip(), r["RETURN_TYPE"],
                    r["TAXPAYER_NAME"], r["XML_BATCH_ID"].strip(), r["RETURN_ID"], r["DLN"],
                    year,
                ))

    with pg.connect() as conn:
        pg.replace_table(conn, "irs_index", COLUMNS, rows)

    metadata = {f"index_rows_{year}": n for year, n in totals.items()}
    metadata.update({
        "rows_kept": len(rows),
        "pool_eins_with_a_return": len({r[1] for r in rows}),
        "pool_size": len(pool),
    })
    return MaterializeResult(metadata=metadata)
