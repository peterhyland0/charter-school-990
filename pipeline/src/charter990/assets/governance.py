"""State records saying who governs a charter school, for schools with no
nonprofit of their own.

Many charter schools never file a Form 990 because no nonprofit runs them: a
school district or a state body does. These sources let an unmatched school
carry that as its reason instead of "no match found". Each writes rows to
school_governance (school, source, governance, detail); governance is
district_run, state_agency, or independent (its own governing body, which is
where a 990 is expected). Independent rows are kept too: they are the
precision check on the other label.

- ca_cde: the California Department of Education's public schools file. Each
  charter's FundingType is "Locally funded" or "Directly funded". Under
  Education Code 47651(a) a charter elects whether its state aid is paid to
  it directly or to the district or county office that chartered it; a
  locally funded charter's money flows through its authorizer. The CDE calls
  these "dependent" and "independent" charters colloquially but discourages
  the terms, which are not in statute (Ed-Data, a CDE partnership:
  https://ed-data.org/article/Charter-Schools-in-California). Funding route
  is not governance: Education Code 47604(a) lets any charter "operate as, or
  be operated by, a nonprofit public benefit corporation". So locally funded
  is read as district_run and directly funded as independent, as a proxy.
  Measured before this module: 262 locally funded operating charters were
  unmatched and 25 matched to a nonprofit.
- wi_dpi: the Wisconsin Department of Public Instruction's list of all charter
  schools. Agency type "Independent Charter School" means a charter authorized
  under Wis. Stat. 118.40(2r) or (2x) (a city, university, county executive
  or the Office of Educational Opportunity), which cannot be an instrumentality
  of any district: independent. "Public School" means a district-authorized
  charter (118.40(2m)). Its school board decides whether the school is an
  instrumentality of the district; if it is, the board employs all its staff,
  and if not, the board may employ none (118.40(7)(a)). DPI publishes the
  counts but not which school is which: 168 instrumentalities and 21
  non-instrumentalities in 2025-26 (DPI Quick Facts). District-authorized
  charters are therefore district_run with instrumentality null (presumed),
  except in Milwaukee, where Milwaukee Public Schools lists its instrumentality
  and non-instrumentality charters by name; a non-instrumentality there is
  independent.
- hi_hrs: Hawaii law, not a download. Every Hawaii charter school is
  authorized by the State Public Charter School Commission, and HRS 302D-25(e)
  treats charter schools "as public schools and entities of the State"; the
  governing board is the employer of its staff under the state public-employee
  chapters 76, 78 and 89 (HRS 302D-1, "governing board"). Every operating
  Hawaii charter is state_agency. The exception the statute allows, a
  conversion charter managed by a nonprofit whose board becomes the governing
  board (HRS 302D-12(d)), is not identifiable from a published list, so it is
  not modelled; some boards also have an affiliated foundation that files.

Arkansas is not covered: the Division of Elementary and Secondary Education
publishes its district-conversion and open-enrollment charters only in PDF
reports. A source that can't be fetched keeps its previous rows.
"""

import csv
import html
import io
import json
import logging
import re
from collections.abc import Callable

import openpyxl
import requests
from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import HTTP_HEADERS, RAW_DIR
from ..util import decode_text, meaningful_words
from .ccd import ccd_schools
from .external import fetch

log = logging.getLogger(__name__)
GOV_DIR = RAW_DIR / "governance"
COLUMNS = ("ncessch", "source", "governance", "detail")

# CDE public schools file (tab-separated, every school in the state with its
# charter number and funding type). The host redirects to sd.cde.ca.gov and
# sits behind a bot screen that answers with an HTML page instead.
CA_SCHOOLS_URL = "https://www.cde.ca.gov/schooldirectory/report?rid=dl1&tp=txt"
# Education Code 47651: a charter's state aid is paid to it ("Directly funded")
# or to its chartering agency ("Locally funded").
CA_FUNDING = {"Locally funded": "district_run", "Directly funded": "independent"}

# Wisconsin DPI "2026-27 List of All Charter Schools", linked from
# https://dpi.wi.gov/parental-education-options/charter-schools/current.
# DPI posts a new media ID each school year.
WI_CHARTERS_URL = "https://dpi.wi.gov/media/55752/download?inline"
# Agency type -> governance. Independent charter schools are 118.40(2r)/(2x)
# charters; "Public School" is a district-authorized (2m) charter.
WI_AGENCY = {"Independent Charter School": "independent", "Public School": "district_run"}
# Milwaukee Public Schools' charter page: lists its charters under
# "Instrumentality Charter Schools" (staffed by MPS employees) and
# "Non-Instrumentality Charter Schools" (staffed by the school's own employer).
MPS_CHARTERS_URL = "https://www.milwaukeepublicschools.org/about/directory/schools/contracted-schools"
MPS_LEA_CODE = "3619"


