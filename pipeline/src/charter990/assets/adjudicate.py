"""Decide the schools the matcher leaves open, two ways, from the same evidence.

In scope:
- ambiguous schools: several nonprofits tie as operator, and the candidates
  are those tied operators;
- unmatched schools, except where state law means most have no nonprofit
  (district_governed_state, public_body_state) or a hand correction says
  none: up to five in-state nonprofits from the latest returns whose core
  name is similar (0.3 or more), whose website has the same site name, or
  that share the ZIP with a similarity of 0.2 or more.

Every candidate gets the same features, and both models see all of them:
- the scored model, a hand-weighted sum (WEIGHTS) that picks the top
  candidate only when it clears SCORE_FLOOR and leads the next by
  SCORE_MARGIN, and otherwise answers none. It always runs.
- Jev, TypeSafe AI's decision model (charter990.jev), given the school's and
  candidates' facts and features as its state, and two questions: whether
  any candidate runs the school, and which one. It answers with
  probabilities only; code picks (JEV_ANY_AT, JEV_PICK_AT), and the stored
  reason is written here from the evidence. It runs only when JEV_API_KEY (or
  TYPESAFE_API_KEY) is set. Answers are stored under a hash of the state and
  the question, so a rerun asks only about new or changed cases.

Shadow mode: decisions go to the adjudications table only. With
ADJUDICATOR=scored or ADJUDICATOR=jev, that method's picks are applied:
an ambiguous school's pick becomes its operator and the other tied operators
become related; an unmatched school's pick is linked as likely, with signal
adjudicated. Hand corrections always win. Applied picks are undone at the
start of every run, so unsetting ADJUDICATOR restores the matcher's answer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from dagster import MaterializeResult, asset

from .. import jev
from ..resources import PostgresResource
from ..util import is_support_org, name_key, named_in, street_key
from .match import PARAMS, SETUP, school_matches

# Unmatched schools in these states are left alone: by law most have no nonprofit.
OUT_OF_SCOPE_REASONS = ["district_governed_state", "public_body_state", "corrected_none"]
# Unmatched-school candidates.
CANDIDATE_NAME_SIMILARITY = 0.3
CANDIDATE_ZIP_SIMILARITY = 0.2
MAX_CANDIDATES = 5
# The authorizer counts as naming the candidate at this similarity of full names.
AUTHORIZER_SIMILARITY = 0.6
# A return this small rarely belongs to a nonprofit that runs a school.
SMALL_REVENUE = 250_000
# The candidate with the most revenue, when it has at least this many times
# the next one's: an operator beside its own foundation or a small related entity.
REVENUE_LEAD = 2.0
# Officers who run a school, not only sit on a board.
SCHOOL_LEADER_TITLE = re.compile(
    r"\b(PRINCIPAL|HEAD OF SCHOOL|SUPERINTENDENT|EXECUTIVE DIRECTOR|SCHOOL DIRECTOR|SCHOOL LEADER|"
    r"DIRECTOR OF SCHOOLS?|CAMPUS DIRECTOR|ADMINISTRATOR)\b"
)

# The scored model. Hand-set, not fitted: each weight says how strongly one
# fact points at the school's own nonprofit, and the floor needs the
# equivalent of two independent facts or a near-identical name in the state.
# Bump SCORED_MODEL when changing a weight, so stored decisions are redone.
SCORED_MODEL = "scored-v1"  # weights, floor, margin and corroboration rule
WEIGHTS = {
    "same_website": 3.0,             # the school's own registrable domain on the return
    "same_stem": 1.0,                # same site name on another ending (school.org vs school.com)
    "same_phone": 2.0,
    "same_street": 2.0,              # same street number and street name
    "title_names_school": 2.0,       # an officer is "Head of School, <this campus>"
    "schedule_o_names_school": 1.5,  # Schedule O names the school
    "same_zip": 0.5,
    "same_city": 0.25,
    "in_state": 1.0,
    "mgmt_delegated": 0.5,           # boards that run schools delegate to a CMO; CMOs don't
    "authorizer_names_candidate": 0.5,  # weak: usually a sponsor that also operates, sometimes only the sponsor
    "matcher_signal": 1.0,           # per signal the matcher found (ambiguous schools only)
    "name_similarity": 4.0,          # times the best of core-name and DBA similarity
    "nums_disagree": -3.0,           # ICAHN 1 against ICAHN 4
    "support_name": -2.0,            # foundation, facilities, holding company
    "small_revenue": -1.0,
    "largest_revenue": 1.0,          # REVENUE_LEAD times the next candidate's revenue
    "school_leader_title": 1.0,      # a principal, head of school or superintendent on the return
}
SCORE_FLOOR = 4.0
SCORE_MARGIN = 1.0
# A short core name matches unrelated organisations ("ROLLING HILLS" is a
# charter middle school and a private prep school), so a pick also needs one
# fact besides the name, unless the full names are near-identical.
CORROBORATING = ("same_website", "same_stem", "same_phone", "same_street", "title_names_school",
                 "schedule_o_names_school", "signals")
NEAR_IDENTICAL_NAME = 0.8
# Part of the scored model's input hash, so a changed rule redoes its decisions.
SCORING_RULES = (WEIGHTS, SCORE_FLOOR, SCORE_MARGIN, CORROBORATING, NEAR_IDENTICAL_NAME)

# Jev answers two questions per school (all wording is here, in JEV_*):
# operator_exists, a yes/no on whether any candidate runs the school, and
# operator, a choice among the candidates (left out when there is only one).
# Code decides: pick the likeliest candidate when P(yes) >= JEV_ANY_AT and its
# share of the choice >= JEV_PICK_AT; otherwise none. Both thresholds were set
# before any answer was seen, not tuned on the eval set: 0.7 is TypeSafe's
# suggested starting point for a yes, and 0.6 means more likely than every
# other candidate together.
JEV_ANY_AT = 0.7
JEV_PICK_AT = 0.6
JEV_WORKERS = 8
# Rough estimate before calling: characters of JSON per input token.
CHARS_PER_TOKEN = 3.0
# A scored none from a tie, or from a name with nothing else behind it.
LOW_CONFIDENCE = 0.3
# Text fields are trimmed so one case stays small.
TEXT_LIMIT = 2000
MAX_TITLES = 25

JEV_EXISTS = "Does any nonprofit in `candidates` hold the charter for, or govern, the school in `school`?"
JEV_EXISTS_TRUE = ("One candidate is the school's own nonprofit, or the network nonprofit that runs it and "
                   "files one return for its schools.")
JEV_EXISTS_FALSE = ("Every candidate is something else: a foundation or booster group that shares the school's "
                    "name, a management company that runs it under contract, its authorizer, a different school "
                    "with a similar name, or a sister charter that runs other campuses.")
JEV_WHICH = "Which nonprofit in `candidates` holds the charter for, or governs, the school in `school`?"
JEV_HOW_FOUND = {
    "ambiguous": "An automatic matcher found these nonprofits equally strong matches for the school.",
    "unmatched": "The automatic matcher found no match; these in-state nonprofits have a similar name or website.",
}

# The candidates, built inside a transaction that is rolled back (see adjudications()).
CANDIDATE_SETUP = [
    "CREATE INDEX ON lf USING gin (dba_core gin_trgm_ops)",
    """CREATE TEMP TABLE adj_cand (scope text, ncessch text, ein text, signals text[]) ON COMMIT DROP""",
    # Ambiguous: the tied operators.
    """INSERT INTO adj_cand
       SELECT 'ambiguous', m.ncessch, m.ein, m.signals
       FROM school_matches m JOIN ops s USING (ncessch)
       WHERE m.ambiguous AND m.role = 'operator'""",
    """CREATE TEMP TABLE adj_scope ON COMMIT DROP AS
       SELECT s.* FROM ops s JOIN school_status st USING (ncessch)
       WHERE st.status = 'unmatched' AND NOT (st.reason = ANY(%(out_of_scope)s))""",
    # Unmatched: in-state nonprofits by name (the gin indexes serve %%, at the
    # lower of the two similarities), or by site name; then the best few.
    """INSERT INTO adj_cand
       WITH hits AS (
           SELECT s.ncessch, f.ein FROM adj_scope s JOIN lf f ON f.state = s.st AND f.core %% s.core
           UNION
           SELECT s.ncessch, f.ein FROM adj_scope s JOIN lf f ON f.state = s.st AND f.dba_core %% s.core
           UNION
           SELECT s.ncessch, f.ein FROM adj_scope s JOIN lf f ON f.state = s.st
             AND split_part(f.website_domain, '.', 1) = split_part(s.website_domain, '.', 1)
             AND length(split_part(s.website_domain, '.', 1)) >= %(min_stem)s
           WHERE NOT (s.website_domain = ANY(%(excluded)s)) AND NOT (f.website_domain = ANY(%(excluded)s))
             AND s.website_domain NOT IN (SELECT domain FROM district_domains)
       ),
       scored AS (
           SELECT h.ncessch, h.ein, f.org_name,
                  greatest(coalesce(similarity(s.core, f.core), 0), coalesce(similarity(s.core, f.dba_core), 0)) AS sim,
                  coalesce(f.zip5 = s.zip5, false) AS same_zip,
                  coalesce(split_part(f.website_domain, '.', 1) = split_part(s.website_domain, '.', 1), false) AS same_stem
           FROM hits h JOIN adj_scope s USING (ncessch) JOIN lf f ON f.ein = h.ein
           WHERE f.org_name !~* %(support_name)s
             -- An umbrella district's own nonprofit authorizes its schools.
             AND NOT (s.umbrella AND similarity(f.core, s.lea_core) >= %(district_sim)s)
             AND NOT EXISTS (SELECT 1 FROM corr c WHERE c.ncessch = h.ncessch AND c.action = 'unlink' AND c.ein = h.ein)
       ),
       ranked AS (
           SELECT *, row_number() OVER (PARTITION BY ncessch
                                        ORDER BY sim + 0.5 * same_stem::int + 0.2 * same_zip::int DESC, ein) AS pos
           FROM scored
           WHERE sim >= %(cand_sim)s OR same_stem OR (same_zip AND sim >= %(cand_zip_sim)s)
       )
       -- A few spare, for the boosters and parent groups dropped in Python.
       SELECT 'unmatched', ncessch, ein, '{}'::text[] FROM ranked WHERE pos <= %(max_cand)s + 3""",
]

CANDIDATE_FACTS = """
    SELECT c.scope, c.ncessch, c.ein, c.signals,
           s.sch_name, s.lstreet1, s.lcity, s.st, s.zip5, s.lea_name, s.chartauthn1, s.website, s.phone10,
           s.core, s.nums,
           f.org_name, f.dba_name, f.street, f.city, f.state, f.zip5, f.website, f.phone10,
           f.total_revenue, f.total_expenses, f.tax_period_end, f.mgmt_delegated, f.mgmt_explanation, f.schedule_o_text,
           f.core, f.nums,
           greatest(coalesce(similarity(s.core, f.core), 0), coalesce(similarity(s.core, f.dba_core), 0)),
           coalesce(similarity(s.name_key, f.name_key), 0),
           coalesce(f.website_domain = s.website_domain AND NOT (s.website_domain = ANY(%(excluded)s)), false),
           coalesce(split_part(f.website_domain, '.', 1) = split_part(s.website_domain, '.', 1)
                    AND f.website_domain <> s.website_domain
                    AND length(split_part(s.website_domain, '.', 1)) >= %(min_stem)s
                    AND NOT (s.website_domain = ANY(%(excluded)s)), false),
           coalesce(f.phone10 = s.phone10, false),
           coalesce(f.zip5 = s.zip5, false),
           coalesce(f.ucity = s.ucity, false),
           f.state = s.st,
           coalesce(greatest(similarity(a.k, f.name_key), coalesce(similarity(a.k, f.dba_key), 0)), 0),
           f.org_name ~* %(support_name)s,
           (SELECT coalesce(array_agg(p.title ORDER BY p.seq), '{}') FROM filing_people p
            WHERE p.object_id = f.object_id AND p.title IS NOT NULL)
    FROM adj_cand c JOIN ops s USING (ncessch) JOIN lf f ON f.ein = c.ein
    CROSS JOIN LATERAL (
        SELECT nullif(btrim(regexp_replace(upper(s.chartauthn1), '[^A-Z0-9]+', ' ', 'g')), '') AS k
    ) a
    ORDER BY c.ncessch, c.ein
