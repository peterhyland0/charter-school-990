"""Schedule R: the organisations each filer is related to by control.

A filer completes Schedule R when it controls, or is controlled by, another
organisation. Part II lists related tax-exempt organisations with their EIN,
primary activity and direct controlling entity; Parts III and IV list related
partnerships, corporations and trusts; Part V line 2 lists the money that
moved between them, by name and transaction letter.

related_orgs stores all of it. related_links then reads it for each nonprofit
that runs schools, from its own return and from any return that lists it:
- management: the related organisation performs services for it (Part V
  letter m on its return, l on theirs), or its return describes the related
  organisation as a management company and doesn't control it. This finds
  networks' management nonprofits (Imagine Schools, Lifelong Learning
  Administration, String Theory Schools), not for-profit managers, which
  aren't related by control and so never appear on Schedule R.
- network: a related nonprofit that runs schools itself, is described as a
  school, or shares paid staff with it: sibling campuses and network offices.
Government bodies (an authorizing district, a university), foundations,
facilities and lending companies, parent groups and unrelated businesses
(a hospital, an investment fund) are left out, as is an organisation a
return calls inactive or dissolved, unless it files a current return of its
own: ResponsiveEd Texas calls ResponsiveEd Arkansas "inactive", but Arkansas
reports $26M of revenue for the year to June 2025.

The 512(b)(13) "controlled entity" box is not used: in 426 pairs of school
nonprofits each claims to control the other.
"""

import re

from dagster import MaterializeResult, asset
from lxml import etree

from ..resources import PostgresResource
from ..util import name_key, parse_bool
from .extract import PARSER, _int, _strip_namespaces, _text, parsed_filings
from .match import SUPPORT_NAME, school_matches
from .returns import xml_path

STAGE = "related"
# (part, group element, element holding the related organisation's name).
# Part II calls it DisregardedEntityName, a quirk of the IRS schema.
PARTS = (
    ("II", "IdRelatedTaxExemptOrgGrp", "DisregardedEntityName"),
    ("III", "IdRelatedOrgTxblPartnershipGrp", "RelatedOrganizationName"),
    ("IV", "IdRelatedOrgTxblCorpTrGrp", "RelatedOrganizationName"),
)
_EIN = re.compile(r"[0-9]{9}")
# Part V line 1's transaction letters.
_LETTERS = "abcdefghijklmnopqrs"


def transaction_letters(value: str | None) -> str | None:
    """The form letters in a free-text transaction type, in order: "m", "1m",
    "b,c" and "lmqs" are all used. Words ("rent", "loan") give nothing."""
    value = (value or "").strip().lower()
    if value in ("n/a", "na", "none"):
        return None
    letters = set()
    for token in re.split(r"[^a-z0-9]+", value):
        token = token.removeprefix("1")
        # Several letters run together are always in form order; a word isn't.
        if token and len(token) <= 4 and all(c in _LETTERS for c in token) and list(token) == sorted(set(token)):
            letters.update(token)
    return "".join(sorted(letters)) or None


def _business_name(group, element: str) -> str | None:
    return " ".join(filter(None, (
        _text(group, f"{element}/BusinessNameLine1Txt", f"{element}/BusinessNameLine1"),
        _text(group, f"{element}/BusinessNameLine2Txt", f"{element}/BusinessNameLine2"),
    ))) or None


def parse_schedule_r(data: bytes) -> tuple[list[dict], list[dict]]:
    """(related organisations, transactions) from one return; empty without Schedule R."""
    if b"IRS990ScheduleR" not in data:
        return [], []
    root = etree.fromstring(data, PARSER)
    _strip_namespaces(root)
    schedule = root.find("ReturnData/IRS990ScheduleR")
    if schedule is None:
        return [], []

    orgs = []
    for part, group, name_element in PARTS:
        for g in schedule.findall(group):
            name = _business_name(g, name_element)
            ein = _text(g, "EIN")
            orgs.append({
                "part": part,
                "name": name,
                "name_key": name_key(name),
                "ein": ein if ein and _EIN.fullmatch(ein) else None,
                "state": _text(g, "USAddress/StateAbbreviationCd", "USAddress/State", "LegalDomicileStateCd"),
                "activity": _text(g, "PrimaryActivitiesTxt"),
                "controlled_by": _business_name(g, "DirectControllingEntityName"),
                "controlled": parse_bool(_text(g, "ControlledOrganizationInd")),
            })
    transactions = []
    for g in schedule.findall("TransactionsRelatedOrgGrp"):
        name = _business_name(g, "OtherOrganizationName")
        transactions.append({
            "name": name,
            "name_key": name_key(name),
            "type": transaction_letters(_text(g, "TransactionTypeTxt")),
            "amount": _int(_text(g, "InvolvedAmt")),
        })
    return orgs, transactions


ORG_COLUMNS = ("part", "name", "name_key", "ein", "state", "activity", "controlled_by", "controlled")
TRANSACTION_COLUMNS = ("name", "name_key", "type", "amount")


