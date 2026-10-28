"""Link schools to the nonprofits that file their returns.

Five signals are computed in SQL for every school and candidate nonprofit:
- website: the same registrable domain
- name_zip: the same 5-digit ZIP and a similar name
- name_city: the same state and city and a very similar name
- district_name: the school's charter district is named like the nonprofit
  (in many states the charter district is the legal nonprofit)
- phone: the same 10-digit phone number
- network_site: where a website can't identify one nonprofit (an umbrella site
  shared by many campus nonprofits, or the same site name on another ending),
  the in-state nonprofit on it whose name clearly matches the school's

A sixth, district_group, spreads a verified link across a charter district:
the district is one legal nonprofit, so its schools share it.

Two more come from outside the IRS data (assets/external.py):
- audit: a federal single audit names this EIN as the auditee at the
  school's name and ZIP, city or street
- holder: the state's charter-holder record (Texas, Louisiana) names a
  holder whose name closely matches the nonprofit's

A pair is verified when two or more independent signals agree and likely when
one does. district_name and district_group both come from the school's
district, so together they count once; audit compares names much as name_zip
and name_city do, so it counts once with them.
Among the nonprofits tied for a school's best tier, ordered rules decide which
one runs the school and what the others are (management, support, affiliate).
Hand corrections are applied last, so they always win and survive reruns.
Every operating school gets a status, and unmatched schools get a reason.
"""

from collections import Counter

from dagster import MaterializeResult, asset

from ..resources import PostgresResource
from ..settings import UMBRELLA_LEAIDS
from ..util import EXCLUDED_DOMAINS, GENERIC_WORDS, US_STATES
from .ccd import ccd_schools
from .external import external_links
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
# States whose charter schools are government bodies by statute, not
# nonprofits, so most have no Form 990: Michigan's public school academies are
# bodies corporate with governmental-agency status (MCL 380.501). Wisconsin
# was checked and left out: each district decides per school (Wis. Stat.
# 118.40(7)). New Mexico was left out for lack of a primary source.
PUBLIC_BODY_STATES = {"MI": "MCL 380.501"}
# A tied nonprofit whose name is this much closer to the school's name wins.
NAME_TIE_MARGIN = 0.1
# network_site: the best-named nonprofit on a shared site needs at least this
# similarity to the school's name, and must lead the next by NAME_TIE_MARGIN.
NETWORK_SITE_SIMILARITY = 0.6
# Site names shorter than this (e.g. "kipp") match too many unrelated domains
# when compared across endings.
MIN_SITE_STEM = 6
# holder: the state record's holder name against the filer's name.
HOLDER_SIMILARITY = 0.8
# An unmatched school's auditee that reports itself as local or state
# government, files no 990, and is named like one is a government body.
GOVERNMENT_NAME = (r"\m(CITY|TOWN|TOWNSHIP|VILLAGE|COUNTY|PARISH|BOROUGH|DISTRICT|ISD|USD|"
                   r"BOARD OF EDUCATION|AUTHORITY|STATE OF|UNIVERSITY|COMMONWEALTH|DEPARTMENT|GOVERNMENT)\M")
# Entities that belong to a network but don't run its schools.
SUPPORT_NAME = r"\m(FOUNDATION|FACILITY|FACILITIES|HOLDING|HOLDINGS|LENDER|REAL ESTATE|PROPERTY|PROPERTIES|BUILDING)\M"

