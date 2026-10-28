"""Checks that fail loudly when the numbers look wrong."""

from dagster import AssetCheckResult, AssetCheckSeverity, asset_check

from .assets.bmf import bmf_candidates
from .assets.extract import parsed_filings
from .assets.ccd import ccd_schools
from .assets.match import school_matches
from .assets.returns import SELECT_LATEST, raw_returns
from .assets.zips import zip_members
from .resources import PostgresResource
from .settings import STATE_FILTER, UMBRELLA_LEAIDS
from .util import is_support_org

# Operating charter schools in the CCD SY 2025-26 preliminary file.
EXPECTED_OPERATING = {None: 8129, "NY": 352}
# Success Academy's FY2025 return: labelled 05A in the index, stored in 05B.zip.
SUCCESS_OBJECT_ID = "202641279349300844"
# Known network filers, reported rather than enforced.
KNOWN_NETWORK_EINS = {
    "205298861": "Success Academy Charter Schools",
    "311488698": "Uncommon Schools Inc",
    "203505981": "Uncommon NYC Charter Schools",
    "203971209": "KIPP New York",
    "651203744": "Achievement First Inc",
    "742948339": "IDEA Public Schools",
    "760615245": "Harmony Public Schools",
    "742974642": "KIPP DC",
    "300135927": "KIPP Chicago",
    "954679811": "Green Dot Public Schools",
    "472892698": "Alliance College-Ready Public Schools",
    "454306715": "Great Hearts America",
}
MAX_FAILURE_RATE = 0.05
MIN_MATCH_RATE = 0.5
# Role expectations for known networks: (school website, state, EIN, expected role).
KNOWN_ROLES = [
    ("ideapublicschools.org", "TX", "742948339", "operator"),       # IDEA Public Schools
    ("successacademies.org", "NY", "364629540", "operator"),        # Success Academy Charter Schools - NYC
    ("successacademies.org", "NY", "205298861", "management"),      # Success Academy Charter Schools Inc
]
# Known operators of single schools, by NCES id, which survives renames:
# (NCES id, state, EIN).
KNOWN_OPERATORS = [
    ("390049605206", "OH", "731685362"),  # Summit Academy - Toledo, one of 21 campus nonprofits on one site
]
# Nonprofits that must never be linked: (EIN, why).
NEVER_LINKED = {
    "750917417": "Texas College, a university, not Texas College Preparatory Academies",
}
# A charter district whose schools are run by this many different operators
# (counting only schools with one clear operator) is probably a statewide
# umbrella (an authorizer) that UMBRELLA_LEAIDS misses.
UMBRELLA_WATCH_SCHOOLS = 10
UMBRELLA_WATCH_OPERATORS = 5

# Each active correction, whether its school still operates, and whether the
# matches reflect it. Shared with the correction tool, which checks its own
# correction right after rebuilding.
CORRECTION_STATE = """
    SELECT c.id, c.action, c.ncessch, c.ein, coalesce(s.is_operating, false) AS operating,
           CASE c.action
               WHEN 'link' THEN EXISTS (SELECT 1 FROM school_matches m WHERE m.ncessch = c.ncessch
                                        AND m.ein = c.ein AND m.role = 'operator' AND m.source = 'corrected')
               WHEN 'unlink' THEN NOT EXISTS (SELECT 1 FROM school_matches m
                                              WHERE m.ncessch = c.ncessch AND m.ein = c.ein)
               ELSE EXISTS (SELECT 1 FROM school_status st WHERE st.ncessch = c.ncessch
                            AND st.reason = 'corrected_none')
           END AS holds
    FROM match_corrections c LEFT JOIN schools s USING (ncessch)
    WHERE c.retired_at IS NULL
"""


@asset_check(asset=ccd_schools, description="Operating charter count matches the CCD file.")
def operating_school_count(pg: PostgresResource) -> AssetCheckResult:
    with pg.connect() as conn:
        (n,) = conn.execute("SELECT count(*) FROM schools WHERE is_operating").fetchone()
    expected = EXPECTED_OPERATING.get(STATE_FILTER)
    return AssetCheckResult(
        passed=expected is None or n == expected,
        metadata={"operating": n, "expected": expected if expected is not None else "no expectation for this filter"},
    )


