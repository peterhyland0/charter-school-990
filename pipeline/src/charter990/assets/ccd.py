"""Charter schools from the NCES Common Core of Data school directory."""

import csv
import io
import zipfile

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import CCD_LEA_URL, CCD_SCHOOL_URL, OPERATING_STATUSES, RAW_DIR, STATE_FILTER
from ..util import decode_text, download_cached, name_key, normalize_site, phone10, zip5

COLUMNS = (
    "ncessch", "schid", "leaid", "lea_name", "st_schid", "sch_name", "st",
    "lstreet1", "lcity", "lzip", "phone", "website", "website_host",
    "website_domain", "chartauthn1", "updated_status", "is_operating", "source_file",
    "lea_type", "zip5", "phone10", "name_key", "lea_name_key",
)


def _status(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def _read_csv(url: str) -> tuple[str, list[dict]]:
    path = download_cached(url, RAW_DIR / "ccd" / url.rsplit("/", 1)[1], refresh=True)
    with zipfile.ZipFile(path) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        return name, list(csv.DictReader(io.StringIO(decode_text(zf.read(name)))))


@asset(group_name="pipeline", description="Charter schools (all statuses), every state unless STATE_FILTER is set.")
def ccd_schools(pg: PostgresResource) -> MaterializeResult:
    csv_name, schools = _read_csv(CCD_SCHOOL_URL)
    _, leas = _read_csv(CCD_LEA_URL)
    lea_type = {r["LEAID"]: r["LEA_TYPE"] for r in leas}
    # Websites of districts that aren't charter districts (LEA_TYPE 7) or
    # "other" agencies (8), which sometimes are charter operators. A domain that
    # a charter district or agency also lists belongs to the charter, not to a
    # regular district, so it stays usable.
    domains_by_kind = {True: set(), False: set()}
    for r in leas:
        if d := normalize_site(r.get("WEBSITE"))[1]:
            domains_by_kind[r["LEA_TYPE"] in ("7", "8")].add(d)
    district_domains = sorted(domains_by_kind[False] - domains_by_kind[True])

    rows, operating, with_domain, in_charter_lea = [], 0, 0, 0
    for r in schools:
        if r["CHARTER_TEXT"] != "Yes" or (STATE_FILTER and r["ST"] != STATE_FILTER):
            continue
        status = _status(r["UPDATED_STATUS"])
        host, domain = normalize_site(r["WEBSITE"])
        is_operating = status in OPERATING_STATUSES
        operating += is_operating
        with_domain += is_operating and domain is not None
        in_charter_lea += is_operating and lea_type.get(r["LEAID"]) == "7"
        rows.append((
            r["NCESSCH"], r["SCHID"], r["LEAID"], r["LEA_NAME"], r["ST_SCHID"],
            r["SCH_NAME"], r["ST"], r["LSTREET1"], r["LCITY"], r["LZIP"], r["PHONE"],
            r["WEBSITE"] or None, host, domain, r["CHARTAUTHN1"] or None, status,
            is_operating, csv_name,
            lea_type.get(r["LEAID"]), zip5(r["LZIP"]), phone10(r["PHONE"]),
            name_key(r["SCH_NAME"]), name_key(r["LEA_NAME"]),
        ))

    with pg.connect() as conn:
        # Synced in place rather than reloaded: the live site keeps its matches
        # until school_matches rebuilds them, even if a step in between fails.
        removed = pg.sync_table(conn, "schools", "ncessch", COLUMNS, rows,
                                dependents=("school_matches", "school_status"))
        pg.replace_table(conn, "district_domains", ("domain",), [(d,) for d in district_domains])

    return MaterializeResult(metadata={
        "source_file": csv_name,
        "state_filter": STATE_FILTER or "all states",
        "charter_rows": len(rows),
        "operating": operating,
        "operating_with_domain": with_domain,
        "operating_in_charter_districts": in_charter_lea,
        "states": len({r[6] for r in rows}),
        "district_domains": len(district_domains),
        "schools_removed": removed,
    })