@asset(group_name="pipeline", deps=[parsed_filings], description="Schedule R: each filer's related organisations and its transactions with them.")
def related_orgs(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        object_ids = [o for (o,) in conn.execute("SELECT object_id FROM filings ORDER BY object_id").fetchall()]

    org_rows, transaction_rows, errors, with_schedule = [], [], [], 0
    for object_id in object_ids:
        path = xml_path(object_id)
        if not path.exists():
            continue
        try:
            orgs, transactions = parse_schedule_r(path.read_bytes())
        except Exception as exc:
            errors.append((object_id, repr(exc)))
            continue
        with_schedule += bool(orgs or transactions)
        org_rows += [(object_id, i, *[o[c] for c in ORG_COLUMNS]) for i, o in enumerate(orgs)]
        transaction_rows += [(object_id, i, *[t[c] for c in TRANSACTION_COLUMNS]) for i, t in enumerate(transactions)]

    with pg.connect() as conn:
        conn.execute("DELETE FROM load_errors WHERE stage = %s", (STAGE,))
        for object_id, error in errors:
            pg.log_error(conn, STAGE, object_id, error)
        pg.replace_table(conn, "filing_related_orgs", ("object_id", "seq", *ORG_COLUMNS), org_rows, live=True)
        pg.replace_table(conn, "filing_related_transactions", ("object_id", "seq", *TRANSACTION_COLUMNS),
                         transaction_rows, live=True)

    return MaterializeResult(metadata={
        "returns_read": len(object_ids),
        "with_schedule_r": with_schedule,
        "related_orgs": len(org_rows),
        "related_orgs_with_ein": sum(1 for r in org_rows if r[5]),
        "transactions": len(transaction_rows),
        "failed": len(errors),
    })


# A Part V transaction belongs to a related organisation when the filer named
# both alike.
SAME_NAME = 0.8
GOVERNMENT_NAME = r"\m(SCHOOL DISTRICT|UNIFIED|ISD|COUNTY OFFICE|BOARD OF EDUCATION|CITY OF|COUNTY OF|UNIVERSITY|COLLEGE)\M"
PARENT_GROUP_NAME = r"\m(FRIENDS OF|AMIGOS DE|PTA|PTO|PTSA|BOOSTERS?|ALUMNI|PARENT|PARENTS)\M"
# Matched against the name and the activity: "Arizona Leasing Enterprises" is
# described as "management".
SUPPORT_ACTIVITY = r"\m(REAL ESTATE|FACILIT|LEAS|LEND|LOAN|PROPERT|HOUSING|FUNDRAIS|HOLDING|BUILDING|INVEST)"
MANAGEMENT_ACTIVITY = r"(MANAGEMENT|MGMT|\mEMO\M|\mCMO\M|ADMINISTRATIVE SERV|ADMIN SUP)"
SCHOOL_ACTIVITY = r"(CHARTER|SCHOOL|EDUCAT|ACADEM|LEARNING|\mPREP\M)"
# Matched against the name and the activity, as in "Troy Prep Foundation Inc
# (dissolved in FY25)" on Uncommon's return.
INACTIVE = r"\m(INACTIV|DISSOLV|DEFUNCT)"

LINKS = [
    """CREATE TEMP TABLE ops ON COMMIT DROP AS
       SELECT DISTINCT m.ein, f.name_key FROM school_matches m JOIN filings f USING (object_id)
       WHERE m.role = 'operator'""",
    """CREATE TEMP TABLE runs ON COMMIT DROP AS
       SELECT ein, count(DISTINCT ncessch) AS schools FROM school_matches WHERE role = 'operator' GROUP BY 1""",
    "CREATE TEMP TABLE lf ON COMMIT DROP AS SELECT * FROM latest_filings",
    "CREATE INDEX ON lf (ein)",
    # Schedule R rows naming another organisation by EIN, on each latest return.
    """CREATE TEMP TABLE rr ON COMMIT DROP AS
       SELECT f.ein AS filer, f.org_name AS filer_name, r.*
       FROM filing_related_orgs r JOIN lf f USING (object_id)
       WHERE r.ein IS NOT NULL AND r.ein <> f.ein""",
    "CREATE INDEX ON rr (filer)",
    "CREATE INDEX ON rr (ein)",
    """CREATE TEMP TABLE rt ON COMMIT DROP AS
       SELECT t.* FROM filing_related_transactions t JOIN lf f USING (object_id)""",
    "CREATE INDEX ON rt (object_id)",
    "ANALYZE rr",
    "ANALYZE rt",
    # Each school nonprofit's related organisations, seen from its own return
    # ('own') or from the related organisation's ('theirs'). on_return is the
    # name the reporting return uses, to find its Part V transactions.
    """CREATE TEMP TABLE edges ON COMMIT DROP AS
       SELECT o.ein, r.ein AS related_ein, r.name AS related_name, r.activity, 'own' AS side,
              r.object_id, r.name_key AS on_return,
              similarity(upper(r.controlled_by), o.name_key) > %(same_name)s AS controlled_by_us
       FROM ops o JOIN rr r ON r.filer = o.ein
       UNION ALL
       SELECT o.ein, r.filer, r.filer_name, NULL, 'theirs', r.object_id, r.name_key, false
       FROM ops o JOIN rr r ON r.ein = o.ein""",
    """CREATE TEMP TABLE pairs ON COMMIT DROP AS
       SELECT e.ein, e.related_ein,
              coalesce(min(f.org_name), mode() WITHIN GROUP (ORDER BY e.related_name)) AS related_name,
              max(e.activity) AS activity,
              bool_or(e.controlled_by_us) AS controlled_by_us,
              coalesce(bool_or(upper(e.related_name) ~ %(inactive)s OR upper(e.activity) ~ %(inactive)s), false)
                  AND count(f.ein) = 0 AS inactive,
              greatest(sum(t.amount) FILTER (WHERE e.side = 'own' AND t.type ~ 'm'),
                       sum(t.amount) FILTER (WHERE e.side = 'theirs' AND t.type ~ 'l')) AS services_amt,
              coalesce(bool_or((e.side = 'own' AND t.type ~ 'm') OR (e.side = 'theirs' AND t.type ~ 'l')), false) AS services,
              coalesce(bool_or(t.type ~ 'o'), false) AS shared_staff,
              array_agg(DISTINCT e.side) AS sides,
              array_agg(DISTINCT e.object_id) AS returns
       FROM edges e
       LEFT JOIN rt t ON t.object_id = e.object_id AND similarity(t.name_key, e.on_return) > %(same_name)s
       LEFT JOIN lf f ON f.ein = e.related_ein
       GROUP BY 1, 2""",
    # A management organisation must be named or described as school-related,
    # unless the school nonprofit says it delegates management (Part VI line
    # 3): a university-run charter's return also lists the company that
    # manages the university's physics lab.
    """INSERT INTO related_links (ein, related_ein, related_name, kind, services_amt, related_schools, evidence)
       SELECT * FROM (
           SELECT p.ein, p.related_ein, p.related_name,
                  CASE
                      WHEN p.inactive THEN NULL
                      WHEN upper(p.related_name) ~ %(government)s OR p.activity ~* 'GOVERNMENT' THEN NULL
                      WHEN upper(p.related_name) ~ %(support)s OR upper(p.related_name) ~ %(parent_group)s
                           OR upper(p.related_name) ~ %(support_activity)s
                           OR upper(p.activity) ~ %(support_activity)s THEN NULL
                      WHEN (p.services OR (upper(p.activity) ~ %(management_activity)s
                                           AND NOT coalesce(p.controlled_by_us, false)))
                           AND (d.mgmt_delegated OR upper(p.related_name) ~ %(school_activity)s
                                OR upper(p.activity) ~ %(school_activity)s) THEN 'management'
                      WHEN coalesce(r.schools, 0) > 0 OR upper(p.activity) ~ %(school_activity)s OR p.shared_staff
                           OR 'theirs' = ANY(p.sides) THEN 'network'
                  END AS kind,
                  p.services_amt, coalesce(r.schools, 0),
                  jsonb_strip_nulls(jsonb_build_object(
                      'listed_on', CASE WHEN cardinality(p.sides) = 2 THEN 'both returns'
                                        WHEN p.sides[1] = 'own' THEN 'its return' ELSE 'their return' END,
                      'returns', to_jsonb(p.returns), 'activity', p.activity,
                      'services', p.services, 'shared_staff', p.shared_staff))
           FROM pairs p
           LEFT JOIN runs r ON r.ein = p.related_ein
           LEFT JOIN lf d ON d.ein = p.ein
       ) x
       WHERE kind IS NOT NULL""",
]

LINK_PARAMS = {
    "same_name": SAME_NAME,
    "government": GOVERNMENT_NAME,
    "support": SUPPORT_NAME,
    "parent_group": PARENT_GROUP_NAME,
    "support_activity": SUPPORT_ACTIVITY,
    "management_activity": MANAGEMENT_ACTIVITY,
    "school_activity": SCHOOL_ACTIVITY,
    "inactive": INACTIVE,
}


@asset(group_name="pipeline", deps=[related_orgs, school_matches], description="Network and management organisations of each school's nonprofit, from Schedule R.")
def related_links(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        # The website reads this table: DELETE, not TRUNCATE, so readers aren't blocked.
        conn.execute("DELETE FROM related_links")
        for sql in LINKS:
            conn.execute(sql, LINK_PARAMS if "%(" in sql else None)
        kinds = dict(conn.execute("SELECT kind, count(*) FROM related_links GROUP BY 1").fetchall())
        schools = dict(conn.execute(
            """SELECT l.kind, count(DISTINCT m.ncessch) FROM related_links l
               JOIN school_matches m ON m.ein = l.ein AND m.role = 'operator' GROUP BY 1"""
        ).fetchall())
    return MaterializeResult(metadata={
        "management_links": kinds.get("management", 0),
        "network_links": kinds.get("network", 0),
        "schools_with_management": schools.get("management", 0),
        "schools_with_network": schools.get("network", 0),
    })
