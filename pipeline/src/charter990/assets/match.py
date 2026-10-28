"""Link schools to the nonprofits that file their returns.

Five signals are computed in SQL for every school and candidate nonprofit:
- website: the same registrable domain
- name_zip: the same 5-digit ZIP and a similar name
- name_city: the same state and city and a very similar name
- district_name: the school's charter district is named like the nonprofit
  (in many states the charter district is the legal nonprofit)
- phone: the same 10-digit phone number

A pair is verified when two or more signals agree and likely when one does.
Every operating school gets a status, and unmatched schools get a reason.
"""

from collections import Counter

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..util import EXCLUDED_DOMAINS
from .ccd import ccd_schools
from .extract import parsed_filings

# Tuned on a New York spot check: below these, name-only pairs were mostly wrong.
NAME_ZIP_SIMILARITY = 0.6
NAME_CITY_SIMILARITY = 0.8
DISTRICT_NAME_SIMILARITY = 0.7
# A domain on more than this many nonprofits' latest returns is an umbrella
# site (a network or service provider), not evidence for any one of them.
UMBRELLA_DOMAIN_FILERS = 10
# Likewise a phone number on more than this many returns is a shared office line.
SHARED_PHONE_FILERS = 5
# States whose charter schools are governed by school districts by law, so
# most have no separate nonprofit (NEPC; CCD shows no charter districts there).
DISTRICT_GOVERNED_STATES = ["AK", "KS", "MD", "VA"]

# Names are compared on their distinctive words (name_core drops CHARTER,
# SCHOOL, ACADEMY and the like) against the filer's name or DBA.
SIM = "greatest(similarity({a}, f.core), coalesce(similarity({a}, f.dba_core), 0))"
# Two names whose numbers differ (ICAHN 1 versus ICAHN 4) are different schools.
NUMS_AGREE = "(s.nums = '' OR f.nums = '' OR s.nums = f.nums)"
# A name whose only distinctive word is its city ("BROOKLYN CHARTER SCHOOL")
# says nothing about which organisation it is.
DISTINCTIVE = "(s.core <> upper(s.lcity) AND f.core <> coalesce(f.city, ''))"

SETUP = [
    """CREATE TEMP TABLE lf ON COMMIT DROP AS
       SELECT *, name_core(name_key) AS core, name_core(dba_key) AS dba_core, name_nums(name_key) AS nums
       FROM latest_filings""",
    "CREATE INDEX ON lf (website_domain)",
    "CREATE INDEX ON lf (zip5)",
    "CREATE INDEX ON lf (phone10)",
    "CREATE INDEX ON lf (state, city)",
    "CREATE INDEX ON lf USING gin (core gin_trgm_ops)",
    "ANALYZE lf",
    """CREATE TEMP TABLE ops ON COMMIT DROP AS
       SELECT *, name_core(name_key) AS core, name_core(lea_name_key) AS lea_core, name_nums(name_key) AS nums
       FROM schools WHERE is_operating""",
    "CREATE TEMP TABLE cand (ncessch text, ein text, object_id text, signal text, evidence jsonb) ON COMMIT DROP",
]

