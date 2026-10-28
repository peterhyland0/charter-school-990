"""Checks that fail loudly when the numbers look wrong."""

from dagster import AssetCheckResult, AssetCheckSeverity, asset_check

from .assets.bmf import bmf_candidates
from .assets.ccd import ccd_schools
from .assets.returns import SELECT_LATEST, raw_returns
from .assets.zips import zip_members
from .resources import PostgresResource

# New York, CCD SY 2025-26 preliminary file.
EXPECTED_OPERATING = 352
# Success Academy's FY2025 return: labelled 05A in the index, stored in 05B.zip.
SUCCESS_OBJECT_ID = "202641279349300844"
# Known New York network filers, reported rather than enforced.
KNOWN_NETWORK_EINS = {
    "205298861": "Success Academy Charter Schools",
    "311488698": "Uncommon Schools Inc",
    "203505981": "Uncommon NYC Charter Schools",
    "203971209": "KIPP New York",
    "651203744": "Achievement First Inc",
}
MAX_FAILURE_RATE = 0.05


@asset_check(asset=ccd_schools, description="Operating charter count matches the CCD file.")
def operating_school_count(pg: PostgresResource) -> AssetCheckResult:
    with pg.connect() as conn:
        (n,) = conn.execute("SELECT count(*) FROM schools WHERE is_operating").fetchone()
    return AssetCheckResult(passed=n == EXPECTED_OPERATING, metadata={"operating": n, "expected": EXPECTED_OPERATING})


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
