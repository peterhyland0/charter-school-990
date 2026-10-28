"""Who holds each Florida charter school, from Florida's own records.

Florida charter schools must be run by nonprofits (s. 1002.33(12)(i), F.S.),
but a campus seldom shares a name, website or address with its holder's 990:
"SOMERSET ACADEMY AVENIR" is run by Somerset Academy, Inc. Two sources name
the holder, each written to external_links like the sources in external.py:

- fl_ag: every Florida charter school files its annual financial audit with
  the Auditor General, who posts them at flauditor.gov, one page per school
  with a PDF per fiscal year. The opening note, and often the cover, names the
  corporation that runs the school ("... is operated by Miami-Dade Foundation
  for Educational Innovation, Inc., a not-for-profit corporation"; "(A CHARTER
  SCHOOL UNDER ADVANTAGE ACADEMY OF MIAMI, INC.)"). The latest report of each
  school is read, the entity taken from the most direct phrase that names
  one (ENTITY_PATTERNS), and the report joined to NCES by the school number
  it cites, or by name within the county whose school board it names.
  Reports are text PDFs; a scanned one yields nothing. The PDFs never change,
  so they are fetched once; the pages are re-read every EXTERNAL_MAX_AGE_DAYS.
- fl_sunbiz: the Division of Corporations' fictitious-name (DBA) filings: a
  school whose exact name is a corporation's registered trade name is held by
  that corporation, with its FEI (EIN) when the filing gives one. The state
  serves the bulk file only over SFTP, so it is placed by hand (SUNBIZ_FIC).

City-run charters (Pembroke Pines, Coral Springs, Aventura) have no
corporation to find, and their audits name none.
"""

import html
import json
import logging
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests

from ..settings import HTTP_HEADERS, RAW_DIR
from ..util import GENERIC_WORDS, name_key
from .external import _keys, fetch

log = logging.getLogger(__name__)
FL_DIR = RAW_DIR / "external" / "florida"

AG_INDEX_URL = "https://flauditor.gov/pages/chschools_efiles.html"
# Seconds between requests to one host.
REQUEST_DELAY = 1.0
# Pages of each audit report read: the organisation note sits in the first 30
# or so; a combined report lists its schools near the front.
AG_MAX_PAGES = 40

_last_request = 0.0


def _get(url: str) -> bytes:
    """GET politely: one request a second, two gentle retries."""
    global _last_request
    for attempt in range(3):
        wait = _last_request + REQUEST_DELAY - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=120)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.content
        except requests.ConnectionError:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))
    raise requests.RequestException(url)


def _download(url: str):
    def download(out) -> None:
        out.write(_get(url))
    return download


_SCHOOL_LINK = re.compile(r'<a\s+href="(chschools_efile%20pages/[^"]+)"[^>]*>(.*?)</a>', re.I | re.S)


def ag_school_pages(index_html: str) -> list[tuple[str, str]]:
    """(school page URL, school name) for every school on the e-file index."""
    out, seen = [], set()
    for href, text in _SCHOOL_LINK.findall(index_html):
        url = urljoin(AG_INDEX_URL, html.unescape(href))
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        if url not in seen and name:
            seen.add(url)
            out.append((url, name))
    return out


_YEAR = re.compile(r"(\d{4})-(\d{2})\s+Fiscal\s+Year", re.I)
_PDF = re.compile(r'href="([^"]+\.pdf)"', re.I)


def ag_reports(page_html: str, page_url: str) -> list[tuple[int, str]]:
    """(fiscal year end, report URL) for each report on a school's page, in
    page order; the year is that of the heading above the link."""
    out, year = [], None
    for m in re.finditer(f"{_YEAR.pattern}|{_PDF.pattern}", page_html, re.I):
        if m.group(1):
            year = int(m.group(1)) + 1
        elif year:
            out.append((year, urljoin(page_url, html.unescape(m.group(3)))))
    return out


