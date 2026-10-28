"""Evidence from outside the IRS data about who holds each charter school.

Four sources, each written to external_links (school, source, holder name, EIN):
- fac: the Federal Audit Clearinghouse. A charter that spends $750k or more
  of federal money in a year ($1M from 2025) files a single audit naming its
  EIN, address and self-reported entity type. An auditee in the school's state
  is linked when its name matches the school's (core similarity
  FAC_SIMILARITY) at the same ZIP or city, or when it sits at the same street
  address and ZIP with a weaker name match (FAC_STREET_SIMILARITY, the trigram
  prefilter). On schools the matcher already links, that auditee is the
  matched operator 87% of the time; most of the rest are a network's filer
  against a campus's, or the reverse.
- tx_tea: the Texas Education Agency's charter holder report: the legal holder
  of each open-enrollment charter, by county-district number.
- la_doe: the Louisiana Department of Education's charter contact list: each
  charter school's nonprofit, joined to NCES by name and ZIP.
- az_ccd: in Arizona each charter holder is its own NCES district, and the
  district name is the holder's legal name with the state's entity ID
  appended. Only used to bring the holder into the candidate pool: the
  district_name signal already compares it.

- website: the operator's legal name or EIN as the school's own website states
  it (footer, privacy, donate pages), for unmatched schools (assets/websites.py).

- fl_ag: the operating nonprofit named in each Florida charter school's audit
  report, filed with the state Auditor General (assets/florida.py); fl_sunbiz:
  Florida trade-name registrations, once their file is downloaded.

California publishes no holder names, so it has no source here.

The rows feed three steps: bmf_candidates adds each holder (by EIN, or by its
exact name in the school's state) to the pool, so its return is fetched;
school_matches turns them into the audit and holder signals; and an unmatched
school whose auditee is a city, county or district with no 990 gets the reason
government_auditee. A source that can't be fetched keeps its previous rows.
"""

import csv
import html
import io
import json
import logging
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import openpyxl
import requests
import xlrd
from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import (
    EXTERNAL_MAX_AGE_DAYS,
    FAC_GENERAL_URL,
    HTTP_HEADERS,
    LA_CHARTER_LIST_URL,
    RAW_DIR,
    TX_CHARTER_HOLDERS_URL,
)
from ..util import GENERIC_WORDS, holder_names, name_key, street_key, zip5
from .ccd import ccd_schools
from .websites import website_rows

log = logging.getLogger(__name__)
EXT_DIR = RAW_DIR / "external"
COLUMNS = ("ncessch", "source", "ein", "holder_name", "holder_keys", "entity_type", "detail")

FAC_SIMILARITY = 0.7
FAC_STREET_SIMILARITY = 0.4
# la_doe: a list row names a school at this name similarity with the same ZIP,
# or at LA_NAME_SIMILARITY on its name alone.
LA_ZIP_SIMILARITY = 0.5
LA_NAME_SIMILARITY = 0.8

# FAC addresses run long in a few rows.
csv.field_size_limit(1 << 24)


def fetch(dest: Path, download: Callable) -> Path:
    """dest, downloaded afresh once it is EXTERNAL_MAX_AGE_DAYS old. download
    writes to an open file; if it fails, the previous copy is kept."""
    if dest.exists() and dest.stat().st_size and time.time() - dest.stat().st_mtime < EXTERNAL_MAX_AGE_DAYS * 86400:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            download(tmp)
        tmp_path.replace(dest)
    except (requests.RequestException, OSError, ValueError) as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        if dest.exists():
            log.warning("using cached %s: %s", dest.name, exc)
            return dest
        raise
    return dest