"""

# Undo picks a previous run applied, so the matcher's own answer is the start.
REVERT = [
    "DELETE FROM school_matches WHERE method = 'adjudicated'",
    """UPDATE school_status
       SET status = 'unmatched', n_candidates = 0, reason = detail->>'matcher_reason',
           detail = detail - 'adjudicated' - 'matcher_reason'
       WHERE detail ? 'adjudicated'""",
    """UPDATE school_matches SET ambiguous = true
       WHERE ncessch IN (SELECT ncessch FROM school_matches
                         WHERE evidence ? 'adjudicated' OR evidence ? 'adjudication_demoted')""",
    """UPDATE school_matches
       SET role = 'operator', source = 'auto', evidence = evidence - 'adjudicated' - 'adjudication_demoted'
       WHERE evidence ? 'adjudicated' OR evidence ? 'adjudication_demoted'""",
]

# Apply picks (adj_picks: ncessch, ein, scope, confidence, reason). Schools
# with an active hand correction are skipped, and never to an unlinked nonprofit.
NOT_CORRECTED = """NOT EXISTS (SELECT 1 FROM match_corrections c
                               WHERE c.ncessch = p.ncessch AND c.retired_at IS NULL
                                 AND (c.action IN ('link', 'none') OR c.ein = p.ein))"""
APPLY = [
    f"""UPDATE school_matches m
        SET role = CASE WHEN m.ein = p.ein THEN 'operator' ELSE 'related' END,
            source = CASE WHEN m.ein = p.ein THEN %(source)s::text ELSE m.source END,
            evidence = m.evidence || CASE
                WHEN m.ein = p.ein THEN jsonb_build_object('adjudicated', jsonb_build_object(
                    'method', %(method)s::text, 'model', p.model, 'confidence', p.confidence, 'reason', p.reason))
                ELSE jsonb_build_object('adjudication_demoted', jsonb_build_object('method', %(method)s::text, 'chosen', p.ein))
            END
        FROM adj_picks p
        WHERE p.scope = 'ambiguous' AND m.ncessch = p.ncessch AND m.role = 'operator' AND {NOT_CORRECTED}
          AND EXISTS (SELECT 1 FROM school_matches x WHERE x.ncessch = p.ncessch AND x.ein = p.ein AND x.role = 'operator')""",
    """UPDATE school_matches SET ambiguous = false
       WHERE ncessch IN (SELECT ncessch FROM school_matches WHERE evidence ? 'adjudicated')""",
    f"""WITH ins AS (
            INSERT INTO school_matches (ncessch, ein, object_id, method, tier, evidence, signals, ambiguous, role, source)
            SELECT p.ncessch, p.ein, f.object_id, 'adjudicated', 'likely',
                   jsonb_build_object('adjudicated', jsonb_build_object(
                       'method', %(method)s::text, 'model', p.model, 'confidence', p.confidence, 'reason', p.reason)),
                   ARRAY['adjudicated'], false, 'operator', %(source)s::text
            FROM adj_picks p JOIN latest_filings f ON f.ein = p.ein
            JOIN school_status st ON st.ncessch = p.ncessch AND st.status = 'unmatched'
            WHERE p.scope = 'unmatched' AND {NOT_CORRECTED}
            ON CONFLICT (ncessch, ein) DO NOTHING
            RETURNING ncessch
        )
        UPDATE school_status st
        SET status = 'likely', n_candidates = 1, reason = NULL,
            detail = st.detail || jsonb_build_object('adjudicated', %(method)s::text, 'matcher_reason', st.reason)
        WHERE st.ncessch IN (SELECT ncessch FROM ins)""",
]

# ----------------------------------------------------------------------------
# Features computed in Python.

def _specific_words(school_core: str | None, candidate_core: str | None) -> str | None:
    """The school's distinctive words that the candidate's own name lacks, as a
    phrase: "ASCEND PRIMARY" for KIPP Ascend Primary against KIPP Chicago."""
    theirs = set((candidate_core or "").split())
    words = [w for w in (school_core or "").split() if w not in theirs and w != "CAMPUS" and not w.isdigit()]
    phrase = " ".join(words)
    return phrase if len(phrase) >= 4 else None


def title_names_school(school_core: str | None, candidate_core: str | None, titles: list[str]) -> bool:
    """An officer's title names the school beyond what the candidate's own name
    says, as in "Head of School, Wrightwood Campus"."""
    phrase = _specific_words(school_core, candidate_core)
    return bool(phrase) and any(f" {phrase} " in f" {name_key(t) or ''} " for t in titles)


def schedule_o_names_school(school_name: str | None, school_core: str | None, candidate_core: str | None,
                            text: str | None) -> bool:
    """Schedule O names the school, in full or by its distinctive words."""
    return named_in(school_name, text) or named_in(_specific_words(school_core, candidate_core), text)


def _nums_disagree(a: str | None, b: str | None) -> bool:
    return bool(a and b and a != b)


def features(row: dict) -> dict:
    """The features both models see, for one school and candidate."""
    return {
        "signals": len(row["signals"]),
        "name_similarity": round(row["name_sim"], 2),
        "full_name_similarity": round(row["full_sim"], 2),
        "same_website": row["same_website"],
        "same_stem": row["same_stem"],
        "same_phone": row["same_phone"],
        "same_street": street_key(row["school_street"]) is not None
        and street_key(row["school_street"]) == street_key(row["street"]),
        "same_zip": row["same_zip"],
        "same_city": row["same_city"],
        "in_state": row["in_state"],
        "mgmt_delegated": row["mgmt_delegated"],
        "revenue": row["revenue"],
        "expenses": row["expenses"],
        # Set across the school's candidates in load_cases.
        "largest_revenue": False,
        "school_leader_title": any(SCHOOL_LEADER_TITLE.search((t or "").upper()) for t in row["titles"]),
        "title_names_school": title_names_school(row["school_core"], row["core"], row["titles"]),
        "authorizer_names_candidate": row["authorizer_sim"] >= AUTHORIZER_SIMILARITY,
        "schedule_o_names_school": schedule_o_names_school(
            row["school_name"], row["school_core"], row["core"], row["schedule_o_text"]),
        "nums_disagree": _nums_disagree(row["school_nums"], row["nums"]),
        "support_name": row["support_name"],
    }


# Features reported in metadata as "how often each fires".
FLAG_FEATURES = (
    "same_website", "same_stem", "same_phone", "same_street", "same_zip", "same_city", "in_state",
    "mgmt_delegated", "title_names_school", "authorizer_names_candidate", "schedule_o_names_school",
    "nums_disagree", "support_name", "largest_revenue", "school_leader_title",
)

# ----------------------------------------------------------------------------
# The scored model.


def score(f: dict) -> tuple[float, dict[str, float]]:
    """A candidate's score and what each feature contributed."""
    parts = {
        "same_website": WEIGHTS["same_website"] * f["same_website"],
        "same_stem": WEIGHTS["same_stem"] * f["same_stem"],
        "same_phone": WEIGHTS["same_phone"] * f["same_phone"],
        "same_street": WEIGHTS["same_street"] * f["same_street"],
        "title_names_school": WEIGHTS["title_names_school"] * f["title_names_school"],
        "schedule_o_names_school": WEIGHTS["schedule_o_names_school"] * f["schedule_o_names_school"],
        "same_zip": WEIGHTS["same_zip"] * f["same_zip"],
        "same_city": WEIGHTS["same_city"] * f["same_city"],
        "in_state": WEIGHTS["in_state"] * bool(f["in_state"]),
        "mgmt_delegated": WEIGHTS["mgmt_delegated"] * bool(f["mgmt_delegated"]),
        "authorizer_names_candidate": WEIGHTS["authorizer_names_candidate"] * f["authorizer_names_candidate"],
        "matcher_signal": WEIGHTS["matcher_signal"] * f["signals"],
        "name_similarity": WEIGHTS["name_similarity"] * f["name_similarity"],
        "nums_disagree": WEIGHTS["nums_disagree"] * f["nums_disagree"],
        "support_name": WEIGHTS["support_name"] * bool(f["support_name"]),
        "small_revenue": WEIGHTS["small_revenue"] * (f["revenue"] is not None and f["revenue"] < SMALL_REVENUE),
        "largest_revenue": WEIGHTS["largest_revenue"] * f["largest_revenue"],
        "school_leader_title": WEIGHTS["school_leader_title"] * f["school_leader_title"],
    }
    parts = {k: round(v, 2) for k, v in parts.items() if v}
    return round(sum(parts.values()), 2), parts


