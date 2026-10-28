"""Pull the rep-facing facts out of each cached return."""

import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dagster import MaterializeResult, asset
from lxml import etree

from ..resources import PostgresResource
from ..util import normalize_site, parse_bool
from .returns import SELECT_LATEST, raw_returns, xml_path

PARSER_VERSION = "0.1"
STAGE = "parse"
NS = {"e": "http://www.irs.gov/efile"}
# Returns are untrusted input: no entity expansion, no network access.
PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _xp(path: str) -> str:
    return "/".join(f"e:{part}" for part in path.split("/"))


def _text(node, *paths: str) -> str | None:
    """First non-empty text among the given paths (newer names first)."""
    for path in paths:
        found = node.find(_xp(path), NS)
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def _int(value: str | None) -> int | None:
    try:
        return int(Decimal(value)) if value is not None else None
    except InvalidOperation:
        return None


def _num(value: str | None) -> Decimal | None:
    try:
        return Decimal(value) if value is not None else None
    except InvalidOperation:
        return None


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _person_name(group) -> str | None:
    return _text(group, "PersonNm", "BusinessName/BusinessNameLine1Txt", "BusinessName/BusinessNameLine1")


def parse_return(data: bytes) -> tuple[dict, list[dict]]:
    root = etree.fromstring(data, PARSER)
    header = root.find("e:ReturnHeader", NS)
    if header is None:
        raise ValueError("no ReturnHeader")
    return_type = _text(header, "ReturnTypeCd")
    if return_type not in ("990", "990EZ"):
        raise ValueError(f"unsupported return type {return_type!r}")
    is_ez = return_type == "990EZ"
    body = root.find(_xp("ReturnData/IRS990EZ" if is_ez else "ReturnData/IRS990"), NS)
    if body is None:
        raise ValueError(f"no form body for return type {return_type!r}")

    website = _text(body, "WebsiteAddressTxt")
    host, domain = normalize_site(website)
    filing = {
        "ein": _text(header, "Filer/EIN"),
        "return_type": return_type,
        "return_version": root.get("returnVersion"),
        "tax_period_begin": _date(_text(header, "TaxPeriodBeginDt")),
        "tax_period_end": _date(_text(header, "TaxPeriodEndDt")),
        "return_ts": _ts(_text(header, "ReturnTs")),
        "amended": bool(parse_bool(_text(body, "AmendedReturnInd"))),
        "org_name": _text(header, "Filer/BusinessName/BusinessNameLine1Txt", "Filer/BusinessName/BusinessNameLine1"),
        "dba_name": _text(body, "DoingBusinessAsName/BusinessNameLine1Txt"),
        "website": website,
        "website_host": host,
        "website_domain": domain,
        "total_revenue": _int(_text(body, "TotalRevenueAmt" if is_ez else "CYTotalRevenueAmt")),
        # 990-EZ does not ask whether management was delegated.
        "mgmt_delegated": None if is_ez else parse_bool(_text(body, "DelegationOfMgmtDutiesInd")),
    }

    people = []
    if is_ez:
        for g in body.findall("e:OfficerDirectorTrusteeEmplGrp", NS):
            benefits = (_int(_text(g, "EmployeeBenefitProgramAmt")) or 0) + (_int(_text(g, "ExpenseAccountOtherAllwncAmt")) or 0)
            people.append({
                "name": _person_name(g), "title": _text(g, "TitleTxt"),
                "avg_hours": _num(_text(g, "AverageHrsPerWkDevotedToPosRt")),
                "comp_org": _int(_text(g, "CompensationAmt")), "comp_related": None,
                "comp_other": benefits or None,
                "is_officer": False, "is_director": False, "is_key_employee": False, "is_hce": False,
            })
    else:
        for g in body.findall("e:Form990PartVIISectionAGrp", NS):
            people.append({
                "name": _person_name(g), "title": _text(g, "TitleTxt"),
                "avg_hours": _num(_text(g, "AverageHoursPerWeekRt")),
                "comp_org": _int(_text(g, "ReportableCompFromOrgAmt")),
                "comp_related": _int(_text(g, "ReportableCompFromRltdOrgAmt")),
                "comp_other": _int(_text(g, "OtherCompensationAmt")),
                "is_officer": bool(parse_bool(_text(g, "OfficerInd"))),
                "is_director": bool(parse_bool(_text(g, "IndividualTrusteeOrDirectorInd"))),
                "is_key_employee": bool(parse_bool(_text(g, "KeyEmployeeInd"))),
                "is_hce": bool(parse_bool(_text(g, "HighestCompensatedEmployeeInd"))),
            })
    if not filing["ein"]:
        raise ValueError("no Filer/EIN")
    return filing, people