# Names are compared on their distinctive words (name_core drops CHARTER,
# SCHOOL, ACADEMY and the like) against the filer's name and its DBA; the
# evidence records whichever of the two matched.
NAME_SIM = """CROSS JOIN LATERAL (
    SELECT similarity(s.core, f.core) AS on_name, coalesce(similarity(s.core, f.dba_core), 0) AS on_dba
) n
CROSS JOIN LATERAL (
    SELECT greatest(n.on_name, n.on_dba) AS sim,
           CASE WHEN n.on_dba > n.on_name THEN f.dba_name ELSE f.org_name END AS matched_name
) x"""
# Two names whose numbers differ (ICAHN 1 versus ICAHN 4) are different schools.
NUMS_AGREE = "(s.nums = '' OR f.nums = '' OR s.nums = f.nums)"
# A name whose only distinctive word is its city ("BROOKLYN CHARTER SCHOOL")
# or its state ("TEXAS COLLEGE PREP") says nothing about which organisation it is.
DISTINCTIVE = """(s.core <> coalesce(s.ucity, '') AND f.core <> coalesce(f.ucity, '')
    AND s.core NOT IN (SELECT core FROM states) AND f.core NOT IN (SELECT core FROM states))"""

SETUP = [
    # Cities are compared in upper case: about a quarter of returns write them
    # in mixed case, while NCES writes them in capitals.
    """CREATE TEMP TABLE lf ON COMMIT DROP AS
       SELECT *, name_core(name_key, %(generic)s) AS core, name_core(dba_key, %(generic)s) AS dba_core,
              name_nums(name_key) AS nums, upper(city) AS ucity
       FROM latest_filings""",
    "CREATE INDEX ON lf (website_domain)",
    "CREATE INDEX ON lf (zip5)",
    "CREATE INDEX ON lf (phone10)",
    "CREATE INDEX ON lf (state, ucity)",
    "CREATE INDEX ON lf USING gin (core gin_trgm_ops)",
    "ANALYZE lf",
    # Arizona's charter districts are their holders, named with the state's
    # entity ID appended ("Center for Academic Success Inc. (4191)") and
    # sometimes a DBA: compare the legal name, and the DBA as a second name.
    """CREATE TEMP TABLE ops ON COMMIT DROP AS
       SELECT *, name_core(name_key, %(generic)s) AS core,
              name_core(split_part(k.lea_key, ' DBA ', 1), %(generic)s) AS lea_core,
              name_core(nullif(split_part(k.lea_key, ' DBA ', 2), ''), %(generic)s) AS lea_dba_core,
              name_nums(name_key) AS nums, upper(lcity) AS ucity,
              leaid = ANY(%(umbrella_leaids)s) AS umbrella
       FROM schools
       CROSS JOIN LATERAL (
           SELECT CASE WHEN lea_name ~ '\\(\\d+\\)\\s*$' THEN regexp_replace(lea_name_key, ' [0-9]+$', '')
                       ELSE lea_name_key END AS lea_key
       ) k
       WHERE is_operating""",
    """CREATE TEMP TABLE states ON COMMIT DROP AS
       SELECT code, name, name_core(name, %(generic)s) AS core
       FROM unnest(%(state_codes)s::text[], %(state_names)s::text[]) AS t(code, name)""",
    "CREATE TEMP TABLE cand (ncessch text, ein text, object_id text, signal text, evidence jsonb) ON COMMIT DROP",
    # Active hand corrections for operating schools.
    """CREATE TEMP TABLE corr ON COMMIT DROP AS
       SELECT c.* FROM match_corrections c JOIN ops s USING (ncessch) WHERE c.retired_at IS NULL""",
]