def corroborated(f: dict) -> bool:
    return any(f[k] for k in CORROBORATING) or f["full_name_similarity"] >= NEAR_IDENTICAL_NAME


def decide(candidates: list[dict]) -> tuple[str | None, float, str]:
    """(chosen EIN or None, confidence, reason) from candidates with ein, name and features.

    Confidence of a pick grows with its lead and its height above the floor;
    of a none, with how far the best candidate falls short of the floor (a
    tie above the floor, or a name with nothing else behind it, is a weak none).
    """
    if not candidates:
        return None, 1.0, "No candidates."
    ranked = sorted(((*score(c["features"]), c) for c in candidates), key=lambda x: (-x[0], x[2]["ein"]))
    top, parts, best = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else None
    lead = top - runner if runner is not None else math.inf
    why = ", ".join(f"{k} {v:+g}" for k, v in sorted(parts.items(), key=lambda kv: -abs(kv[1])))
    others = f"; next best {ranked[1][2]['name']} {runner:g}" if runner is not None else "; no other candidate"
    if top >= SCORE_FLOOR and lead >= SCORE_MARGIN and not corroborated(best["features"]):
        return None, LOW_CONFIDENCE, (
            f"{best['name']} scores {top:g} ({why}) but only on its name: no website, phone, street, "
            f"officer title, Schedule O or matcher signal agrees, and the full names differ.")
    if top >= SCORE_FLOOR and lead >= SCORE_MARGIN:
        conf = min(0.99, 0.5 + min(lead, 4) / 8 + min(top - SCORE_FLOOR, 4) / 8)
        return best["ein"], round(conf, 2), f"{best['name']} scores {top:g} ({why}){others}."
    if top < SCORE_FLOOR:
        conf = 1 - max(top, 0) / SCORE_FLOOR
        return None, round(conf, 2), f"Best is {best['name']} at {top:g}, below the floor of {SCORE_FLOOR:g} ({why})."
    return None, LOW_CONFIDENCE, (
        f"{best['name']} scores {top:g} ({why}) but leads by only {lead:g}{others}; the margin is {SCORE_MARGIN:g}.")


