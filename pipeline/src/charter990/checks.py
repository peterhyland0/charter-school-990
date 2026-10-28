"""Checks that fail loudly when the numbers look wrong."""

from dagster import AssetCheckResult, AssetCheckSeverity, asset_check

from .assets.bmf import bmf_candidates
from .assets.ccd import ccd_schools
from .assets.match import school_matches
from .assets.returns import SELECT_LATEST, raw_returns
from .assets.zips import zip_members
from .resources import PostgresResource
from .settings import STATE_FILTER
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