def _stream(url: str) -> Callable:
    def download(out) -> None:
        # The FAC link redirects to a short-lived signed S3 URL.
        with requests.get(url, headers=HTTP_HEADERS, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            resp.raw.decode_content = True
            shutil.copyfileobj(resp.raw, out, length=1 << 20)
    return download


_INPUT = re.compile(r"<input\b[^>]*>", re.I)


def _tx_export(out) -> None:
    """Post the report viewer's own "export to Excel" request back to it."""
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    page = session.get(TX_CHARTER_HOLDERS_URL, timeout=120)
    page.raise_for_status()
    fields = {}
    for tag in _INPUT.findall(page.text):
        name, value = re.search(r'name="([^"]*)"', tag), re.search(r'value="([^"]*)"', tag)
        if name and 'type="hidden"' in tag:
            fields[html.unescape(name.group(1))] = html.unescape(value.group(1)) if value else ""
    fields["__EVENTTARGET"] = "crViewer"
    fields["__EVENTARGUMENT"] = json.dumps({"text": "RecordToMSExcel", "range": "false", "tb": "crexport"})
    resp = session.post(TX_CHARTER_HOLDERS_URL, data=fields, timeout=300)
    resp.raise_for_status()
    if "excel" not in resp.headers.get("content-type", ""):
        raise ValueError("the TEA report viewer did not return a spreadsheet")
    out.write(resp.content)


def tx_holder_row(row: list) -> tuple[str, str, str] | None:
    """(county-district number, charter name, holder) from one report row, or
    None for headings and blank rows."""
    if len(row) < 3:
        return None
    cd = f"{int(row[1]):06d}" if isinstance(row[1], float) else str(row[1]).strip()
    charter, holder = str(row[0]).strip(), str(row[2]).strip()
    return (cd, charter, holder) if re.fullmatch(r"\d{6}", cd) and charter and holder else None


def tx_holders(data: bytes) -> list[tuple[str, str, str]]:
    """The TEA report's charters and their holders."""
    sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    return [r for i in range(sheet.nrows) if (r := tx_holder_row(sheet.row_values(i)))]


def la_nonprofits(data: bytes) -> list[tuple[str, str, str | None]]:
    """(school name, nonprofit, ZIP) rows of the LDOE list's first sheet."""
    sheet = openpyxl.load_workbook(io.BytesIO(data), read_only=True).worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(h or "").strip().lower() for h in next(rows)]
    school_at, nonprofit_at, address_at = (
        header.index("school name"), header.index("non-profit name"), header.index("school address"))
    out = []
    for r in rows:
        school, nonprofit = (str(r[school_at] or "").strip(), str(r[nonprofit_at] or "").strip())
        if school and nonprofit:
            zips = re.findall(r"\b\d{5}\b", str(r[address_at] or ""))
            out.append((school, nonprofit, zips[-1] if zips else None))
    return out


def fac_auditees(path: Path, states: set[str]):
    """The latest audit of each (EIN, auditee name) in the given states."""
    latest: dict[tuple[str, str], dict] = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            ein = re.sub(r"\D", "", r["auditee_ein"] or "").zfill(9)
            if r["auditee_state"] not in states or not ein.strip("0") or len(ein) != 9:
                continue
            key = (ein, r["auditee_name"])
            if key not in latest or r["audit_year"] > latest[key]["audit_year"]:
                latest[key] = r | {"ein": ein}
    return latest.values()


# ----------------------------------------------------------------------------

FAC_MATCH = """
    SELECT DISTINCT ON (s.ncessch, f.ein) s.ncessch, f.ein, f.name, f.entity_type, f.audit_year,
           round(x.sim::numeric, 2), coalesce(f.street_k = s.street_k, false), f.city, f.zip5
    FROM sch s JOIN fac_a f ON f.state = s.st AND f.core %% s.core
    CROSS JOIN LATERAL (SELECT similarity(s.core, f.core) AS sim) x
    WHERE ((x.sim >= %(fac_sim)s AND (f.zip5 = s.zip5 OR f.city = s.ucity))
           OR (f.street_k = s.street_k AND f.zip5 = s.zip5))
      -- ICAHN 1 is not ICAHN 4, and "BROOKLYN CHARTER SCHOOL" says nothing.
      AND (s.nums = '' OR f.nums = '' OR s.nums = f.nums)
      AND s.core <> coalesce(s.ucity, '')
    ORDER BY s.ncessch, f.ein, x.sim DESC, f.audit_year DESC
"""