# ----------------------------------------------------------------------------
# Jev.


def _trim(text: str | None, limit: int = TEXT_LIMIT) -> str | None:
    return text if text is None or len(text) <= limit else text[:limit] + " [...]"


def case_payload(scope: str, rows: list[dict]) -> dict:
    """What both models are given for one school: DB facts and features only."""
    s = rows[0]
    return {
        "scope": scope,
        "school": {
            "ncessch": s["ncessch"], "name": s["school_name"], "street": s["school_street"],
            "city": s["school_city"], "state": s["school_state"], "zip": s["school_zip"],
            "district": s["district"], "authorizer": s["authorizer"], "website": s["school_website"],
            "phone": s["school_phone"],
        },
        "candidates": [
            {
                "ein": r["ein"], "name": r["name"], "dba": r["dba"], "street": r["street"], "city": r["city"],
                "state": r["state"], "zip": r["zip"], "website": r["website"], "phone": r["phone"],
                "tax_period_end": r["tax_period_end"], "signals": sorted(r["signals"]),
                "officer_titles": sorted(set(r["titles"]))[:MAX_TITLES],
                "mgmt_explanation": _trim(r["mgmt_explanation"]),
                "schedule_o_text": r["schedule_o_text"],
                "features": r["features"],
            }
            for r in rows
        ],
    }