@asset_check(asset=bmf_candidates, description="Which known network filers made it into the pool.")
def known_networks_in_pool(pg: PostgresResource) -> AssetCheckResult:
    with pg.connect() as conn:
        found = {e for (e,) in conn.execute(
            "SELECT ein FROM bmf_orgs WHERE ein = ANY(%s)", (list(KNOWN_NETWORK_EINS),)
        ).fetchall()}
    missing = {e: n for e, n in KNOWN_NETWORK_EINS.items() if e not in found}
    return AssetCheckResult(
        passed=not missing,
        severity=AssetCheckSeverity.WARN,
        metadata={"found": len(found), "missing": ", ".join(f"{n} ({e})" for e, n in missing.items()) or "none"},
    )


@asset_check(asset=zip_members, description="Returns are located by zip contents, not by index label.")
def success_return_located(pg: PostgresResource) -> AssetCheckResult:
    if STATE_FILTER not in (None, "NY"):
        return AssetCheckResult(passed=True, metadata={"skipped": f"state filter {STATE_FILTER}"})
    with pg.connect() as conn:
        row = conn.execute("SELECT zip_name FROM zip_members WHERE object_id = %s", (SUCCESS_OBJECT_ID,)).fetchone()
    zip_name = row[0] if row else None
    return AssetCheckResult(passed=zip_name == "2026_TEOS_XML_05B.zip", metadata={"zip": zip_name or "not found"})


@asset_check(asset=raw_returns, description="At most 5% of selected returns failed to fetch or weren't in any zip.")
def fetch_failure_rate(pg: PostgresResource) -> AssetCheckResult:
    with pg.connect() as conn:
        (failed,) = conn.execute("SELECT count(*) FROM load_errors WHERE stage = 'fetch'").fetchone()
        (total,) = conn.execute(f"SELECT count(*) FROM ({SELECT_LATEST}) s").fetchone()
    rate = failed / total if total else 0.0
    return AssetCheckResult(passed=rate <= MAX_FAILURE_RATE, metadata={"failed": failed, "selected": total, "rate": round(rate, 4)})


@asset_check(asset=school_matches, description="No support group (booster, PTA, alumni) is a verified match.")
def no_support_groups_verified(pg: PostgresResource) -> AssetCheckResult:
    # Same exemption as the pool: an organisation the IRS codes as a school
    # (FOUNDATION 11) is a school, even if named "Girls Athletic Leadership School".
    with pg.connect() as conn:
        names = conn.execute(
            """SELECT DISTINCT f.org_name FROM school_matches m
               JOIN filings f ON f.object_id = m.object_id
               LEFT JOIN bmf_orgs b ON b.ein = m.ein
               WHERE m.tier = 'verified' AND b.foundation IS DISTINCT FROM '11'"""
        ).fetchall()
    offenders = sorted(n for (n,) in names if is_support_org(n))
    return AssetCheckResult(passed=not offenders, metadata={"offenders": ", ".join(offenders[:20]) or "none"})


@asset_check(asset=school_matches, description="At least half of operating schools are verified or likely.")
def match_rate(pg: PostgresResource) -> AssetCheckResult:
    with pg.connect() as conn:
        matched, total = conn.execute(
            "SELECT count(*) FILTER (WHERE status <> 'unmatched'), count(*) FROM school_status"
        ).fetchone()
    rate = matched / total if total else 0.0
    return AssetCheckResult(
        passed=rate >= MIN_MATCH_RATE,
        severity=AssetCheckSeverity.WARN,
        metadata={"matched": matched, "operating": total, "rate": round(rate, 4)},
    )


@asset_check(asset=school_matches, description="Every active hand correction is reflected in the matches.")
def corrections_hold(pg: PostgresResource) -> AssetCheckResult:
    with pg.connect() as conn:
        rows = conn.execute(CORRECTION_STATE).fetchall()
    orphaned = [r[0] for r in rows if not r[4]]
    broken = [r[0] for r in rows if r[4] and not r[5]]
    return AssetCheckResult(
        passed=not broken,
        metadata={
            "active": len(rows),
            "not_applied": ", ".join(map(str, broken)) or "none",
            # The school closed or NCES renumbered it: the correction no longer applies.
            "school_gone": ", ".join(map(str, orphaned)) or "none",
        },
    )