LA_MATCH = """
    SELECT DISTINCT ON (s.ncessch) s.ncessch, l.nonprofit, l.school, round(x.sim::numeric, 2)
    FROM schools s JOIN la l ON true
    CROSS JOIN LATERAL (SELECT similarity(s.name_key, l.name_key) AS sim) x
    WHERE s.st = 'LA' AND s.is_operating
      AND ((x.sim >= %(la_zip_sim)s AND s.zip5 = l.zip5) OR x.sim >= %(la_sim)s)
    ORDER BY s.ncessch, x.sim DESC
"""


def _keys(name: str) -> list[str]:
    return [k for p in holder_names(name) if (k := name_key(p))]


def _fac_rows(conn) -> list[tuple]:
    path = fetch(EXT_DIR / "fac_general.csv", _stream(FAC_GENERAL_URL))
    schools = conn.execute(
        "SELECT ncessch, st, name_key, upper(lcity), zip5, lstreet1 FROM schools WHERE is_operating").fetchall()
    states = {s[1] for s in schools}
    conn.execute("""CREATE TEMP TABLE fac_a (ein text, name text, name_key text, street_k text, city text,
                    state text, zip5 text, entity_type text, audit_year int) ON COMMIT DROP""")
    with conn.cursor() as cur, cur.copy("COPY fac_a FROM STDIN") as copy:
        for r in fac_auditees(path, states):
            # "FIT KIDS, INC. DBA CHAMPION SCHOOLS": either name can be the school's.
            for key in set(_keys(r["auditee_name"])):
                copy.write_row((r["ein"], r["auditee_name"], key, street_key(r["auditee_address_line_1"]),
                                (r["auditee_city"] or "").upper(), r["auditee_state"], zip5(r["auditee_zip"]),
                                r["entity_type"], int(r["audit_year"])))
    conn.execute("ALTER TABLE fac_a ADD COLUMN core text, ADD COLUMN nums text")
    conn.execute("UPDATE fac_a SET core = name_core(name_key, %s), nums = name_nums(name_key)", (list(GENERIC_WORDS),))
    conn.execute("CREATE INDEX ON fac_a USING gin (core gin_trgm_ops)")
    conn.execute("ANALYZE fac_a")
    conn.execute("""CREATE TEMP TABLE sch (ncessch text, st text, name_key text, ucity text, zip5 text,
                    street_k text) ON COMMIT DROP""")
    with conn.cursor() as cur, cur.copy("COPY sch FROM STDIN") as copy:
        for ncessch, st, key, city, z, street in schools:
            copy.write_row((ncessch, st, key, city, z, street_key(street)))
    conn.execute("ALTER TABLE sch ADD COLUMN core text, ADD COLUMN nums text")
    conn.execute("UPDATE sch SET core = name_core(name_key, %s), nums = name_nums(name_key)", (list(GENERIC_WORDS),))
    # The trigram prefilter: the weakest name match allowed (same street and ZIP).
    conn.execute(f"SET LOCAL pg_trgm.similarity_threshold = {FAC_STREET_SIMILARITY}")
    rows = []
    for ncessch, ein, name, entity_type, year, sim, same_street, city, z in conn.execute(
        FAC_MATCH, {"fac_sim": FAC_SIMILARITY}
    ).fetchall():
        rows.append((ncessch, "fac", ein, name, _keys(name), entity_type or None, json.dumps({
            "audit_year": year, "similarity": float(sim), "same_street": same_street, "city": city, "zip": z})))
    return rows