def input_hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def option_key(r: dict) -> str:
    """A choice name the model can read: "eagle_county_charter_academy_273430689"."""
    slug = "_".join((name_key(r["name"]) or "nonprofit").lower().split())[:48]
    return f"{slug}_{r['ein']}"


def criterion(r: dict) -> str:
    """One line describing a candidate, as its choice in the operator question."""
    name = r["name"] + (f" (DBA {r['dba']})" if r["dba"] else "")
    where = ", ".join(x for x in (r["city"], r["state"]) if x) or "address not reported"
    revenue = f"revenue ${r['revenue']:,}" if r["revenue"] is not None else "revenue not reported"
    return f"{name}, EIN {r['ein']}, {where}, {revenue}."


def jev_state(scope: str, payload: dict) -> dict:
    """The case as Jev's state: candidates keyed by their choice names, so a
    question can refer to each by path, and how they were found in words."""
    return {
        "school": payload["school"],
        "how_candidates_were_found": JEV_HOW_FOUND[scope],
        "candidates": {option_key(c): c for c in payload["candidates"]},
    }


def jev_questions(rows: list[dict]) -> dict:
    questions = {"operator_exists": {"type": "noul", "instructions": JEV_EXISTS,
                                     "criteria": {"true": JEV_EXISTS_TRUE, "false": JEV_EXISTS_FALSE}}}
    if len(rows) > 1:
        questions["operator"] = {"type": "choice", "instructions": JEV_WHICH,
                                 "criteria": {option_key(r): criterion(r) for r in rows}}
    return questions


def jev_probabilities(answers: dict, rows: list[dict]) -> tuple[float, dict[str, float]]:
    """(P(some candidate runs the school), each candidate's share of the choice by EIN)."""
    p_any = float(answers["operator_exists"]["noul"])
    if len(rows) == 1:
        return p_any, {rows[0]["ein"]: 1.0}
    by_key = {option_key(r): r["ein"] for r in rows}
    probs = answers["operator"]["probabilities"]
    return p_any, {by_key[k]: float(v) for k, v in probs.items() if k in by_key}


def jev_decision(p_any: float, shares: dict[str, float]) -> tuple[str | None, float]:
    """The likeliest candidate when Jev says one runs the school (JEV_ANY_AT)
    and it clearly leads the choice (JEV_PICK_AT); otherwise none. The
    confidence of a pick is P(yes) times its share; of a none, P(no)."""
    top = min(shares, key=lambda e: (-shares[e], e)) if shares else None
    if top and p_any >= JEV_ANY_AT and shares[top] >= JEV_PICK_AT:
        return top, round(p_any * shares[top], 4)
    return None, round(1 - p_any, 4)