SIGNALS = {
    "website": """
        WITH umbrella AS (
            SELECT website_domain FROM lf WHERE website_domain IS NOT NULL
            GROUP BY 1 HAVING count(*) > %(umbrella)s
        )
        INSERT INTO cand
        SELECT s.ncessch, f.ein, f.object_id, 'website',
               jsonb_build_object('school', s.website, 'filing', f.website, 'domain', s.website_domain)
        FROM ops s JOIN lf f ON f.website_domain = s.website_domain
        WHERE NOT (s.website_domain = ANY(%(excluded)s))
          AND s.website_domain NOT IN (SELECT website_domain FROM umbrella)
          AND s.website_domain NOT IN (SELECT domain FROM district_domains)
    """,
    "name_zip": f"""
        INSERT INTO cand
        SELECT s.ncessch, f.ein, f.object_id, 'name_zip',
               jsonb_build_object('school', s.sch_name, 'filing', f.org_name, 'zip', s.zip5,
                                  'similarity', round(x.sim::numeric, 2))
        FROM ops s JOIN lf f ON f.zip5 = s.zip5
        CROSS JOIN LATERAL (SELECT {SIM.format(a='s.core')} AS sim) x
        WHERE x.sim >= %(zip_sim)s AND {NUMS_AGREE} AND {DISTINCTIVE}
    """,
    "name_city": f"""
        INSERT INTO cand
        SELECT s.ncessch, f.ein, f.object_id, 'name_city',
               jsonb_build_object('school', s.sch_name, 'filing', f.org_name, 'city', s.lcity,
                                  'similarity', round(x.sim::numeric, 2))
        FROM ops s JOIN lf f ON f.state = s.st AND f.city = upper(s.lcity)
        CROSS JOIN LATERAL (SELECT {SIM.format(a='s.core')} AS sim) x
        WHERE x.sim >= %(city_sim)s AND {NUMS_AGREE} AND {DISTINCTIVE}
    """,
    # Only charter districts whose name differs from the school's own name;
    # otherwise this would just repeat the school-name comparison.
    "district_name": """
        WITH d AS (
            SELECT DISTINCT st, lea_name, lea_name_key, lea_core FROM ops
            WHERE lea_type = '7' AND lea_core IS NOT NULL
              AND similarity(lea_name_key, name_key) < 0.9
        ),
        dm AS (
            SELECT d.st, d.lea_name_key, d.lea_name, f.ein, f.object_id, f.org_name,
                   similarity(d.lea_core, f.core) AS sim
            FROM d JOIN lf f ON f.state = d.st AND f.core %% d.lea_core
        )
        INSERT INTO cand
        SELECT s.ncessch, dm.ein, dm.object_id, 'district_name',
               jsonb_build_object('district', dm.lea_name, 'filing', dm.org_name,
                                  'similarity', round(dm.sim::numeric, 2))
        FROM ops s JOIN dm ON dm.st = s.st AND dm.lea_name_key = s.lea_name_key
        WHERE dm.sim >= %(district_sim)s
    """,
    "phone": f"""
        WITH shared AS (
            SELECT phone10 FROM lf WHERE phone10 IS NOT NULL
            GROUP BY 1 HAVING count(*) > %(shared_phone)s
        )
        INSERT INTO cand
        SELECT s.ncessch, f.ein, f.object_id, 'phone', jsonb_build_object('phone', s.phone10)
        FROM ops s JOIN lf f ON f.phone10 = s.phone10
        WHERE {NUMS_AGREE} AND s.phone10 NOT IN (SELECT phone10 FROM shared)
    """,
}

BUILD_MATCHES = """
    CREATE TEMP TABLE pairs ON COMMIT DROP AS
    SELECT ncessch, ein, min(object_id) AS object_id,
           array_agg(DISTINCT signal ORDER BY signal) AS signals,
           jsonb_object_agg(signal, evidence) AS evidence
    FROM cand GROUP BY ncessch, ein;

    -- The same ZIP implies the same city, so don't count the name twice.
    UPDATE pairs SET signals = array_remove(signals, 'name_city')
    WHERE 'name_zip' = ANY(signals) AND 'name_city' = ANY(signals);

    INSERT INTO school_matches (ncessch, ein, object_id, method, tier, evidence, signals, ambiguous)
    WITH t AS (
        SELECT p.*, CASE WHEN cardinality(signals) >= 2 THEN 'verified' ELSE 'likely' END AS tier,
               CASE WHEN cardinality(signals) >= 2 THEN 1 ELSE 2 END AS rank
        FROM pairs p
    ),
    r AS (SELECT t.*, min(rank) OVER (PARTITION BY ncessch) AS best_rank FROM t)
    SELECT ncessch, ein, object_id, 'signals', tier, evidence, signals,
           count(*) FILTER (WHERE rank = best_rank) OVER (PARTITION BY ncessch) > 1
    FROM r;
"""