@asset_check(asset=school_matches, description="Known networks get the expected roles, and known wrong links stay out.")
def known_roles(pg: PostgresResource) -> AssetCheckResult:
    problems = []
    with pg.connect() as conn:
        for domain, st, ein, role in KNOWN_ROLES:
            if STATE_FILTER not in (None, st):
                continue
            found = dict(conn.execute(
                """SELECT m.role, count(*) FROM school_matches m JOIN schools s USING (ncessch)
                   WHERE s.website_domain = %s AND s.st = %s AND m.ein = %s GROUP BY 1""",
                (domain, st, ein),
            ).fetchall())
            if set(found) != {role}:
                problems.append(f"{ein} on {domain} schools: expected {role}, got {found or 'no link'}")
        for ncessch, st, ein in KNOWN_OPERATORS:
            if STATE_FILTER not in (None, st):
                continue
            (n,) = conn.execute(
                "SELECT count(*) FROM school_matches WHERE ncessch = %s AND ein = %s AND role = 'operator'",
                (ncessch, ein),
            ).fetchone()
            if not n:
                problems.append(f"school {ncessch} ({st}) not run by {ein}")
        for ein, why in NEVER_LINKED.items():
            (n,) = conn.execute("SELECT count(*) FROM school_matches WHERE ein = %s", (ein,)).fetchone()
            if n:
                problems.append(f"{ein} linked to {n} schools ({why})")
        # An umbrella district's own nonprofit (the Charter Institute at Erskine)
        # authorizes its schools and must not be linked to them.
        (n,) = conn.execute(
            """SELECT count(*) FROM school_matches m JOIN schools s USING (ncessch) JOIN filings f USING (object_id)
               WHERE s.leaid = ANY(%s) AND similarity(upper(f.org_name), upper(s.lea_name)) >= 0.7""",
            (list(UMBRELLA_LEAIDS),),
        ).fetchone()
        if n:
            problems.append(f"{n} schools linked to their umbrella district's own nonprofit")
    return AssetCheckResult(
        passed=not problems,
        severity=AssetCheckSeverity.WARN,
        metadata={"problems": "; ".join(problems) or "none"},
    )


@asset_check(asset=school_matches, description="No charter district outside the umbrella list looks like a statewide authorizer.")
def umbrella_watch(pg: PostgresResource) -> AssetCheckResult:
    with pg.connect() as conn:
        suspects = conn.execute(
            """SELECT s.st, s.lea_name, s.leaid, count(DISTINCT s.ncessch) AS schools, count(DISTINCT m.ein) AS operators
               FROM schools s JOIN school_matches m ON m.ncessch = s.ncessch AND m.role = 'operator' AND NOT m.ambiguous
               WHERE s.is_operating AND s.lea_type = '7' AND NOT (s.leaid = ANY(%s))
               GROUP BY 1, 2, 3
               HAVING count(DISTINCT s.ncessch) >= %s AND count(DISTINCT m.ein) >= %s
               ORDER BY operators DESC""",
            (list(UMBRELLA_LEAIDS), UMBRELLA_WATCH_SCHOOLS, UMBRELLA_WATCH_OPERATORS),
        ).fetchall()
    return AssetCheckResult(
        passed=not suspects,
        severity=AssetCheckSeverity.WARN,
        metadata={"suspects": "; ".join(f"{st} {name} ({leaid}): {n} schools, {k} operators"
                                        for st, name, leaid, n, k in suspects) or "none"},
    )


# Management companies the parser must name: (filer EIN, how, expected text).
KNOWN_MANAGERS = [
    ("650857507", "contractor", "ACADEMICA"),                       # Mater Academy Inc, managed by Academica
    ("364629540", "explanation", "SUCCESS ACADEMY CHARTER SCHOOLS"),  # Success NYC, managed by its network
]


@asset_check(asset=parsed_filings, description="Known management companies are named by the contractor list or Schedule O.")
def known_managers_named(pg: PostgresResource) -> AssetCheckResult:
    problems = []
    with pg.connect() as conn:
        for ein, how, text in KNOWN_MANAGERS:
            if how == "contractor":
                sql = """SELECT count(*) FROM latest_filings f JOIN filing_contractors c USING (object_id)
                         WHERE f.ein = %s AND c.is_management AND upper(c.name) LIKE %s"""
            else:
                sql = "SELECT count(*) FROM latest_filings WHERE ein = %s AND upper(mgmt_explanation) LIKE %s"
            (n,) = conn.execute(sql, (ein, f"%{text}%")).fetchone()
            if not n:
                problems.append(f"{ein}: no {how} naming {text}")
        named, delegating = conn.execute(
            """SELECT count(*) FILTER (WHERE mgmt_explanation IS NOT NULL OR EXISTS (
                          SELECT 1 FROM filing_contractors c WHERE c.object_id = f.object_id AND c.is_management)),
                      count(*)
               FROM latest_filings f WHERE mgmt_delegated"""
        ).fetchone()
    return AssetCheckResult(
        passed=not problems,
        severity=AssetCheckSeverity.WARN,
        metadata={"problems": "; ".join(problems) or "none", "delegating_returns": delegating,
                  "delegating_with_named_evidence": named},
    )