def _download(url: str, check: Callable[[bytes], bool] | None = None) -> Callable:
    def download(out) -> None:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=300)
        resp.raise_for_status()
        if check and not check(resp.content):
            # A bot screen or error page served with status 200.
            raise ValueError(f"{url} did not return the expected file")
        out.write(resp.content)
    return download


def _is_cde_file(data: bytes) -> bool:
    return data.startswith(b"CDSCode\t")


def _is_xlsx(data: bytes) -> bool:
    return data.startswith(b"PK")


# ----------------------------------------------------------------------------
# Parsers (pure, tested in tests/test_governance.py)


def ca_charters(text: str) -> dict[str, dict]:
    """Charter rows of the CDE file by 7-digit school code (the CDS code's last
    7 digits), with a governance for the locally and directly funded ones."""
    out = {}
    for r in csv.DictReader(io.StringIO(text), delimiter="\t"):
        cds = (r.get("CDSCode") or "").strip()
        governance = CA_FUNDING.get((r.get("FundingType") or "").strip())
        if r.get("Charter") != "Y" or governance is None or len(cds) != 14 or cds.endswith("0000000"):
            continue
        row = {
            "governance": governance,
            "cds_code": cds,
            "nces_id": (r.get("NCESDist") or "") + (r.get("NCESSchool") or ""),
            "status": r.get("StatusType"),
            "funding_type": r["FundingType"].strip(),
            "charter_number": r.get("CharterNum"),
            "authorizer": r.get("District"),
            "authorizer_type": r.get("DOCType"),
        }
        code = cds[-7:]
        # A school code reused after a closure: keep the active row.
        if code not in out or row["status"] == "Active":
            out[code] = row
    return out


def wi_charters(data: bytes) -> dict[str, dict]:
    """DPI list rows by NCES state school ID ("WI-<LEA code>-<school code>")."""
    sheet = openpyxl.load_workbook(io.BytesIO(data), read_only=True).worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    for header in rows:
        names = [str(h or "").strip() for h in header]
        if "Charter School Name" in names:
            break
    else:
        raise ValueError("no header row in the DPI charter list")
    col = {n: i for i, n in enumerate(names)}
    agency_col = next(i for n, i in col.items() if n.startswith("Agency"))
    out = {}
    for r in rows:
        lea, school, agency = r[col["LEA Code"]], r[col["School Code"]], str(r[agency_col] or "").strip()
        if lea is None or school is None or agency not in WI_AGENCY:
            continue
        lea, school = f"{int(lea):04d}", f"{int(school):04d}"
        out[f"WI-{lea}-{school}"] = {
            "governance": WI_AGENCY[agency],
            "lea_code": lea,
            "authorizer": str(r[0] or "").strip(),
            "school": str(r[col["Charter School Name"]] or "").strip(),
            "agency_type": agency,
        }
    return out


