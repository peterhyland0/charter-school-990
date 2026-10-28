"""Link schools to nonprofits whose latest return lists the same website domain."""

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..util import EXCLUDED_DOMAINS
from .ccd import ccd_schools
from .extract import parsed_filings

MATCH_SQL = """
WITH lf AS (
    SELECT * FROM latest_filings
    WHERE website_domain IS NOT NULL AND NOT (website_domain = ANY(%(excluded)s))
),
cand AS (
    SELECT s.ncessch, lf.ein, lf.object_id, s.website_domain AS domain,
           s.website AS school_website, lf.website AS filing_website
    FROM schools s
    JOIN lf ON lf.website_domain = s.website_domain
    WHERE s.is_operating
)
INSERT INTO school_matches (ncessch, ein, object_id, method, tier, evidence)
SELECT ncessch, ein, object_id, 'website_domain',
       CASE WHEN count(*) OVER (PARTITION BY ncessch) = 1 THEN 'domain_single' ELSE 'domain_multi' END,
       jsonb_build_object('domain', domain, 'school_website', school_website, 'filing_website', filing_website)
FROM cand
"""


@asset(group_name="phase0", deps=[ccd_schools, parsed_filings], description="School-to-nonprofit links by shared website domain.")
def school_matches(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        conn.execute("TRUNCATE school_matches")
        conn.execute(MATCH_SQL, {"excluded": sorted(EXCLUDED_DOMAINS)})
        tiers = dict(conn.execute("SELECT tier, count(*) FROM school_matches GROUP BY tier").fetchall())
        matched, operating = conn.execute(
            "SELECT count(DISTINCT m.ncessch), (SELECT count(*) FROM schools WHERE is_operating) FROM school_matches m"
        ).fetchone()

    return MaterializeResult(metadata={
        "schools_matched": matched,
        "operating_schools": operating,
        "unmatched": operating - matched,
        "domain_single": tiers.get("domain_single", 0),
        "domain_multi": tiers.get("domain_multi", 0),
    })
