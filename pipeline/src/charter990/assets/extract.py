"""Pull the rep-facing facts out of each cached return."""

import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dagster import MaterializeResult, asset
from lxml import etree

from ..resources import PostgresResource
from ..util import (
    MGMT_EXPLANATION_REF,
    is_management_service,
    name_key,
    named_in,
    normalize_site,
    parse_bool,
    phone10,
    zip5,
)
from .returns import SELECT_LATEST, raw_returns, xml_path

PARSER_VERSION = "0.7"
STAGE = "parse"
# Returns are untrusted input: no entity expansion, no network access.
PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _strip_namespaces(root) -> None:
    """Most returns use the http://www.irs.gov/efile namespace but some omit it,
    so match on local tag names only."""
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{"):
            el.tag = el.tag.split("}", 1)[1]


def _text(node, *paths: str) -> str | None:
    """First non-empty text among the given paths (newer names first)."""
    for path in paths:
        found = node.find(path)
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


def _mgmt_explanation(root) -> str | None:
    """Schedule O text explaining Part VI line 3, the delegation question."""
    parts = [
        _text(d, "ExplanationTxt", "ShortExplanationTxt")
        for d in root.findall("ReturnData/IRS990ScheduleO/SupplementalInformationDetail")
        if MGMT_EXPLANATION_REF.search(_text(d, "FormAndLineReferenceDesc") or "")
    ]
    return "\n\n".join(p for p in parts if p) or None


# Long enough for a network's list of the schools it runs; some returns run to
# hundreds of thousands of characters of boilerplate.
SCHEDULE_O_MAX_CHARS = 8000


def _schedule_o_text(root) -> str | None:
    """Every Schedule O entry as "<line reference>: <explanation>", capped."""
    parts = []
    for d in root.findall("ReturnData/IRS990ScheduleO/SupplementalInformationDetail"):
        ref = _text(d, "FormAndLineReferenceDesc")
        explanation = _text(d, "ExplanationTxt", "ShortExplanationTxt")
        if explanation:
            parts.append(f"{ref}: {explanation}" if ref else explanation)
    return "\n\n".join(parts)[:SCHEDULE_O_MAX_CHARS] or None


def _contractors(body, explanation: str | None) -> list[dict]:
    """Part VII Section B: the five highest-paid independent contractors."""
    rows = []
    for g in body.findall("ContractorCompensationGrp"):
        name = " ".join(filter(None, (
            _text(g, "ContractorName/BusinessName/BusinessNameLine1Txt", "ContractorName/PersonNm"),
            _text(g, "ContractorName/BusinessName/BusinessNameLine2Txt"),
        ))) or None
        services = _text(g, "ServicesDesc")
        rows.append({
            "name": name,
            "services": services,
            "compensation": _int(_text(g, "CompensationAmt")),
            "is_management": is_management_service(services) or named_in(name, explanation),
        })
    return rows


def parse_return(data: bytes) -> tuple[dict, list[dict], list[dict]]:
    root = etree.fromstring(data, PARSER)
    _strip_namespaces(root)
    header = root.find("ReturnHeader")
    if header is None:
        raise ValueError("no ReturnHeader")
    return_type = _text(header, "ReturnTypeCd")
    if return_type not in ("990", "990EZ"):
        raise ValueError(f"unsupported return type {return_type!r}")
    is_ez = return_type == "990EZ"
    body = root.find("ReturnData/IRS990EZ" if is_ez else "ReturnData/IRS990")
    if body is None:
        raise ValueError(f"no form body for return type {return_type!r}")

    website = _text(body, "WebsiteAddressTxt")
    host, domain = normalize_site(website)
    org_name = _text(header, "Filer/BusinessName/BusinessNameLine1Txt", "Filer/BusinessName/BusinessNameLine1")
    dba_name = _text(body, "DoingBusinessAsName/BusinessNameLine1Txt")
    filing = {
        "ein": _text(header, "Filer/EIN"),
        "return_type": return_type,
        "return_version": root.get("returnVersion"),
        "tax_period_begin": _date(_text(header, "TaxPeriodBeginDt")),
        "tax_period_end": _date(_text(header, "TaxPeriodEndDt")),
        "return_ts": _ts(_text(header, "ReturnTs")),
        "amended": bool(parse_bool(_text(body, "AmendedReturnInd"))),
        "org_name": org_name,
        "dba_name": dba_name,
        "website": website,
        "website_host": host,
        "website_domain": domain,
        "total_revenue": _int(_text(body, "TotalRevenueAmt" if is_ez else "CYTotalRevenueAmt")),
        # End-of-year balances: Part X on the 990, Part II on the 990-EZ.
        "total_expenses": _int(_text(body, "TotalExpensesAmt" if is_ez else "CYTotalExpensesAmt")),
        "total_assets": _int(_text(body, "Form990TotalAssetsGrp/EOYAmt" if is_ez else "TotalAssetsEOYAmt")),
        "total_liabilities": _int(_text(body, "SumOfTotalLiabilitiesGrp/EOYAmt" if is_ez else "TotalLiabilitiesEOYAmt")),
        "net_assets": _int(_text(body, "NetAssetsOrFundBalancesGrp/EOYAmt", "NetAssetsOrFundBalancesEOYAmt")),
        "mgmt_explanation": None if is_ez else _mgmt_explanation(root),
        "schedule_o_text": _schedule_o_text(root),
        # 990-EZ does not ask whether management was delegated.
        "mgmt_delegated": None if is_ez else parse_bool(_text(body, "DelegationOfMgmtDutiesInd")),
        # The filer's own contact details, used as matching signals. Foreign
        # addresses have no USAddress block and stay empty.
        "phone10": phone10(_text(header, "Filer/PhoneNum")),
        "street": _text(header, "Filer/USAddress/AddressLine1Txt", "Filer/USAddress/AddressLine1"),
        "city": _text(header, "Filer/USAddress/CityNm", "Filer/USAddress/City"),
        "state": _text(header, "Filer/USAddress/StateAbbreviationCd", "Filer/USAddress/State"),
        "zip5": zip5(_text(header, "Filer/USAddress/ZIPCd", "Filer/USAddress/ZIPCode")),
        "name_key": name_key(org_name),
        "dba_key": name_key(dba_name),
    }

    people = []
    if is_ez:
        for g in body.findall("OfficerDirectorTrusteeEmplGrp"):
            benefits = (_int(_text(g, "EmployeeBenefitProgramAmt")) or 0) + (_int(_text(g, "ExpenseAccountOtherAllwncAmt")) or 0)
            people.append({
                "name": _person_name(g), "title": _text(g, "TitleTxt"),
                "avg_hours": _num(_text(g, "AverageHrsPerWkDevotedToPosRt")),
                "comp_org": _int(_text(g, "CompensationAmt")), "comp_related": None,
                "comp_other": benefits or None,
                "is_officer": False, "is_director": False, "is_key_employee": False, "is_hce": False,
            })
    else:
        for g in body.findall("Form990PartVIISectionAGrp"):
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
    contractors = [] if is_ez else _contractors(body, filing["mgmt_explanation"])
    return filing, people, contractors


FILING_COLUMNS = (
    "ein", "return_type", "return_version", "tax_period_begin", "tax_period_end",
    "return_ts", "amended", "org_name", "dba_name", "website", "website_host",
    "website_domain", "total_revenue", "mgmt_delegated",
    "total_expenses", "total_assets", "total_liabilities", "net_assets", "mgmt_explanation", "schedule_o_text",
    "phone10", "street", "city", "state", "zip5", "name_key", "dba_key",
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
CONTRACTOR_COLUMNS = ("name", "services", "compensation", "is_management")
INSERT_CONTRACTOR = f"""
    INSERT INTO filing_contractors (object_id, seq, {', '.join(CONTRACTOR_COLUMNS)})
    VALUES (%s, %s, {', '.join(['%s'] * len(CONTRACTOR_COLUMNS))})
"""
INSERT_PERSON = f"""
    INSERT INTO filing_people (object_id, seq, {', '.join(PEOPLE_COLUMNS)})
    VALUES (%s, %s, {', '.join(['%s'] * len(PEOPLE_COLUMNS))})
"""


def _store(conn, object_id: str, zip_name: str, data: bytes, filing: dict, people: list[dict],
           contractors: list[dict]) -> None:
    sha = hashlib.sha256(data).hexdigest()
    conn.execute(UPSERT_FILING, (object_id, *[filing[c] for c in FILING_COLUMNS], zip_name, sha, PARSER_VERSION))
    conn.execute("DELETE FROM filing_people WHERE object_id = %s", (object_id,))
    with conn.cursor() as cur:
        cur.executemany(INSERT_PERSON, [
            (object_id, i, *[p[c] for c in PEOPLE_COLUMNS]) for i, p in enumerate(people)
        ])
    conn.execute("DELETE FROM filing_contractors WHERE object_id = %s", (object_id,))
    with conn.cursor() as cur:
        cur.executemany(INSERT_CONTRACTOR, [
            (object_id, i, *[k[c] for c in CONTRACTOR_COLUMNS]) for i, k in enumerate(contractors)
        ])


@asset(group_name="pipeline", deps=[raw_returns], description="Parsed facts and people for each cached return.")
def parsed_filings(pg: PostgresResource) -> MaterializeResult:
    parsed = failed = people_rows = unchanged = 0
    with pg.connect() as conn:
        selected = conn.execute(SELECT_LATEST).fetchall()
        existing = dict(conn.execute(
            "SELECT object_id, sha256 FROM filings WHERE parser_version = %s", (PARSER_VERSION,)
        ).fetchall())
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
            if existing.get(object_id) == hashlib.sha256(data).hexdigest():
                unchanged += 1  # same file, same parser: nothing to redo
                continue
            try:
                filing, people, contractors = parse_return(data)
                # A savepoint per return: a database error rolls back this one only.
                with conn.transaction():
                    _store(conn, object_id, zip_name, data, filing, people, contractors)
            except Exception as exc:
                failed += 1
                pg.log_error(conn, STAGE, object_id, repr(exc))
                continue
            parsed += 1
            people_rows += len(people)

    return MaterializeResult(metadata={
        "parsed": parsed, "unchanged": unchanged, "failed": failed,
        "people_rows_written": people_rows, "pruned": pruned,
    })