_H3 = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.I | re.S)
_TAB = re.compile(r'<a\b[^>]*role="tab"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", fragment)).split())


def mps_charters(page: str) -> dict[str, bool]:
    """MPS charter names -> whether each is an instrumentality of MPS."""
    out = {}
    parts = _H3.split(page)
    # parts: [before, title1, body1, title2, body2, ...]
    for title, body in zip(parts[1::2], parts[2::2]):
        title = _text(title).lower()
        if not title.endswith("charter schools"):
            continue
        is_instrumentality = not title.startswith("non")
        for item in _TAB.findall(body):
            # "GreenTree Preparatory Academy: 6–12", "La Causa Charter School (two locations)"
            name = re.sub(r"\((two|multiple) locations\)|:.*$", "", _text(item)).strip()
            if name:
                out[name] = is_instrumentality
    if not out:
        raise ValueError("no charter schools found on the MPS page")
    return out


def _squash(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def mps_status(school: str, mps: dict[str, bool]) -> tuple[str, bool] | None:
    """The MPS list entry for a DPI school name: the entry containing the most
    of the school's distinctive words, at least two of them (or its only one).
    Compared without spaces, so "Green Tree" finds "GreenTree"."""
    words = [w for w in meaningful_words(school) if len(w) >= 4] or meaningful_words(school)
    if not words:
        return None
    scored = sorted(((sum(w in _squash(name) for w in words), name) for name in mps), reverse=True)
    best, name = scored[0]
    if best < min(2, len(words)) or (len(scored) > 1 and scored[1][0] == best):
        return None
    return name, mps[name]


# ----------------------------------------------------------------------------
# Loaders: each returns school_governance rows for the operating schools.


def _ca_rows(conn) -> list[tuple]:
    path = fetch(GOV_DIR / "ca_pubschls.txt", _download(CA_SCHOOLS_URL, _is_cde_file))
    by_code = ca_charters(decode_text(path.read_bytes()))
    by_nces = {r["nces_id"]: r for r in by_code.values() if len(r["nces_id"]) == 12}
    rows = []
    for ncessch, st_schid in conn.execute(
        "SELECT ncessch, st_schid FROM schools WHERE st = 'CA' AND is_operating"
    ).fetchall():
        # st_schid is "CA-<district 7>-<school 7>"; the school code is the CDS
        # code's last 7 digits. The NCES ID columns cover the rest.
        r = by_code.get((st_schid or "").rsplit("-", 1)[-1]) or by_nces.get(ncessch)
        if r:
            detail = {k: v for k, v in r.items() if k not in ("governance", "nces_id")}
            rows.append((ncessch, "ca_cde", r["governance"], json.dumps(detail)))
    return rows


def _wi_rows(conn) -> list[tuple]:
    data = fetch(GOV_DIR / "wi_charters.xlsx", _download(WI_CHARTERS_URL, _is_xlsx)).read_bytes()
    listed = wi_charters(data)
    try:
        page = fetch(GOV_DIR / "mps_charters.html", _download(MPS_CHARTERS_URL)).read_text(errors="replace")
        mps = mps_charters(page)
    except (requests.RequestException, OSError, ValueError) as exc:
        # Without the MPS list its charters stay presumed instrumentalities.
        log.warning("MPS charter list unavailable: %r", exc)
        mps = {}
    rows = []
    for ncessch, st_schid in conn.execute(
        "SELECT ncessch, st_schid FROM schools WHERE st = 'WI' AND is_operating"
    ).fetchall():
        r = listed.get(st_schid or "")
        if not r:
            continue
        governance, detail = r["governance"], {k: v for k, v in r.items() if k != "governance"}
        if governance == "district_run":
            # 118.40(7)(a): unknown unless the district says; DPI's statewide
            # count makes instrumentality the presumption.
            detail["instrumentality"] = None
            if r["lea_code"] == MPS_LEA_CODE and (hit := mps_status(r["school"], mps)):
                detail["mps_list_name"], detail["instrumentality"] = hit
                if not hit[1]:
                    governance = "independent"
        rows.append((ncessch, "wi_dpi", governance, json.dumps(detail)))
    return rows


def _hi_rows(conn) -> list[tuple]:
    detail = json.dumps({"authorizer": "State Public Charter School Commission",
                         "basis": "HRS 302D-25(e): public schools and entities of the State"})
    return [
        (ncessch, "hi_hrs", "state_agency", detail)
        for (ncessch,) in conn.execute("SELECT ncessch FROM schools WHERE st = 'HI' AND is_operating").fetchall()
    ]


LOADERS = {"ca_cde": _ca_rows, "wi_dpi": _wi_rows, "hi_hrs": _hi_rows}


def load_governance(connect: Callable) -> dict[str, object]:
    """Rebuild school_governance one source at a time. connect() returns a
    connection used as a context manager (PostgresResource.connect, or a
    psycopg connect partial from a script)."""
    metadata: dict[str, object] = {}
    for source, load in LOADERS.items():
        try:
            with connect() as conn:
                rows = load(conn)
                conn.execute("DELETE FROM school_governance WHERE source = %s", (source,))
                with conn.cursor() as cur, cur.copy(f"COPY school_governance ({', '.join(COLUMNS)}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
        except Exception as exc:  # one source failing must not stop the others
            log.warning("governance source %s failed, keeping its previous rows: %r", source, exc)
            metadata[f"{source}_error"] = repr(exc)[:300]
            continue
        for governance in ("district_run", "state_agency", "independent"):
            if n := sum(r[2] == governance for r in rows):
                metadata[f"{source}_{governance}"] = n
    return metadata


@asset(group_name="pipeline", deps=[ccd_schools],
       description="Charter schools that state records show as district-run, state bodies, or independent.")
def school_governance(pg: PostgresResource) -> MaterializeResult:
    return MaterializeResult(metadata=load_governance(pg.connect))