def _evidence(r: dict) -> tuple[list[str], list[str]]:
    """The facts for and against one candidate, in words."""
    f, pro, con = r["features"], [], []
    if f["same_website"]:
        pro.append(f"same website ({r['website']})")
    if f["same_stem"]:
        pro.append(f"same site name ({r['website']})")
    if f["same_phone"]:
        pro.append("same phone")
    if f["same_street"]:
        pro.append(f"same street ({r['street']})")
    if f["title_names_school"]:
        title = next((t for t in r["titles"] if title_names_school(r["school_core"], r["core"], [t])), "")
        pro.append(f'an officer title names the school ("{title}")')
    if f["schedule_o_names_school"]:
        pro.append("Schedule O names the school")
    if f["school_leader_title"]:
        pro.append("officers include a school leader")
    if f["largest_revenue"]:
        pro.append(f"the largest revenue among the candidates (${f['revenue']:,})")
    if f["authorizer_names_candidate"]:
        pro.append("the authorizer bears its name")
    if f["signals"]:
        pro.append(f"matcher signals: {', '.join(sorted(r['signals']))}")
    pro.append(f"name similarity {f['name_similarity']:.2f}")
    if f["support_name"]:
        con.append("named like a support entity")
    if f["nums_disagree"]:
        con.append("its number differs from the school's")
    if f["revenue"] is not None and f["revenue"] < SMALL_REVENUE:
        con.append(f"small revenue (${f['revenue']:,})")
    if not f["in_state"]:
        con.append("files from another state")
    return pro, con


def rationale(p_any: float, shares: dict[str, float], chosen: str | None, rows: list[dict]) -> str:
    """The stored reason, written from the evidence, never by the model."""
    by_ein = {r["ein"]: r for r in rows}
    ranked = sorted(shares, key=lambda e: (-shares[e], e))
    yes = round(p_any * 100)
    if not ranked:
        return f"None: no candidates. Jev puts {yes}% on a candidate running the school."
    best = by_ein[chosen or ranked[0]]
    share = round(shares[best["ein"]] * 100)
    if chosen:
        lead = f"Jev puts {yes}% on a candidate running the school, and {share}% of that on {best['name']}."
    elif p_any < JEV_ANY_AT:
        lead = (f"None: Jev puts {yes}% on any candidate running the school ({round(JEV_ANY_AT * 100)}% needed); "
                f"the likeliest is {best['name']} at {share}%.")
    else:
        lead = (f"None: Jev puts {yes}% on a candidate running the school but only {share}% on the likeliest, "
                f"{best['name']} ({round(JEV_PICK_AT * 100)}% needed).")
    pro, con = _evidence(best)
    text = lead + f" For it: {'; '.join(pro)}." + (f" Against: {'; '.join(con)}." if con else "")
    others = [e for e in ranked if e != best["ein"]]
    if others:
        text += f" Next: {by_ein[others[0]]['name']} {round(shares[others[0]] * 100)}%."
    return text


def estimate_cost(requests_: list[tuple[dict, dict]]) -> float:
    chars = sum(len(json.dumps(state, default=str)) + len(json.dumps(qs)) for state, qs in requests_)
    return round(chars / CHARS_PER_TOKEN * jev.USD_PER_M_INPUT / 1e6, 4)


def call_jev(state: dict, questions: dict, rows: list[dict]):
    """One school. Returns ((chosen, confidence, reason), model version, input tokens)."""
    response = jev.ask(state, questions)
    p_any, shares = jev_probabilities(response["answers"], rows)
    chosen, confidence = jev_decision(p_any, shares)
    return ((chosen, confidence, rationale(p_any, shares, chosen, rows)),
            response["model"] or jev.model(), jev.input_tokens(response["usage"]))


# ----------------------------------------------------------------------------

FACT_KEYS = (
    "scope", "ncessch", "ein", "signals",
    "school_name", "school_street", "school_city", "school_state", "school_zip", "district", "authorizer",
    "school_website", "school_phone", "school_core", "school_nums",
    "name", "dba", "street", "city", "state", "zip", "website", "phone",
    "revenue", "expenses", "tax_period_end", "mgmt_delegated", "mgmt_explanation", "schedule_o_text", "core", "nums",
    "name_sim", "full_sim", "same_website", "same_stem", "same_phone", "same_zip", "same_city", "in_state",
    "authorizer_sim", "support_name", "titles",
)
CANDIDATE_PARAMS = {
    **PARAMS,
    "out_of_scope": OUT_OF_SCOPE_REASONS,
    "cand_sim": CANDIDATE_NAME_SIMILARITY,
    "cand_zip_sim": CANDIDATE_ZIP_SIMILARITY,
    "max_cand": MAX_CANDIDATES,
}


def mark_largest_revenue(rows: list[dict]) -> None:
    """Flag the candidate whose revenue is REVENUE_LEAD times the next one's."""
    ranked = sorted((r["features"]["revenue"] or 0, r["ein"]) for r in rows)
    if len(ranked) >= 2 and ranked[-1][0] > 0 and ranked[-1][0] >= REVENUE_LEAD * ranked[-2][0]:
        next(r for r in rows if r["ein"] == ranked[-1][1])["features"]["largest_revenue"] = True