def _slug(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", unquote(url.rsplit("/", 1)[-1]))


def _report_text(url: str) -> str:
    """The first AG_MAX_PAGES pages of a report as text, cached beside the PDF."""
    import pypdf

    pdf = FL_DIR / "ag" / "reports" / _slug(url)
    txt = pdf.with_suffix(".txt")
    if txt.exists():
        return txt.read_text()
    if not pdf.exists():
        fetch(pdf, _download(url))
    reader = pypdf.PdfReader(pdf)
    parts = []
    for page in reader.pages[:AG_MAX_PAGES]:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # a malformed page must not lose the report
            log.warning("page of %s unreadable: %r", pdf.name, exc)
    text = "\n\f\n".join(parts)
    txt.write_text(text)
    return text


# ----------------------------------------------------------------------------
# Reading the operating entity out of a report.

_NFP = r"(?:Florida\s+)?(?:not-for-profit|nonprofit)"
# One organisation name: capitalised words, with the connectors names use.
_NAME = (r"[A-Z0-9][\w'’&./\-]*(?:,?\s+(?:[A-Z0-9][\w'’&./\-]*|of|for|and|the|at|in|on|de|del|la|&|[–-]))*")
# Phrases that name the operating entity, most direct first. Each captures it as "e".
ENTITY_PATTERNS = (
    # "The School's charter is held by Denovo, Inc., a not-for-profit corporation"
    rf"charter is held by (?P<e>{_NAME}),?\s+(?:\([^)]{{1,40}}\)\s*,?\s*)?a\s+{_NFP}",
    # "is operated by Miami-Dade Foundation for Educational Innovation, Inc. (MDFEI), a not-for-profit"
    rf"(?:operated|governed|sponsored|organized|owned) by (?P<e>{_NAME}),?\s+(?:\([^)]{{1,40}}\)\s*,?\s*)?(?:is\s+)?a\s+{_NFP}",
    # "is a charter school under Academic Solutions Academy, Inc., a not-for-profit"; "a division of
    # Achievement Academy, Inc. (a nonprofit organization)"
    rf"(?:under|division of|department of|program of|operated by)\s+(?:the\s+)?(?P<e>{_NAME}),?\s+(?:\([^)]{{1,40}}\)\s*,?\s*)?\(?(?:is\s+)?a\s+{_NFP}",
    # Cover pages: "(A CHARTER SCHOOL UNDER ADVANTAGE ACADEMY OF MIAMI, INC.)", "A Department of
    # Archimedean Academy, Inc.", "An elementary school program of Alachua Learning Center, Inc."
    rf"(?i:charter school under|a department of|school program of|a division of)\s+(?:THE\s+|the\s+)?(?P<e>{_NAME}(?i:,?\s+inc\b\.?))",
    # "ACADEMIR CHARTER SCHOOLS, INC. D/B/A ACADEMIR CHARTER SCHOOL WEST"
    rf"(?P<e>{_NAME}(?i:,?\s+inc\b\.?))\s+(?i:d/b/a|dba)\s",
    # "Academy at the Farm, Inc., (the "School") is a not-for-profit corporation"
    rf"(?P<e>{_NAME}),?\s+(?:\([^)]{{1,40}}\)\s*,?\s*)?(?:\([^)]{{1,40}}\)\s*,?\s*)?is\s+a\s+{_NFP}\s+(?:corporation|organization|entity)",
    # Failing all of those, the corporation the report names most, at least twice.
    rf"(?P<e>{_NAME}(?i:,?\s+inc\b\.?))",
)
# The last pattern counts any "..., Inc." in the report, so it must skip the
# management companies, auditors and lenders reports also name.
_NOT_HOLDERS = re.compile(
    r"\b(CHARTER SCHOOLS USA|ACADEMICA|IMAGINE SCHOOLS|EDISON|PEARSON|K12|STRIDE|CONNECTIONS|SCHOOL DEVELOPMENT|"
    r"REDEVELOPMENT|PROPERTIES|PROPERTY|HOLDINGS|MANAGEMENT|BANK|FINANCIAL|INSURANCE|CAPITAL|"
    r"DEVELOPERS|REALTY|LEASING|CONSULTING|MOORE|CPAS?|ASSOCIATES)\b")
# "The School is a not-for-profit corporation": the school is its own
# corporation, so its holder carries its name.
_SELF = re.compile(rf"\bThe\s+(?:School|Academy|Charter School)\s+is\s+a\s+{_NFP}\s+corporation")
_ENTITY_RES = [re.compile(p) for p in ENTITY_PATTERNS]
# Note headings that run into the name in extracted text ("Reporting Entity Academy at the Farm").
_HEADING_WORDS = frozenset(
    """NOTE NOTES ORGANIZATION ORGANIZATIONS OPERATIONS NATURE ENTITY REPORTING POLICIES ACCOUNTING
    SIGNIFICANT SUMMARY DESCRIPTION BACKGROUND GENERAL INFORMATION STATEMENTS FINANCIAL PURPOSE
    HISTORY ACTIVITIES BASIS PRESENTATION INCOME TAXES TAX ALTHOUGH WHILE BECAUSE SINCE""".split()
)
# Subjects that aren't names: "The School is a not-for-profit corporation".
_NOT_NAMES = re.compile(
    r"^(THE |A |AN )?(SCHOOL|ACADEMY|CHARTER SCHOOL|ORGANIZATION|CORPORATION|ENTITY|CENTER|SCHOOLS|COMPANY|"
    r"FOUNDATION|BOARD|DISTRICT|IT|WHICH|SUCH)$|\b(INCOME TAX|INTERNAL REVENUE|UNIFORM GUIDANCE|"
    r"SECTION|CHAPTER|STATUTES|AUDITOR|CPAS?|LLP|LLC|P\.?A\.?|P\.?L\.?)$")


def normalize_text(text: str) -> str:
    """One line of text, with the PDF extraction's broken spacing mended where
    it matters: "not -for-profit", "Miami- Dade"."""
    text = " ".join(text.replace("\f", " ").split())
    text = re.sub(r"\s+([,;:)])", r"\1", re.sub(r"\(\s+", "(", text))
    text = re.sub(r"(?i)\bnot\s*-?\s*for\s*-?\s*profit\b", "not-for-profit", text)
    text = re.sub(r"(?i)\bnon\s*-\s*profit\b|\bnon\s+profit\b", "nonprofit", text)
    return re.sub(r"(?<=\w)- (?=[A-Z])|(?<=[a-z]) -(?=[A-Z])", "-", text)


def _mend_words(name: str, vocab: dict[str, int]) -> str:
    """Join the words PDF extraction split ("Academir C harter School"), when the
    joined word is one the report spells whole elsewhere."""
    words, out = name.split(), []
    for w in words:
        if out and (joined := out[-1] + w).lower() in vocab and vocab[joined.lower()] >= 2 and (
                vocab.get(out[-1].lower(), 0) < vocab[joined.lower()] or vocab.get(w.lower(), 0) < vocab[joined.lower()]):
            out[-1] = joined
        else:
            out.append(w)
    return " ".join(out)


def _mend_text(text: str, vocab: dict[str, int]) -> str:
    """_mend_words over a whole report, for splits inside a capitalised word
    ("Be ulah Academy"), which would otherwise break a name in two."""
    def join(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        joined = (a + b).lower()
        if vocab.get(joined, 0) >= 2 and min(vocab.get(a.lower(), 0), vocab.get(b.lower(), 0)) < vocab[joined]:
            return a + b
        return m.group(0)
    return re.sub(r"\b([A-Z][a-z]{0,2}) ([a-z]+)\b", join, text)


def _clean_entity(raw: str, vocab: dict[str, int]) -> str | None:
    # "Bodwell Academy, A Component of The Arc Of Palm Beach County, Inc."
    raw = re.split(r"(?i),?\s+an?\s+(?:component|division|department|program)\s+of\s+", raw)[-1]
    # "To the Board of Directors of United Cerebral Palsy...".
    raw = re.split(r"(?i)\b(?:board of directors|trustees|members) of\s+(?:the\s+)?", raw)[-1]
    # "Franklin Academy Foundation, Inc. and Subsidiaries"; "Marco Island Academy, A Public Charter High School".
    raw = re.split(r"(?i)\s+and\s+(?:subsidiar|affiliate)|,\s+an?\s+(?:public|charter|florida|not-for|nonprofit)", raw)[0]
    # A name runs on across a sentence end: "...Elementary School. The Company".
    raw = re.split(r"(?<!\bInc)(?<!\bSt)(?<!\b[A-Z])\.\s+(?=[A-Z])", raw)[-1]
    words = raw.split()
    # Drop note headings and numbering that run into the name.
    for i in range(len(words) - 1, -1, -1):
        if words[i].strip(".:–-").upper() in _HEADING_WORDS or re.fullmatch(r"[\dA-Z]?[.)]|\d+", words[i]):
            words = words[i + 1:]
            break
    name = _mend_words(" ".join(words), vocab).strip(" ,.;:")
    if re.search(r"(?i),?\s+inc$", name):
        name += "."
    key = (name_key(name) or "")
    if (len(key) < 4 or _NOT_NAMES.search(key) or not re.search(r"[A-Za-z]{3}", name)
            or re.search(r"\b(SCHOOL BOARD|BOARD OF|COUNTY FLORIDA)\b|\b(AND|OF|THE|FOR|AT)$", key)):
        return None
    return name


def report_entity(text: str) -> tuple[str, int] | None:
    """The legal entity a charter school audit names as running the school, and
    the index of the pattern that found it (lower is more direct)."""
    flat = normalize_text(text)
    vocab: dict[str, int] = {}
    for w in re.findall(r"[A-Za-z]+", flat):
        vocab[w.lower()] = vocab.get(w.lower(), 0) + 1
    flat = _mend_text(flat, vocab)
    last = len(_ENTITY_RES) - 1
    for i, pattern in enumerate(_ENTITY_RES):
        found: dict[str, int] = {}
        for m in pattern.finditer(flat):
            # Names run on past an "Inc.": "Sample School, Inc. The School is...". The
            # subject is the last; the last pattern counts each.
            parts = _AFTER_INC.split(m.group("e"))
            for part in parts if i == last else parts[-1:]:
                name = _clean_entity(part, vocab)
                if name and not (i == last and _NOT_HOLDERS.search(name.upper())):
                    found[name] = found.get(name, 0) + 1
        if i == last:
            found = {n: c for n, c in found.items() if c >= 2}
        if found:
            return max(found, key=lambda n: (bool(_LEGAL_END.search(n)), found[n], len(n))), i
    if _SELF.search(flat):
        return SELF, len(_ENTITY_RES)
    return None


_LEGAL_END = re.compile(r"(?i)\b(inc|incorporated|corporation|corp)\.?$")
_AFTER_INC = re.compile(r"(?<=\b[Ii]nc\.)\s+(?=[A-Z])|(?<=\bINC\.)\s+(?=[A-Z])")
# report_entity's answer when the school is its own corporation.
SELF = "(the school)"


_COUNTY = re.compile(
    r"(?i)(?:school board of|school district of|board of)\s+([A-Z][A-Za-z.\- ]{1,20}?)\s+county"
    r"|([A-Z][A-Za-z.\-]+(?: [A-Z][A-Za-z.\-]+)?)\s+county(?:,? florida)?,?\s+(?:district\s+)?school\s+(?:board|district)")


def county_key(name: str | None) -> str | None:
    """MIAMI-DADE, Dade, St. Lucie, Saint Lucie -> MIAMIDADE, MIAMIDADE, STLUCIE, STLUCIE."""
    key = re.sub(r"[^A-Z]", "", (name or "").upper().replace("SAINT", "ST"))
    return {"DADE": "MIAMIDADE"}.get(key, key) or None


def report_county(text: str) -> str | None:
    """The county whose school board the report names (the school's sponsor), as a county_key."""
    counts: dict[str, int] = {}
    for m in _COUNTY.finditer(normalize_text(text)):
        words = (m.group(1) or m.group(2)).split()
        # "...Component Unit of the District School Board of Miami-Dade County": keep the last words.
        key = county_key(" ".join(w for w in words[-2:] if w.lower() not in {"the", "of"}))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get) if counts else None


# "Academic Solutions Academy - A #5233", "School No. 0123", "MSID 5414".
_SCHOOL_NO = re.compile(r"(?i)(?:#\s?|school\s+(?:no\.?|number)\s*|msid\s*(?:no\.?|number|#)?\s*)(\d{4})\b")


def report_school_numbers(text: str) -> set[str]:
    return set(_SCHOOL_NO.findall(normalize_text(text)))


def ag_reports_latest() -> list[dict]:
    """Each school page's latest report: page name, fiscal year, URL and text.
    A page or report that can't be fetched is skipped; the index must load."""
    index = fetch(FL_DIR / "ag" / "index.html", _download(AG_INDEX_URL)).read_text(errors="replace")
    out = []
    for url, name in ag_school_pages(index):
        try:
            page = fetch(FL_DIR / "ag" / "pages" / _slug(url), _download(url)).read_text(errors="replace")
            reports = ag_reports(page, url)
            if not reports:
                continue
            year = max(y for y, _ in reports)
            # Under one year a revised report follows the original.
            report_url = [u for y, u in reports if y == year][-1]
            out.append({"page": name, "year": year, "url": report_url, "text": _report_text(report_url)})
        except (requests.RequestException, OSError, ValueError) as exc:
            log.warning("AG report for %s unavailable: %r", name, exc)
        except Exception as exc:  # pypdf raises its own errors on broken files
            log.warning("AG report for %s unreadable: %r", name, exc)
    return out


# ----------------------------------------------------------------------------
# Joining reports to NCES schools.

# A report is about a school in the report's county whose name is this similar
# to the report's page name, compared on distinctive words as the matcher does
# (name_core: "Academir Charter School East at Doral" is ACADEMIR EAST DORAL),
# or anywhere in Florida at AG_STATE_SIMILARITY; or, whatever the name, whose
# state school number the report cites in the school's county.
AG_COUNTY_SIMILARITY = 0.7
AG_STATE_SIMILARITY = 0.9
# A cited school number counts when the names share something ("KIPP Miami
# Sunrise Academy" citing KIPP Miami-Liberty City's number: same holder), not
# for a stray "#2022"; and outweighs a name this much less similar.
AG_NUMBER_SIMILARITY = 0.3
AG_NUMBER_WEIGHT = 0.3

AG_MATCH = """
    WITH s AS (
        SELECT ncessch, name_key, name_core(name_key, %(generic)s) AS core, name_nums(name_key) AS nums,
               split_part(st_schid, '-', 2) AS dist, split_part(st_schid, '-', 3) AS schno
        FROM schools WHERE st = 'FL' AND is_operating
    )
    SELECT DISTINCT ON (s.ncessch) s.ncessch, a.id, round(x.sim::numeric, 2), x.by_number
    FROM s JOIN ag a ON a.core %% s.core OR (a.dist = s.dist AND s.schno = ANY(a.numbers))
    CROSS JOIN LATERAL (SELECT similarity(s.core, a.core) AS sim,
                               coalesce(a.dist = s.dist AND s.schno = ANY(a.numbers)
                                        AND similarity(s.core, a.core) >= %(number_sim)s, false) AS by_number) x
    WHERE x.by_number
       OR ((s.nums = '' OR a.nums = '' OR s.nums = a.nums)
           AND ((a.dist = s.dist AND x.sim >= %(county_sim)s) OR x.sim >= %(state_sim)s))
    -- A cited number outweighs a name up to NUMBER_WEIGHT less similar.
    ORDER BY s.ncessch, x.sim + %(number_weight)s * x.by_number::int DESC,
             similarity(s.name_key, a.name_key) DESC, a.year DESC
"""


def fl_ag_rows(conn) -> list[tuple]:
    """external_links rows (source fl_ag) from the Auditor General's audits."""
    reports = ag_reports_latest()
    if not reports:
        raise ValueError("no Auditor General reports could be read")
    # District numbers by county: Florida numbers its districts by county.
    dists = {county_key(lea): dist for dist, lea in conn.execute(
        "SELECT DISTINCT split_part(st_schid, '-', 2), lea_name FROM schools WHERE st = 'FL'").fetchall()}
    conn.execute("""CREATE TEMP TABLE ag (id int, page text, year int, url text, entity text, pattern int,
                    dist text, numbers text[], name_key text, nums text) ON COMMIT DROP""")
    found = {}
    with conn.cursor() as cur, cur.copy("COPY ag (id, page, year, url, entity, pattern, dist, numbers, name_key) FROM STDIN") as copy:
        for i, r in enumerate(reports):
            hit = report_entity(r["text"])
            if not hit:
                continue
            entity, pattern = hit
            found[i] = (r, r["page"] if entity == SELF else entity, pattern)
            copy.write_row((i, r["page"], r["year"], r["url"], found[i][1], pattern, dists.get(report_county(r["text"])),
                            sorted(report_school_numbers(r["text"])), name_key(r["page"])))
    conn.execute("ALTER TABLE ag ADD COLUMN core text")
    conn.execute("UPDATE ag SET nums = name_nums(name_key), core = name_core(name_key, %s)", (list(GENERIC_WORDS),))
    conn.execute(f"SET LOCAL pg_trgm.similarity_threshold = {min(AG_COUNTY_SIMILARITY, AG_STATE_SIMILARITY)}")
    rows = []
    params = {"generic": list(GENERIC_WORDS), "county_sim": AG_COUNTY_SIMILARITY, "state_sim": AG_STATE_SIMILARITY,
              "number_sim": AG_NUMBER_SIMILARITY, "number_weight": AG_NUMBER_WEIGHT}
    for ncessch, i, sim, by_number in conn.execute(AG_MATCH, params).fetchall():
        r, entity, pattern = found[i]
        rows.append((ncessch, "fl_ag", None, entity, _keys(entity), None, json.dumps({
            "report_school": r["page"], "fiscal_year": r["year"], "report": r["url"], "pattern": pattern,
            "similarity": float(sim), "by_school_number": by_number})))
    return rows


# ----------------------------------------------------------------------------
# Sunbiz fictitious names.

# The Division of Corporations' quarterly fictitious name file (ficdata.zip,
# fixed-width ASCII). The state serves it only from an SFTP server behind a
# login it publishes for everyone, so it is placed here by hand rather than
# fetched by the pipeline.
SUNBIZ_FIC = FL_DIR / "sunbiz" / "ficdata.zip"
# Field positions (0-based start, end) from dos.sunbiz.org/data-definitions/fic.html.
FIC_NAME = (12, 204)
FIC_COUNTY = (204, 216)
FIC_OWNERS_AT = 388
FIC_OWNER_LEN = 171
FIC_MAX_OWNERS = 10
# Within an owner block: name, format (P person, C corporation), FEI, charter number.
FIC_OWNER_NAME = (12, 67)
FIC_OWNER_FORMAT = (67, 68)
FIC_OWNER_FEI = (150, 159)
FIC_OWNER_DOC = (159, 171)


def fic_record(line: str) -> tuple[str, str, list[tuple[str, str | None]]] | None:
    """(fictitious name, county, [(corporate owner, FEI)]) from one ficdata
    line, or None when no owner is a corporation."""
    def field(span, base=0):
        return line[base + span[0]:base + span[1]].strip()
    owners = []
    for n in range(FIC_MAX_OWNERS):
        base = FIC_OWNERS_AT + n * FIC_OWNER_LEN
        if len(line) <= base or field(FIC_OWNER_FORMAT, base) != "C" or not field(FIC_OWNER_NAME, base):
            continue
        fei = re.sub(r"\D", "", field(FIC_OWNER_FEI, base))
        owners.append((field(FIC_OWNER_NAME, base), fei if len(fei) == 9 and fei.strip("0") else None))
    name = field(FIC_NAME)
    return (name, field(FIC_COUNTY), owners) if name and owners else None


def _fic_lines(path: Path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():
                with z.open(member) as f:
                    for raw in f:
                        yield raw.decode("cp1252", errors="replace").rstrip("\r\n")
    else:
        with open(path, encoding="cp1252", errors="replace") as f:
            for raw in f:
                yield raw.rstrip("\r\n")


def fl_sunbiz_rows(conn) -> list[tuple]:
    """external_links rows (source fl_sunbiz): a school whose exact name
    (name_key) is a fictitious name registered by a corporation, in the
    school's county when the filing gives one."""
    if not SUNBIZ_FIC.exists():
        raise FileNotFoundError(f"place the Sunbiz quarterly ficdata.zip at {SUNBIZ_FIC}")
    schools: dict[str, list[tuple[str, str | None]]] = {}
    for ncessch, key, lea in conn.execute(
            "SELECT ncessch, name_key, lea_name FROM schools WHERE st = 'FL' AND is_operating").fetchall():
        schools.setdefault(key, []).append((ncessch, county_key(lea)))
    rows = []
    for line in _fic_lines(SUNBIZ_FIC):
        rec = fic_record(line)
        if not rec or name_key(rec[0]) not in schools:
            continue
        name, county, owners = rec
        for ncessch, school_county in schools[name_key(name)]:
            if county and school_county and county_key(county) != school_county:
                continue
            for owner, fei in owners:
                rows.append((ncessch, "fl_sunbiz", fei, owner, _keys(owner), None,
                             json.dumps({"fictitious_name": name, "county": county or None})))
    return rows


def florida_rows(conn) -> list[tuple]:
    """Both Florida sources' rows. external_links replaces rows one source at a
    time, so register fl_ag_rows and fl_sunbiz_rows separately in LOADERS."""
    rows = fl_ag_rows(conn)
    if SUNBIZ_FIC.exists():
        rows += fl_sunbiz_rows(conn)
    return rows