FILING_COLUMNS = (
    "ein", "return_type", "return_version", "tax_period_begin", "tax_period_end",
    "return_ts", "amended", "org_name", "dba_name", "website", "website_host",
    "website_domain", "total_revenue", "mgmt_delegated",
)
PEOPLE_COLUMNS = (
    "name", "title", "avg_hours", "comp_org", "comp_related", "comp_other",
    "is_officer", "is_director", "is_key_employee", "is_hce",
)
UPSERT_FILING = f"""
    INSERT INTO filings (object_id, {', '.join(FILING_COLUMNS)}, source_zip, sha256, parser_version, parsed_at)
    VALUES (%s, {', '.join(['%s'] * len(FILING_COLUMNS))}, %s, %s, %s, now())
    ON CONFLICT (object_id) DO UPDATE SET
    {', '.join(f'{c} = EXCLUDED.{c}' for c in FILING_COLUMNS)},
    source_zip = EXCLUDED.source_zip, sha256 = EXCLUDED.sha256,
    parser_version = EXCLUDED.parser_version, parsed_at = now()
"""
INSERT_PERSON = f"""
    INSERT INTO filing_people (object_id, seq, {', '.join(PEOPLE_COLUMNS)})
    VALUES (%s, %s, {', '.join(['%s'] * len(PEOPLE_COLUMNS))})
"""


def _store(conn, object_id: str, zip_name: str, data: bytes, filing: dict, people: list[dict]) -> None:
    sha = hashlib.sha256(data).hexdigest()
    conn.execute(UPSERT_FILING, (object_id, *[filing[c] for c in FILING_COLUMNS], zip_name, sha, PARSER_VERSION))
    conn.execute("DELETE FROM filing_people WHERE object_id = %s", (object_id,))
    with conn.cursor() as cur:
        cur.executemany(INSERT_PERSON, [
            (object_id, i, *[p[c] for c in PEOPLE_COLUMNS]) for i, p in enumerate(people)
        ])


@asset(group_name="phase0", deps=[raw_returns], description="Parsed facts and people for each cached return.")
def parsed_filings(pg: PostgresResource) -> MaterializeResult:
    parsed = failed = people_rows = 0
    with pg.connect() as conn:
        selected = conn.execute(SELECT_LATEST).fetchall()
        conn.execute("DELETE FROM load_errors WHERE stage = %s", (STAGE,))
        # Drop filings that are no longer selected, for example because their
        # nonprofit left the candidate pool, so they can't match schools.
        pruned = conn.execute(
            "DELETE FROM filings WHERE NOT (object_id = ANY(%s))", ([r[0] for r in selected],)
        ).rowcount
        for object_id, zip_name, _ in selected:
            path = xml_path(object_id)
            if zip_name is None or not path.exists():
                continue  # the fetch failure is already logged
            data = path.read_bytes()
            try:
                filing, people = parse_return(data)
                # A savepoint per return: a database error rolls back this one only.
                with conn.transaction():
                    _store(conn, object_id, zip_name, data, filing, people)
            except Exception as exc:
                failed += 1
                pg.log_error(conn, STAGE, object_id, repr(exc))
                continue
            parsed += 1
            people_rows += len(people)

    return MaterializeResult(metadata={
        "parsed": parsed, "failed": failed, "people_rows": people_rows, "pruned": pruned,
    })