def load_cases(conn) -> dict[tuple[str, str], list[dict]]:
    """Candidates and features for each in-scope school, keyed by (scope, ncessch).

    Runs in a transaction the caller rolls back: picks a previous run applied
    are undone first, so the scope is the matcher's own.
    """
    for sql in REVERT:
        conn.execute(sql)
    for sql in SETUP:
        conn.execute(sql, PARAMS if "%(" in sql else None)
    # Lets the gin indexes serve % at the lower of the two name similarities.
    conn.execute(f"SET LOCAL pg_trgm.similarity_threshold = {CANDIDATE_ZIP_SIMILARITY}")
    for sql in CANDIDATE_SETUP:
        conn.execute(sql, CANDIDATE_PARAMS if "%(" in sql else None)
    cases: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for values in conn.execute(CANDIDATE_FACTS, CANDIDATE_PARAMS).fetchall():
        row = dict(zip(FACT_KEYS, values))
        row["tax_period_end"] = str(row["tax_period_end"]) if row["tax_period_end"] else None
        row["features"] = features(row)
        cases[(row["scope"], row["ncessch"])].append(row)
    scope = conn.execute("SELECT ncessch FROM adj_scope").fetchall()
    for (ncessch,) in scope:
        cases.setdefault(("unmatched", ncessch), [])
    # Unmatched: drop parent groups and boosters, keep the best few.
    for (scope_name, ncessch), rows in cases.items():
        if scope_name == "unmatched":
            rows[:] = sorted(
                (r for r in rows if not is_support_org(r["name"])),
                key=lambda r: (-(r["name_sim"] + 0.5 * r["same_stem"] + 0.2 * r["same_zip"]), r["ein"]),
            )[:MAX_CANDIDATES]
            rows.sort(key=lambda r: r["ein"])
    for rows in cases.values():
        mark_largest_revenue(rows)
    return cases


UPSERT = """
    INSERT INTO adjudications (ncessch, method, model, input_hash, chosen_ein, confidence, reason)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ncessch, method, model, input_hash) DO UPDATE
    SET chosen_ein = EXCLUDED.chosen_ein, confidence = EXCLUDED.confidence, reason = EXCLUDED.reason,
        decided_at = now()
    WHERE (adjudications.chosen_ein, adjudications.confidence, adjudications.reason)
          IS DISTINCT FROM (EXCLUDED.chosen_ein, EXCLUDED.confidence, EXCLUDED.reason)
"""


