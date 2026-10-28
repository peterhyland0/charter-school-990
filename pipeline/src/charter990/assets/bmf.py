"""Candidate nonprofits from the IRS Exempt Organizations Business Master File.

Two routes into the pool:
- codes: in-state 501(c)(3)s that the IRS codes as schools (FOUNDATION 11),
  as charter schools (NTEE B29), or whose name says CHARTER.
- first_word: 501(c)(3)s anywhere in the file whose name opens with the same
  distinctive word as an operating charter school or its district, such as
  KIPP or SUCCESS. Big networks are often coded as neither schools nor charters.
"""

import csv
from collections import Counter

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import BMF_URL, RAW_DIR, STATE
from ..util import download_cached, first_word
from .ccd import ccd_schools

# A first word shared by more than this many 501(c)(3)s in the file is too
# common to identify a network (for example BROOKLYN or HARLEM).
MAX_WORD_FREQUENCY = 200

COLUMNS = (
    "ein", "name", "sort_name", "city", "state", "zip", "subsection", "foundation",
    "affiliation", "filing_req_cd", "ntee_cd", "pool_route", "source_file",
)


@asset(group_name="phase0", deps=[ccd_schools], description="Nonprofits that might run the state's charter schools.")
def bmf_candidates(pg: PostgresResource) -> MaterializeResult:
    path = download_cached(BMF_URL, RAW_DIR / "bmf" / BMF_URL.rsplit("/", 1)[1])

    with pg.connect() as conn:
        names = conn.execute(
            "SELECT sch_name, lea_name FROM schools WHERE is_operating"
        ).fetchall()
    school_words = {w for pair in names for n in pair if (w := first_word(n))}

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        orgs = [r for r in csv.DictReader(f) if r["SUBSECTION"] == "03"]

    word_counts = Counter(first_word(r["NAME"]) for r in orgs)
    usable_words = {w for w in school_words if word_counts.get(w, 0) <= MAX_WORD_FREQUENCY}
    too_common = sorted(school_words - usable_words)

    rows, routes = [], Counter()
    for r in orgs:
        name_upper = f"{r['NAME']} {r['SORT_NAME']}".upper()
        by_codes = r["STATE"] == STATE and (
            r["FOUNDATION"] == "11" or r["NTEE_CD"] == "B29" or "CHARTER" in name_upper
        )
        by_word = first_word(r["NAME"]) in usable_words
        if not (by_codes or by_word):
            continue
        route = "codes" if by_codes else "first_word"
        routes[route] += 1
        rows.append((
            r["EIN"].zfill(9), r["NAME"], r["SORT_NAME"] or None, r["CITY"], r["STATE"],
            r["ZIP"], r["SUBSECTION"], r["FOUNDATION"], r["AFFILIATION"],
            r["FILING_REQ_CD"], r["NTEE_CD"] or None, route, path.name,
        ))

    with pg.connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE bmf_orgs")
        with cur.copy(f"COPY bmf_orgs ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)

    return MaterializeResult(metadata={
        "pool_size": len(rows),
        "route_codes": routes["codes"],
        "route_first_word": routes["first_word"],
        "school_words": len(school_words),
        "words_dropped_as_too_common": ", ".join(too_common) or "none",
    })