SIGNALS = {
    "website": """
        WITH umbrella AS (
            SELECT website_domain FROM lf WHERE website_domain IS NOT NULL
            GROUP BY 1 HAVING count(*) > %(umbrella_filers)s
        )
        INSERT INTO cand
        SELECT s.ncessch, f.ein, f.object_id, 'website',
               jsonb_build_object('school', s.website, 'filing', f.website, 'domain', s.website_domain)
        FROM ops s JOIN lf f ON f.website_domain = s.website_domain
        WHERE NOT (s.website_domain = ANY(%(excluded)s))
          AND s.website_domain NOT IN (SELECT website_domain FROM umbrella)
          AND s.website_domain NOT IN (SELECT domain FROM district_domains)
    """,
    # Summit Academies lists one site on 21 campus nonprofits, which is too many
    # for the website signal, and some schools list summitacademies.org where
    # the returns say .com. Rank every in-state nonprofit on the same site or
    # same site name by how closely its name matches the school's; link the
    # winner only if it clearly leads and the website signal didn't already
    # link it. Measured on schools that already match: it picks the current
    # operator 192 times in 196.
    "network_site": """
        WITH umbrella AS (
            SELECT website_domain FROM lf WHERE website_domain IS NOT NULL
            GROUP BY 1 HAVING count(*) > %(umbrella_filers)s
        ),
        site AS (
            SELECT s.ncessch, s.website AS school_site, s.website_domain AS school_domain, f.ein, f.object_id,
                   f.org_name, f.website_domain AS filer_domain, similarity(s.name_key, f.name_key) AS sim
            FROM ops s JOIN lf f ON f.state = s.st
             AND (f.website_domain = s.website_domain
                  OR (split_part(f.website_domain, '.', 1) = split_part(s.website_domain, '.', 1)
                      AND length(split_part(s.website_domain, '.', 1)) >= %(min_stem)s))
            WHERE NOT (s.website_domain = ANY(%(excluded)s)) AND NOT (f.website_domain = ANY(%(excluded)s))
              AND s.website_domain NOT IN (SELECT domain FROM district_domains)
        ),
        ranked AS (
            SELECT site.*, count(*) OVER (PARTITION BY ncessch) AS n, row_number() OVER w AS pos,
                   lead(sim) OVER w AS runner_up
            FROM site WINDOW w AS (PARTITION BY ncessch ORDER BY sim DESC, ein)
        )
        INSERT INTO cand
        SELECT ncessch, ein, object_id, 'network_site',
               jsonb_build_object('school', school_site, 'filing', org_name, 'domain', filer_domain,
                                  'similarity', round(sim::numeric, 2))
        FROM ranked
        WHERE pos = 1 AND n >= 2 AND sim >= %(site_sim)s
          AND (runner_up IS NULL OR sim - runner_up >= %(name_margin)s)
          -- Only where the website signal couldn't link this nonprofit.
          AND (filer_domain <> school_domain OR school_domain IN (SELECT website_domain FROM umbrella))
    """,
    "name_zip": f"""
        INSERT INTO cand
        SELECT s.ncessch, f.ein, f.object_id, 'name_zip',
               jsonb_build_object('school', s.sch_name, 'filing', x.matched_name, 'zip', s.zip5,
                                  'similarity', round(x.sim::numeric, 2))
        FROM ops s JOIN lf f ON f.zip5 = s.zip5
        {NAME_SIM}
        WHERE x.sim >= %(zip_sim)s AND {NUMS_AGREE} AND {DISTINCTIVE}
    """,
    "name_city": f"""
        INSERT INTO cand
        SELECT s.ncessch, f.ein, f.object_id, 'name_city',
               jsonb_build_object('school', s.sch_name, 'filing', x.matched_name, 'city', s.lcity,
                                  'similarity', round(x.sim::numeric, 2))
        FROM ops s JOIN lf f ON f.state = s.st AND f.ucity = s.ucity
        {NAME_SIM}
        WHERE x.sim >= %(city_sim)s AND {NUMS_AGREE} AND {DISTINCTIVE}
    """,
    # Statewide umbrella districts are authorizers, not networks, so their
    # names say nothing about who runs a school. A district named for a
    # university (University of Texas University Charter School) matches the
    # university's law school and foundations, which don't run schools.
    "district_name": """
        WITH d AS (
            SELECT DISTINCT o.st, o.lea_name, o.lea_name_key, c.lea_core FROM ops o
            CROSS JOIN LATERAL unnest(ARRAY[o.lea_core, o.lea_dba_core]) AS c(lea_core)
            WHERE o.lea_type = '7' AND NOT o.umbrella AND c.lea_core IS NOT NULL AND c.lea_core <> ''
              AND c.lea_core NOT IN (SELECT core FROM states)
        ),
        dm AS (
            SELECT d.st, d.lea_name_key, d.lea_name, f.ein, f.object_id, f.org_name,
                   similarity(d.lea_core, f.core) AS sim
            FROM d JOIN lf f ON f.state = d.st AND f.core %% d.lea_core
            WHERE f.org_name !~* '\\mUNIVERSITY OF\\M'
        )
        INSERT INTO cand
        SELECT s.ncessch, dm.ein, dm.object_id, 'district_name',
               jsonb_build_object('district', dm.lea_name, 'filing', dm.org_name,
                                  'similarity', round(dm.sim::numeric, 2))
        FROM ops s JOIN dm ON dm.st = s.st AND dm.lea_name_key = s.lea_name_key
        WHERE dm.sim >= %(district_sim)s
          -- Skip the school that shares its district's name: for it this would
          -- only repeat the school-name comparison as a second signal.
          AND similarity(s.lea_name_key, s.name_key) < 0.9
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
    # A federal single audit filed under this EIN at the school's name and
    # place (matched in assets/external.py).
    "audit": """
        INSERT INTO cand
        SELECT x.ncessch, f.ein, f.object_id, 'audit',
               jsonb_build_object('auditee', x.holder_name, 'entity_type', x.entity_type) || x.detail
        FROM external_links x JOIN ops s USING (ncessch) JOIN lf f ON f.ein = x.ein
        WHERE x.source = 'fac'
    """,
    # The state's record of the charter's legal holder, against in-state filers.
    "holder": """
        WITH h AS (
            SELECT DISTINCT x.ncessch, x.source, x.holder_name, s.st,
                   name_core(k, %(generic)s) AS core, name_nums(k) AS nums
            FROM external_links x JOIN ops s USING (ncessch), unnest(x.holder_keys) AS k
            WHERE x.source IN ('tx_tea', 'la_doe')
        )
        INSERT INTO cand
        SELECT h.ncessch, f.ein, f.object_id, 'holder',
               jsonb_build_object('source', h.source, 'holder', h.holder_name, 'filing', f.org_name,
                                  'similarity', round(similarity(h.core, f.core)::numeric, 2))
        FROM h JOIN lf f ON f.state = h.st AND f.core %% h.core
        WHERE h.core <> '' AND h.core NOT IN (SELECT core FROM states)
          AND similarity(h.core, f.core) >= %(holder_sim)s
          AND (h.nums = '' OR f.nums = '' OR h.nums = f.nums)
          AND f.org_name !~* '\\mUNIVERSITY OF\\M'
    """,
}

# Pairs and their tiers from the candidate signals. Run twice: once for the
# five direct signals, then again after district_group is added. Hand
# corrections that remove a link go first, so the removed nonprofit neither
# spreads across a district nor takes part in deciding roles.
BUILD_PAIRS = [
    """DELETE FROM cand USING corr c
       WHERE cand.ncessch = c.ncessch AND (c.action = 'none' OR (c.action = 'unlink' AND cand.ein = c.ein))""",
    # An umbrella district's own nonprofit authorizes its schools; it doesn't
    # run them, even when a school lists its phone number.
    """DELETE FROM cand USING ops s, lf f
       WHERE cand.ncessch = s.ncessch AND cand.object_id = f.object_id
         AND s.umbrella AND similarity(f.core, s.lea_core) >= %(district_sim)s""",
    "DROP TABLE IF EXISTS pairs",
    """CREATE TEMP TABLE pairs ON COMMIT DROP AS
       SELECT ncessch, ein, min(object_id) AS object_id,
              array_agg(DISTINCT signal ORDER BY signal) AS signals,
              jsonb_object_agg(signal, evidence) AS evidence,
              0 AS strength
       FROM cand GROUP BY ncessch, ein""",
    # The same ZIP implies the same city, so don't count the name twice.
    """UPDATE pairs SET signals = array_remove(signals, 'name_city')
       WHERE 'name_zip' = ANY(signals) AND 'name_city' = ANY(signals)""",
    # Independent evidence: the two district signals count as one.
    """UPDATE pairs SET strength = (
           SELECT count(DISTINCT CASE WHEN x IN ('district_name', 'district_group') THEN 'district'
                                      WHEN x IN ('name_zip', 'name_city', 'audit') THEN 'name' ELSE x END)
           FROM unnest(signals) AS x)""",
]

# A charter district is one legal nonprofit, so a nonprofit verified for one
# of its schools is evidence for the others. Only in-state, non-support
# nonprofits spread: IDEA's Arkansas affiliate is verified for three Texas
# schools by phone, which says nothing about the other 130.
DISTRICT_GROUP = """
    WITH verified AS (
        SELECT s.leaid, s.lea_name, p.ein, p.object_id, count(*) AS n
        FROM pairs p JOIN ops s USING (ncessch) JOIN lf f ON f.object_id = p.object_id
        WHERE p.strength >= 2 AND s.lea_type = '7' AND NOT s.umbrella
          AND f.state = s.st AND f.org_name !~* %(support_name)s
        GROUP BY 1, 2, 3, 4
    )
    INSERT INTO cand
    SELECT s.ncessch, v.ein, v.object_id, 'district_group',
           jsonb_build_object('district', v.lea_name, 'verified_schools', v.n - coalesce(own.self, 0))
    FROM ops s JOIN verified v ON v.leaid = s.leaid
    LEFT JOIN LATERAL (
        SELECT 1 AS self FROM pairs p
        WHERE p.ncessch = s.ncessch AND p.ein = v.ein AND p.strength >= 2
    ) own ON true
    -- At least one verified school other than this one.
    WHERE v.n - coalesce(own.self, 0) >= 1
"""

# Role facts for every pair, then ordered rules among the pairs tied for each
# school's best tier. Each rule only sets pairs aside when some tied pair
# survives it, so a school never loses its last candidate.
ROLES = [
    """CREATE TEMP TABLE r ON COMMIT DROP AS
        SELECT p.*, CASE WHEN p.strength >= 2 THEN 'verified' ELSE 'likely' END AS tier,
               p.strength >= 2 AS strong,
               -- In the school's state, and not named for another state. A
               -- state whose name sits inside the school's own state's name
               -- (VIRGINIA in WEST VIRGINIA) doesn't count, and nor does
               -- WASHINGTON, which in names is usually a person or DC.
               f.state = s.st AND NOT EXISTS (
                   SELECT 1 FROM states x JOIN states own ON own.code = s.st
                   WHERE x.code <> s.st AND x.name <> 'WASHINGTON' AND position(x.name IN own.name) = 0
                     AND (' ' || f.name_key || ' ') LIKE ('%% ' || x.name || ' %%')
               ) AS in_state,
               f.org_name ~* %(support_name)s AS support,
               f.mgmt_delegated AS delegates,
               similarity(s.name_key, f.name_key) AS name_sim,
               NULL::text AS role
        FROM pairs p JOIN ops s USING (ncessch) JOIN lf f ON f.object_id = p.object_id""",
    # The rules below look up other pairs of the same school.
    "CREATE INDEX ON r (ncessch)",
    """UPDATE r SET role = 'other'
       WHERE NOT strong AND EXISTS (SELECT 1 FROM r x WHERE x.ncessch = r.ncessch AND x.strong)""",
    """UPDATE r SET role = 'affiliate'
       WHERE role IS NULL AND NOT in_state
         AND EXISTS (SELECT 1 FROM r x WHERE x.ncessch = r.ncessch AND x.role IS NULL AND x.in_state)""",
    # The remaining rules only choose among nonprofits in the school's own
    # state. When every candidate is out of state (a network's filers based
    # elsewhere), the school's own operator isn't on file: leave it ambiguous.
    """CREATE TEMP TABLE home ON COMMIT DROP AS
       SELECT DISTINCT ncessch FROM r WHERE role IS NULL AND in_state""",
    """UPDATE r SET role = 'support'
       WHERE role IS NULL AND support AND ncessch IN (SELECT ncessch FROM home)
         AND EXISTS (SELECT 1 FROM r x WHERE x.ncessch = r.ncessch AND x.role IS NULL AND NOT x.support)""",
    # Some tied nonprofits delegate management and some don't: the ones that
    # delegate run schools, and the ones they delegate to manage them. Not when
    # the non-delegating one is clearly the school's namesake and the
    # delegating ones aren't (ASU Prep Academy Inc versus a university entity
    # that shares asu.edu): then the delegating ones are unrelated.
    """UPDATE r SET role = 'management'
       WHERE role IS NULL AND delegates IS FALSE AND ncessch IN (SELECT ncessch FROM home)
         AND EXISTS (SELECT 1 FROM r x WHERE x.ncessch = r.ncessch AND x.role IS NULL AND x.delegates
                     AND x.name_sim >= r.name_sim - %(name_margin)s)""",
    # A nonprofit the school points at directly (website, phone, name) beats
    # one that only shares a name with the school's district, or that only
    # filed a federal audit under a name like the school's (the Orlando
    # Science Center against Orlando Science Elementary's own website).
    """UPDATE r SET role = 'related'
       WHERE role IS NULL AND signals <@ %(indirect)s AND ncessch IN (SELECT ncessch FROM home)
         AND EXISTS (SELECT 1 FROM r x WHERE x.ncessch = r.ncessch AND x.role IS NULL
                     AND NOT x.signals <@ %(indirect)s)""",
    """WITH ranked AS (
           SELECT ncessch, ein, name_sim,
                  row_number() OVER (PARTITION BY ncessch ORDER BY name_sim DESC, ein) AS pos,
                  lead(name_sim) OVER (PARTITION BY ncessch ORDER BY name_sim DESC, ein) AS runner_up
           FROM r WHERE role IS NULL AND ncessch IN (SELECT ncessch FROM home)
       ),
       decided AS (
           SELECT ncessch, ein FROM ranked
           WHERE pos = 1 AND runner_up IS NOT NULL AND name_sim - runner_up >= %(name_margin)s
       )
       UPDATE r SET role = 'related'
       FROM decided d
       WHERE r.ncessch = d.ncessch AND r.role IS NULL AND r.ein <> d.ein""",
    "UPDATE r SET role = 'operator' WHERE role IS NULL",
    """INSERT INTO school_matches (ncessch, ein, object_id, method, tier, evidence, signals, ambiguous, role, source)
       SELECT ncessch, ein, object_id, 'signals', tier, evidence, signals,
              count(*) FILTER (WHERE role = 'operator') OVER (PARTITION BY ncessch) > 1, role, 'auto'
       FROM r""",
]

# Hand links, applied after roles so they always win. (Unlinks and "no
# nonprofit" were applied to the candidates.) A link to a nonprofit the
# matcher didn't find is added with its latest filing.
LINK_CORRECTIONS = [
    """INSERT INTO school_matches (ncessch, ein, object_id, method, tier, evidence, signals, ambiguous, role, source)
       SELECT c.ncessch, c.ein, f.object_id, 'correction', 'verified', '{}', '{}', false, 'operator', 'corrected'
       FROM corr c JOIN lf f ON f.ein = c.ein
       WHERE c.action = 'link'
       ON CONFLICT (ncessch, ein) DO NOTHING""",
    # The hand-linked nonprofit runs the school; any other operator is demoted.
    """UPDATE school_matches m
       SET role = CASE WHEN c.ein = m.ein THEN 'operator' ELSE 'related' END,
           tier = CASE WHEN c.ein = m.ein THEN 'verified' ELSE m.tier END,
           source = CASE WHEN c.ein = m.ein THEN 'corrected' ELSE m.source END,
           evidence = CASE WHEN c.ein = m.ein
                           THEN m.evidence || jsonb_build_object('correction', jsonb_build_object(
                                'id', c.id, 'note', c.note, 'by', c.created_by, 'at', c.created_at))
                           ELSE m.evidence END
       FROM corr c
       WHERE c.action = 'link' AND m.ncessch = c.ncessch AND (m.ein = c.ein OR m.role = 'operator')""",
    # Recompute ambiguity for schools a correction touched.
    """UPDATE school_matches m SET ambiguous = x.n > 1
       FROM (SELECT ncessch, count(*) FILTER (WHERE role = 'operator') AS n FROM school_matches GROUP BY 1) x
       WHERE x.ncessch = m.ncessch AND m.ncessch IN (SELECT ncessch FROM corr)""",
]

BUILD_STATUS = """
    INSERT INTO school_status (ncessch, status, n_candidates, reason, detail, corrected)
    SELECT s.ncessch,
           coalesce(m.best, 'unmatched'),
           coalesce(m.n, 0),
           CASE
               WHEN m.best IS NOT NULL THEN NULL
               WHEN c.ncessch IS NOT NULL THEN 'corrected_none'
               WHEN s.st = ANY(%(district_states)s) THEN 'district_governed_state'
               -- The IRS filing requirement code says why a registered
               -- nonprofit has no 990 or 990-EZ on file.
               WHEN pf.filing_req_cd = '02' THEN 'postcard_filer'
               WHEN pf.filing_req_cd IN ('00', '06', '07', '13', '14') THEN 'not_required_to_file'
               WHEN pf.ein IS NOT NULL THEN 'no_recent_return'
               WHEN pb.law IS NOT NULL THEN 'public_body_state'
               WHEN ga.ein IS NOT NULL THEN 'government_auditee'
               -- The school's own site is its regular district's: probably
               -- run by the district, with no separate nonprofit.
               WHEN s.website_domain IN (SELECT domain FROM district_domains) THEN 'uses_district_website'
               WHEN s.lea_type IS DISTINCT FROM '7' THEN 'inside_regular_district'
               ELSE 'no_candidate_found'
           END,
           jsonb_strip_nulls(jsonb_build_object(
               'has_website', s.website_domain IS NOT NULL,
               'nonprofit_ein', pf.ein, 'nonprofit_name', pf.name, 'filing_req_cd', pf.filing_req_cd,
               'correction_note', c.note, 'state_law', pb.law,
               'auditee_name', ga.holder_name, 'auditee_ein', ga.ein)),
           EXISTS (SELECT 1 FROM corr x WHERE x.ncessch = s.ncessch)
    FROM ops s
    LEFT JOIN (
        SELECT ncessch, CASE WHEN bool_or(tier = 'verified') THEN 'verified' ELSE 'likely' END AS best,
               count(*) AS n
        FROM school_matches GROUP BY ncessch
    ) m USING (ncessch)
    LEFT JOIN corr c ON c.ncessch = s.ncessch AND c.action = 'none'
    LEFT JOIN unnest(%(public_body_codes)s::text[], %(public_body_laws)s::text[]) AS pb(code, law) ON pb.code = s.st
    LEFT JOIN LATERAL (
        -- Audited as part of a city, county or school district that files no 990.
        SELECT x.ein, x.holder_name FROM external_links x
        WHERE m.best IS NULL AND x.ncessch = s.ncessch AND x.source = 'fac'
          AND x.entity_type IN ('local', 'state') AND x.holder_name ~* %(government_name)s
          AND NOT EXISTS (SELECT 1 FROM lf WHERE lf.ein = x.ein)
        ORDER BY x.detail->>'audit_year' DESC
        LIMIT 1
    ) ga ON true
    LEFT JOIN LATERAL (
        -- A registered nonprofit that matches by name and ZIP but has no 990 or
        -- 990-EZ in the 2025 or 2026 index.
        SELECT b.ein, b.name, b.filing_req_cd FROM bmf_orgs b
        WHERE m.best IS NULL AND c.ncessch IS NULL AND b.zip5 = s.zip5
          AND NOT EXISTS (SELECT 1 FROM irs_index i WHERE i.ein = b.ein)
          AND greatest(similarity(s.core, name_core(b.name_key, %(generic)s)),
                       coalesce(similarity(s.core, name_core(b.sort_name_key, %(generic)s)), 0)) >= %(zip_sim)s
          AND (s.nums = '' OR name_nums(b.name_key) = '' OR s.nums = name_nums(b.name_key))
          AND s.core <> coalesce(s.ucity, '') AND s.core NOT IN (SELECT core FROM states)
        ORDER BY similarity(s.core, name_core(b.name_key, %(generic)s)) DESC
        LIMIT 1
    ) pf ON true
"""

PARAMS = {
    "generic": list(GENERIC_WORDS),
    "state_codes": list(US_STATES),
    "state_names": list(US_STATES.values()),
    "umbrella_leaids": list(UMBRELLA_LEAIDS),
    "excluded": sorted(EXCLUDED_DOMAINS),
    "umbrella_filers": UMBRELLA_DOMAIN_FILERS,
    "shared_phone": SHARED_PHONE_FILERS,
    "zip_sim": NAME_ZIP_SIMILARITY,
    "city_sim": NAME_CITY_SIMILARITY,
    "district_sim": DISTRICT_NAME_SIMILARITY,
    "district_states": DISTRICT_GOVERNED_STATES,
    "public_body_codes": list(PUBLIC_BODY_STATES),
    "public_body_laws": list(PUBLIC_BODY_STATES.values()),
    "support_name": SUPPORT_NAME,
    "name_margin": NAME_TIE_MARGIN,
    "indirect": ["district_name", "district_group", "audit"],
    "site_sim": NETWORK_SITE_SIMILARITY,
    "min_stem": MIN_SITE_STEM,
    "holder_sim": HOLDER_SIMILARITY,
    "government_name": GOVERNMENT_NAME,
}


def _run(conn, statements) -> None:
    for sql in statements:
        # Pass parameters only to statements that use them.
        conn.execute(sql, PARAMS if "%(" in sql else None)


@asset(group_name="pipeline", deps=[ccd_schools, parsed_filings, external_links], description="School-to-nonprofit links from eight signals, each nonprofit's role, hand corrections, and a status for every school.")
def school_matches(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        # DELETE, not TRUNCATE: the live site keeps showing the previous matches
        # until this transaction commits the new ones.
        conn.execute("DELETE FROM school_matches")
        conn.execute("DELETE FROM school_status")
        _run(conn, SETUP)
        # Lets the gin index serve the district-name % (similarity) operator.
        conn.execute(f"SET LOCAL pg_trgm.similarity_threshold = {DISTRICT_NAME_SIMILARITY}")
        signal_rows = {}
        for name, sql in SIGNALS.items():
            signal_rows[name] = conn.execute(sql, PARAMS if "%(" in sql else None).rowcount
        _run(conn, BUILD_PAIRS)
        signal_rows["district_group"] = conn.execute(DISTRICT_GROUP, PARAMS).rowcount
        _run(conn, BUILD_PAIRS)
        _run(conn, ROLES)
        _run(conn, LINK_CORRECTIONS)
        conn.execute(BUILD_STATUS, PARAMS)

        statuses = dict(conn.execute("SELECT status, count(*) FROM school_status GROUP BY 1").fetchall())
        reasons = dict(conn.execute(
            "SELECT reason, count(*) FROM school_status WHERE status = 'unmatched' GROUP BY 1"
        ).fetchall())
        roles = dict(conn.execute("SELECT role, count(*) FROM school_matches GROUP BY 1").fetchall())
        (ambiguous,) = conn.execute("SELECT count(DISTINCT ncessch) FROM school_matches WHERE ambiguous").fetchone()
        (corrections,) = conn.execute("SELECT count(*) FROM corr").fetchone()
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
        "corrections_applied": corrections,
    }
    metadata.update({f"reason_{k}": v for k, v in reasons.items()})
    metadata.update({f"role_{k}": v for k, v in roles.items()})
    metadata.update({f"pairs_with_{k}": v for k, v in signal_use.items()})
    metadata.update({f"candidate_rows_{k}": v for k, v in signal_rows.items()})
    return MaterializeResult(metadata=metadata)
