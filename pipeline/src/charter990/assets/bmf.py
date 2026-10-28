"""Candidate nonprofits from the IRS Exempt Organizations Business Master File.

Three routes into the pool, both limited to 501(c)(3) organisations:
- codes: in a state with charter schools, and coded by the IRS as a school
  (FOUNDATION 11) or a charter school (NTEE B29), or named CHARTER.
- name: coded as education (NTEE B) or not coded at all, and sharing a network
  key with an operating charter school or its district, such as KIPP or
  GREAT HEARTS. Many big networks are coded as neither schools nor charters.
- external: named as a school's holder outside the IRS data (external_links):
  by EIN in a federal single audit, or by its exact legal name, in the school's
  state, in a state charter-holder record.

Support groups (boosters, PTAs, alumni, "Friends of") never enter the pool
unless the IRS codes them as a school: they share school names and websites
but don't run schools.
"""

import csv
from collections import Counter
from pathlib import Path

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import BMF_URLS, RAW_DIR
from ..util import download_cached, first_two, first_word, is_support_org, name_key, network_key, zip5
from .ccd import ccd_schools
from .external import external_links

# A key shared by more than this many 501(c)(3)s nationally is too common to
# identify a network (for example HOPE or ALLIANCE on their own).
MAX_KEY_FREQUENCY = 200

COLUMNS = (
    "ein", "name", "sort_name", "city", "state", "zip", "subsection", "foundation",
    "affiliation", "filing_req_cd", "ntee_cd", "pool_route", "source_file",
    "street", "zip5", "name_key", "sort_name_key",
)


def _charities(paths: list[Path]):
    """Stream 501(c)(3) rows from the regional files without holding them in memory."""
    for path in paths:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                if r["SUBSECTION"] == "03":
                    yield path.name, r


@asset(group_name="pipeline", deps=[ccd_schools, external_links], description="Nonprofits that might run the charter schools.")
def bmf_candidates(pg: PostgresResource) -> MaterializeResult:
    paths = [download_cached(u, RAW_DIR / "bmf" / u.rsplit("/", 1)[1], refresh=True) for u in BMF_URLS]

    with pg.connect() as conn:
        names = conn.execute("SELECT sch_name, lea_name FROM schools WHERE is_operating").fetchall()
        states = {s for (s,) in conn.execute("SELECT DISTINCT st FROM schools WHERE is_operating").fetchall()}
        external_eins = {e for (e,) in conn.execute(
            "SELECT DISTINCT ein FROM external_links WHERE ein IS NOT NULL").fetchall()}
        external_names = set(conn.execute(
            """SELECT DISTINCT s.st, k FROM external_links x JOIN schools s USING (ncessch),
                      unnest(x.holder_keys) AS k
               WHERE x.ein IS NULL""").fetchall())

    # Pass 1: how common each opening word and word pair is among all 501(c)(3)s.
    first_counts, two_counts = Counter(), Counter()
    for _, r in _charities(paths):
        first_counts[first_word(r["NAME"])] += 1
        two_counts[first_two(r["NAME"])] += 1

    school_keys = {
        k for pair in names for n in pair
        if (k := network_key(n, first_counts, two_counts, MAX_KEY_FREQUENCY))
    }

    # Pass 2: apply the pool rules.
    rows, routes, support_dropped, seen = [], Counter(), 0, set()
    for source, r in _charities(paths):
        ein = r["EIN"].zfill(9)
        if ein in seen:
            continue
        name, sort_name = r["NAME"], r["SORT_NAME"]
        is_school = r["FOUNDATION"] == "11"
        by_codes = r["STATE"] in states and (
            is_school or r["NTEE_CD"] == "B29" or "CHARTER" in f"{name} {sort_name}".upper()
        )
        by_name = (r["NTEE_CD"][:1] in ("B", "")) and (
            network_key(name, first_counts, two_counts, MAX_KEY_FREQUENCY) in school_keys
        )
        by_external = ein in external_eins or any(
            (r["STATE"], k) in external_names for k in (name_key(name), name_key(sort_name)) if k)
        if not (by_codes or by_name or by_external):
            continue
        if not is_school and (is_support_org(name) or is_support_org(sort_name)):
            support_dropped += 1
            continue
        seen.add(ein)
        route = "codes" if by_codes else "first_word" if by_name else "external"
        routes[route] += 1
        rows.append((
            ein, name, sort_name or None, r["CITY"], r["STATE"], r["ZIP"], r["SUBSECTION"],
            r["FOUNDATION"], r["AFFILIATION"], r["FILING_REQ_CD"], r["NTEE_CD"] or None,
            route, source, r["STREET"] or None, zip5(r["ZIP"]), name_key(name), name_key(sort_name),
        ))

    with pg.connect() as conn:
        pg.replace_table(conn, "bmf_orgs", COLUMNS, rows, live=True)  # the website reads filer states

    return MaterializeResult(metadata={
        "pool_size": len(rows),
        "route_codes": routes["codes"],
        "route_name": routes["first_word"],
        "route_external": routes["external"],
        "support_groups_dropped": support_dropped,
        "network_keys": len(school_keys),
        "states_with_charters": len(states),
    })