def _tx_rows(conn) -> list[tuple]:
    data = fetch(EXT_DIR / "tx_charter_holders.xls", _tx_export).read_bytes()
    by_cd = {cd: (charter, holder) for cd, charter, holder in tx_holders(data)}
    rows = []
    for ncessch, st_schid in conn.execute(
        "SELECT ncessch, st_schid FROM schools WHERE st = 'TX' AND is_operating"
    ).fetchall():
        cd = (st_schid or "").split("-")[1] if (st_schid or "").count("-") >= 2 else None
        if cd in by_cd:
            charter, holder = by_cd[cd]
            rows.append((ncessch, "tx_tea", None, holder, _keys(holder), None,
                         json.dumps({"county_district": cd, "charter": charter})))
    return rows


def _la_rows(conn) -> list[tuple]:
    def download(out) -> None:
        resp = requests.get(LA_CHARTER_LIST_URL, headers=HTTP_HEADERS, timeout=120)
        resp.raise_for_status()
        out.write(resp.content)

    data = fetch(EXT_DIR / "la_charter_list.xlsx", download).read_bytes()
    conn.execute("CREATE TEMP TABLE la (school text, nonprofit text, zip5 text, name_key text) ON COMMIT DROP")
    with conn.cursor() as cur, cur.copy("COPY la FROM STDIN") as copy:
        for school, nonprofit, z in la_nonprofits(data):
            copy.write_row((school, nonprofit, z, name_key(school)))
    return [
        (ncessch, "la_doe", None, nonprofit, _keys(nonprofit), None,
         json.dumps({"list_school": school, "similarity": float(sim)}))
        for ncessch, nonprofit, school, sim in conn.execute(
            LA_MATCH, {"la_zip_sim": LA_ZIP_SIMILARITY, "la_sim": LA_NAME_SIMILARITY}).fetchall()
    ]


def _az_rows(conn) -> list[tuple]:
    rows = []
    for ncessch, lea_name in conn.execute(
        "SELECT ncessch, lea_name FROM schools WHERE st = 'AZ' AND is_operating AND lea_type = '7' AND lea_name IS NOT NULL"
    ).fetchall():
        names = holder_names(lea_name)
        if names:
            rows.append((ncessch, "az_ccd", None, " dba ".join(names), _keys(lea_name), None, "{}"))
    return rows


def _fl_ag_rows(conn) -> list[tuple]:
    # florida.py uses this module's fetch() and _keys(), so it is imported here, not at the top.
    from .florida import fl_ag_rows
    return fl_ag_rows(conn)


def _fl_sunbiz_rows(conn) -> list[tuple]:
    # Needs ficdata.zip downloaded by hand (the state serves it over SFTP only);
    # until then it raises and the source keeps its previous rows.
    from .florida import fl_sunbiz_rows
    return fl_sunbiz_rows(conn)


LOADERS = {"fac": _fac_rows, "tx_tea": _tx_rows, "la_doe": _la_rows, "az_ccd": _az_rows, "website": website_rows,
           "fl_ag": _fl_ag_rows, "fl_sunbiz": _fl_sunbiz_rows}


@asset(group_name="pipeline", deps=[ccd_schools],
       description="Charter holders from federal single audits and state charter records, by school.")
def external_links(pg: PostgresResource) -> MaterializeResult:
    metadata: dict[str, object] = {}
    for source, load in LOADERS.items():
        try:
            with pg.connect() as conn:
                rows = load(conn)
                conn.execute("DELETE FROM external_links WHERE source = %s", (source,))
                with conn.cursor() as cur, cur.copy(f"COPY external_links ({', '.join(COLUMNS)}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
        except Exception as exc:  # one source failing must not stop the others
            log.warning("external source %s failed, keeping its previous rows: %r", source, exc)
            metadata[f"{source}_error"] = repr(exc)[:300]
            continue
        metadata[f"{source}_rows"] = len(rows)
        metadata[f"{source}_schools"] = len({r[0] for r in rows})
        metadata[f"{source}_with_ein"] = len({r[0] for r in rows if r[2]})
    return MaterializeResult(metadata=metadata)
