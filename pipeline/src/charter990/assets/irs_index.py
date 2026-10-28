"""The IRS yearly e-file index, filtered to candidate nonprofits."""

import csv

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import INDEX_YEAR, IRS_XML_BASE, RAW_DIR
from ..util import download_cached
from .bmf import bmf_candidates

RETURN_TYPES = {"990", "990EZ"}
COLUMNS = (
    "object_id", "ein", "tax_period", "return_type", "taxpayer_name",
    "xml_batch_id", "return_id", "dln", "index_year",
)


@asset(group_name="phase0", deps=[bmf_candidates], description="Index rows for candidate EINs (990 and 990-EZ only).")
def irs_index(pg: PostgresResource) -> MaterializeResult:
    url = f"{IRS_XML_BASE}/{INDEX_YEAR}/index_{INDEX_YEAR}.csv"
    path = download_cached(url, RAW_DIR / "irs" / f"index_{INDEX_YEAR}.csv")

    with pg.connect() as conn:
        pool = {e for (e,) in conn.execute("SELECT ein FROM bmf_orgs").fetchall()}

    rows, total = [], 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        for r in csv.DictReader(f):
            total += 1
            ein = r["EIN"].strip().zfill(9)  # the index drops leading zeros
            if r["RETURN_TYPE"] not in RETURN_TYPES or ein not in pool:
                continue
            rows.append((
                r["OBJECT_ID"].strip(), ein, r["TAX_PERIOD"].strip(), r["RETURN_TYPE"],
                r["TAXPAYER_NAME"], r["XML_BATCH_ID"].strip(), r["RETURN_ID"], r["DLN"],
                INDEX_YEAR,
            ))

    with pg.connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE irs_index")
        with cur.copy(f"COPY irs_index ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)

    return MaterializeResult(metadata={
        "index_rows_total": total,
        "rows_kept": len(rows),
        "pool_eins_with_a_return": len({r[1] for r in rows}),
        "pool_size": len(pool),
    })
