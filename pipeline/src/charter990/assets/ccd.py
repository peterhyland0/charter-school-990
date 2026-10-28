"""Charter schools from the NCES Common Core of Data school directory."""

import csv
import io
import zipfile

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import CCD_SCHOOL_URL, OPERATING_STATUSES, RAW_DIR, STATE
from ..util import decode_text, download_cached, normalize_site

COLUMNS = (
    "ncessch", "schid", "leaid", "lea_name", "st_schid", "sch_name", "st",
    "lstreet1", "lcity", "lzip", "phone", "website", "website_host",
    "website_domain", "chartauthn1", "updated_status", "is_operating", "source_file",
)


def _status(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


@asset(group_name="phase0", description="Charter schools in the phase 0 state, all statuses.")
def ccd_schools(pg: PostgresResource) -> MaterializeResult:
    zip_path = download_cached(CCD_SCHOOL_URL, RAW_DIR / "ccd" / CCD_SCHOOL_URL.rsplit("/", 1)[1])
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = decode_text(zf.read(csv_name))

    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if r["ST"] != STATE or r["CHARTER_TEXT"] != "Yes":
            continue
        status = _status(r["UPDATED_STATUS"])
        host, domain = normalize_site(r["WEBSITE"])
        rows.append((
            r["NCESSCH"], r["SCHID"], r["LEAID"], r["LEA_NAME"], r["ST_SCHID"],
            r["SCH_NAME"], r["ST"], r["LSTREET1"], r["LCITY"], r["LZIP"], r["PHONE"],
            r["WEBSITE"] or None, host, domain, r["CHARTAUTHN1"] or None, status,
            status in OPERATING_STATUSES, csv_name,
        ))

    with pg.connect() as conn, conn.cursor() as cur:
        # Matches reference schools and are rebuilt downstream on every run.
        cur.execute("TRUNCATE schools CASCADE")
        with cur.copy(f"COPY schools ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)

    operating = [r for r in rows if r[16]]
    return MaterializeResult(metadata={
        "source_file": csv_name,
        "charter_rows": len(rows),
        "operating": len(operating),
        "operating_with_domain": sum(1 for r in operating if r[13]),
    })