@asset(
    group_name="pipeline",
    deps=[school_matches],
    description="Decisions for ambiguous and unmatched schools by a scored model and by Jev, from the same "
                "evidence. Shadow mode unless ADJUDICATOR=scored|jev.",
)
def adjudications(pg: PostgresResource) -> MaterializeResult:
    applying = (os.environ.get("ADJUDICATOR") or "").strip().lower() or None
    if applying not in (None, "scored", "jev"):
        raise ValueError(f"ADJUDICATOR must be scored or jev, not {applying!r}")

    with pg.connect() as conn:
        with conn.transaction(force_rollback=True):
            cases = load_cases(conn)

    # The scored model, and Jev's requests.
    scored = {}  # (scope, ncessch) -> (hash, chosen, confidence, reason)
    jev_inputs = {}  # (scope, ncessch) -> (hash, state, questions)
    for key, rows in cases.items():
        payload = case_payload(key[0], rows) if rows else {"scope": key[0], "ncessch": key[1], "candidates": []}
        scored[key] = (input_hash(SCORED_MODEL, SCORING_RULES, payload), *decide(
            [{"ein": r["ein"], "name": r["name"], "features": r["features"]} for r in rows]))
        state = jev_state(key[0], payload) if rows else payload
        questions = jev_questions(rows)
        jev_inputs[key] = (input_hash(jev.model(), state, jev.questions_hash(questions)), state, questions)

    # Jev: only schools with candidates and no stored answer for this state and
    # question. The stored answer may come from an earlier Jev version, since
    # the alias can't pin one; the version is recorded in the model column.
    jev_meta: dict[str, object] = {"jev_model_asked": jev.model(), "jev_any_at": JEV_ANY_AT,
                                   "jev_pick_at": JEV_PICK_AT}
    with pg.connect() as conn:
        conn.autocommit = True
        stored = set(conn.execute("SELECT ncessch, input_hash FROM adjudications WHERE method = 'jev'").fetchall())
        pending = {k: v for k, v in jev_inputs.items() if cases[k] and (k[1], v[0]) not in stored}
        limit = int(os.environ.get("ADJUDICATOR_MAX_CALLS") or 0) or None
        if limit:
            pending = dict(sorted(pending.items())[:limit])
        jev_meta["jev_cached"] = sum(1 for k, v in jev_inputs.items() if cases[k] and (k[1], v[0]) in stored)
        jev_meta["jev_pending_calls"] = len(pending)
        jev_meta["jev_estimated_cost_usd"] = estimate_cost([(v[1], v[2]) for v in pending.values()])
        jev_meta["jev_estimated_cost_full_run_usd"] = estimate_cost(
            [(v[1], v[2]) for k, v in jev_inputs.items() if cases[k]])
        if not jev.available():
            jev_meta["jev_note"] = "skipped: JEV_API_KEY is not set (make adjudicate loads .env.jev)"
        else:
            conn.execute("DELETE FROM load_errors WHERE stage = 'adjudicate'")
            # Schools with no candidates are answered without a call.
            for (scope, ncessch), (h, _, _) in jev_inputs.items():
                if not cases[(scope, ncessch)]:
                    conn.execute(UPSERT, (ncessch, "jev", jev.model(), h, None, 1.0, "No candidates; no call made."))
            calls = failures = tokens_in = 0
            errors: Counter = Counter()
            versions: Counter = Counter()
            with ThreadPoolExecutor(JEV_WORKERS) as pool:
                futures = {
                    pool.submit(call_jev, state, questions, cases[key]): (key, h)
                    for key, (h, state, questions) in pending.items()
                }
                for future in as_completed(futures):
                    (scope, ncessch), h = futures[future]
                    calls += 1
                    try:
                        (chosen, confidence, reason), version, t_in = future.result()
                    except Exception as exc:  # logged, and asked again on the next run
                        failures += 1
                        errors[type(exc).__name__] += 1
                        pg.log_error(conn, "adjudicate", ncessch, repr(exc))
                        continue
                    tokens_in += t_in
                    versions[version] += 1
                    # Stored as each answer arrives, so an interrupted run keeps what it paid for.
                    conn.execute(UPSERT, (ncessch, "jev", version, h, chosen, confidence, reason))
            jev_meta.update({
                "jev_calls": calls, "jev_failures": failures, "jev_input_tokens": tokens_in,
                "jev_cost_usd": round(tokens_in * jev.USD_PER_M_INPUT / 1e6, 4),
                "jev_versions": ", ".join(f"{v} ({n})" for v, n in versions.most_common()) or "none called",
            })
            jev_meta.update({f"jev_error_{k}": v for k, v in errors.items()})

    with pg.connect() as conn:
        for sql in REVERT:
            conn.execute(sql)
        with conn.cursor() as cur:
            cur.executemany(UPSERT, [
                (ncessch, "scored", SCORED_MODEL, h, chosen, conf, reason)
                for (_, ncessch), (h, chosen, conf, reason) in scored.items()
            ])
        # Drop decisions about inputs that no longer exist (schools that left
        # the scope or whose evidence changed), so each school's latest
        # decision is about its current evidence.
        conn.execute("CREATE TEMP TABLE adj_current (ncessch text, method text, input_hash text) ON COMMIT DROP")
        with conn.cursor() as cur, cur.copy("COPY adj_current FROM STDIN") as copy:
            for (_, ncessch), (h, *_) in scored.items():
                copy.write_row((ncessch, "scored", h))
            for (_, ncessch), (h, *_) in jev_inputs.items():
                copy.write_row((ncessch, "jev", h))
        pruned = conn.execute(
            """DELETE FROM adjudications a WHERE NOT EXISTS (
                   SELECT 1 FROM adj_current c
                   WHERE c.ncessch = a.ncessch AND c.method = a.method AND c.input_hash = a.input_hash)"""
        ).rowcount

        # Decisions for the current inputs, by method: the latest, for Jev,
        # whichever version answered.
        current = {}
        for ncessch, method, model, chosen, conf, reason in conn.execute(
            """SELECT DISTINCT ON (a.ncessch, a.method) a.ncessch, a.method, a.model, a.chosen_ein, a.confidence,
                      a.reason
               FROM adjudications a JOIN adj_current c USING (ncessch, method, input_hash)
               WHERE a.method = 'jev' OR a.model = %s
               ORDER BY a.ncessch, a.method, a.decided_at DESC""",
            (SCORED_MODEL,),
        ).fetchall():
            current[(method, ncessch)] = (chosen, conf, reason, model)

        applied = Counter()
        if applying:
            picks = [
                (ncessch, chosen, scope, conf, reason, model)
                for (scope, ncessch) in cases
                for chosen, conf, reason, model in [current.get((applying, ncessch), (None, 0, "", ""))]
                if chosen
            ]
            conn.execute(
                "CREATE TEMP TABLE adj_picks (ncessch text, ein text, scope text, confidence real, reason text, "
                "model text) "
                "ON COMMIT DROP")
            with conn.cursor() as cur, cur.copy("COPY adj_picks FROM STDIN") as copy:
                for row in picks:
                    copy.write_row(row)
            params = {"method": applying, "source": "model" if applying == "scored" else "jev"}
            for sql in APPLY:
                conn.execute(sql, params)
            applied = Counter(dict(conn.execute(
                """SELECT CASE WHEN method = 'adjudicated' THEN 'unmatched' ELSE 'ambiguous' END, count(*)
                   FROM school_matches WHERE evidence ? 'adjudicated' GROUP BY 1""").fetchall()))

    # Metadata: scope sizes, decisions, how often each feature fires.
    meta: dict[str, object] = {"adjudicator_applied": applying or "none (shadow mode)"}
    for scope_name in ("ambiguous", "unmatched"):
        keys = [k for k in cases if k[0] == scope_name]
        rows = [r for k in keys for r in cases[k]]
        decided = [scored[k][1] for k in keys]
        meta[f"{scope_name}_schools"] = len(keys)
        meta[f"{scope_name}_schools_without_candidates"] = sum(1 for k in keys if not cases[k])
        meta[f"{scope_name}_candidates"] = len(rows)
        meta[f"scored_{scope_name}_picked"] = sum(1 for c in decided if c)
        meta[f"scored_{scope_name}_none"] = sum(1 for c in decided if not c)
        jev_decided = [current.get(("jev", k[1])) for k in keys]
        meta[f"jev_{scope_name}_decided"] = sum(1 for d in jev_decided if d)
        meta[f"jev_{scope_name}_picked"] = sum(1 for d in jev_decided if d and d[0])
        for name in FLAG_FEATURES:
            meta[f"feature_{scope_name}_{name}"] = sum(1 for r in rows if r["features"][name])
        if applying:
            meta[f"applied_{scope_name}"] = applied.get(scope_name, 0)
    meta["decisions_pruned"] = pruned
    meta.update(jev_meta)
    return MaterializeResult(metadata=meta)