BUILD_STATUS = """
    INSERT INTO school_status (ncessch, status, n_candidates, reason, detail)
    SELECT s.ncessch,
           coalesce(m.best, 'unmatched'),
           coalesce(m.n, 0),
           CASE
               WHEN m.best IS NOT NULL THEN NULL
               WHEN s.st = ANY(%(district_states)s) THEN 'district_governed_state'
               WHEN pf.ein IS NOT NULL THEN 'filer_without_efile'
               WHEN s.lea_type IS DISTINCT FROM '7' THEN 'inside_regular_district'
               ELSE 'no_candidate_found'
           END,
           jsonb_strip_nulls(jsonb_build_object(
               'has_website', s.website_domain IS NOT NULL,
               'postcard_ein', pf.ein, 'postcard_name', pf.name))
    FROM ops s
    LEFT JOIN (
        SELECT ncessch, CASE WHEN bool_or(tier = 'verified') THEN 'verified' ELSE 'likely' END AS best,
               count(*) AS n
        FROM school_matches GROUP BY ncessch
    ) m USING (ncessch)
    LEFT JOIN LATERAL (
        -- A nonprofit that matches by name and ZIP but filed no 990 or 990-EZ:
        -- most likely a small organisation that files only the 990-N postcard.
        SELECT b.ein, b.name FROM bmf_orgs b
        WHERE m.best IS NULL AND b.zip5 = s.zip5
          AND NOT EXISTS (SELECT 1 FROM irs_index i WHERE i.ein = b.ein)
          AND greatest(similarity(s.core, name_core(b.name_key)), coalesce(similarity(s.core, name_core(b.sort_name_key)), 0)) >= %(zip_sim)s
          AND (s.nums = '' OR name_nums(b.name_key) = '' OR s.nums = name_nums(b.name_key))
          AND s.core <> upper(s.lcity)
        ORDER BY similarity(s.core, name_core(b.name_key)) DESC
        LIMIT 1
    ) pf ON true
"""

PARAMS = {
    "excluded": sorted(EXCLUDED_DOMAINS),
    "umbrella": UMBRELLA_DOMAIN_FILERS,
    "shared_phone": SHARED_PHONE_FILERS,
    "zip_sim": NAME_ZIP_SIMILARITY,
    "city_sim": NAME_CITY_SIMILARITY,
    "district_sim": DISTRICT_NAME_SIMILARITY,
    "district_states": DISTRICT_GOVERNED_STATES,
}


@asset(group_name="pipeline", deps=[ccd_schools, parsed_filings], description="School-to-nonprofit links from five signals, plus a status for every school.")
def school_matches(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        conn.execute("TRUNCATE school_matches, school_status")
        for statement in SETUP:
            conn.execute(statement)
        # Lets the gin index serve the district-name % (similarity) operator.
        conn.execute(f"SET LOCAL pg_trgm.similarity_threshold = {DISTRICT_NAME_SIMILARITY}")
        signal_rows = {}
        for name, sql in SIGNALS.items():
            # Pass parameters only to statements that use them.
            signal_rows[name] = conn.execute(sql, PARAMS if "%(" in sql else None).rowcount
        conn.execute(BUILD_MATCHES)
        conn.execute(BUILD_STATUS, PARAMS)

        statuses = dict(conn.execute("SELECT status, count(*) FROM school_status GROUP BY 1").fetchall())
        reasons = dict(conn.execute(
            "SELECT reason, count(*) FROM school_status WHERE status = 'unmatched' GROUP BY 1"
        ).fetchall())
        (ambiguous,) = conn.execute("SELECT count(DISTINCT ncessch) FROM school_matches WHERE ambiguous").fetchone()
        signal_use = Counter(dict(conn.execute(
            "SELECT unnest(signals), count(*) FROM school_matches GROUP BY 1"
        ).fetchall()))

    operating = sum(statuses.values())
    matched = statuses.get("verified", 0) + statuses.get("likely", 0)
    metadata = {
        "operating_schools": operating,
        "verified": statuses.get("verified", 0),
        "likely": statuses.get("likely", 0),
        "unmatched": statuses.get("unmatched", 0),
        "match_rate": round(matched / operating, 4) if operating else 0.0,
        "ambiguous_schools": ambiguous,
    }
    metadata.update({f"reason_{k}": v for k, v in reasons.items()})
    metadata.update({f"pairs_with_{k}": v for k, v in signal_use.items()})
    metadata.update({f"candidate_rows_{k}": v for k, v in signal_rows.items()})
    return MaterializeResult(metadata=metadata)
