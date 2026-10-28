import pytest

from charter990.assets.extract import parse_return
from charter990.assets.zips import LABEL

RETURN = """﻿<?xml version="1.0" encoding="utf-8"?>
<Return xmlns="http://www.irs.gov/efile" returnVersion="2024v5.0">
  <ReturnHeader>
    <ReturnTs>2025-05-13T18:30:19-05:00</ReturnTs>
    <TaxPeriodEndDt>2024-06-30</TaxPeriodEndDt>
    <ReturnTypeCd>{rtype}</ReturnTypeCd>
    <TaxPeriodBeginDt>2023-07-01</TaxPeriodBeginDt>
    <Filer>{ein}<BusinessName><BusinessNameLine1Txt>EXAMPLE CHARTER SCHOOL</BusinessNameLine1Txt></BusinessName></Filer>
  </ReturnHeader>
  <ReturnData>
    <IRS990>
      <AmendedReturnInd>X</AmendedReturnInd>
      <WebsiteAddressTxt>WWW.EXAMPLE.ORG</WebsiteAddressTxt>
      <DelegationOfMgmtDutiesInd>true</DelegationOfMgmtDutiesInd>
      <CYTotalRevenueAmt>1234567</CYTotalRevenueAmt>
      <Form990PartVIISectionAGrp>
        <PersonNm>JANE DOE</PersonNm><TitleTxt>CEO</TitleTxt>
        <AverageHoursPerWeekRt>40.00</AverageHoursPerWeekRt><OfficerInd>X</OfficerInd>
        <ReportableCompFromOrgAmt>150000</ReportableCompFromOrgAmt>
        <ReportableCompFromRltdOrgAmt>0</ReportableCompFromRltdOrgAmt>
        <OtherCompensationAmt>12000</OtherCompensationAmt>
      </Form990PartVIISectionAGrp>
    </IRS990>
  </ReturnData>
</Return>"""


def build(rtype="990", ein="<EIN>123456789</EIN>") -> bytes:
    return RETURN.format(rtype=rtype, ein=ein).encode("utf-8")


def test_parses_core_facts():
    filing, people = parse_return(build())
    assert filing["ein"] == "123456789"
    assert filing["amended"] is True
    assert filing["mgmt_delegated"] is True
    assert filing["total_revenue"] == 1234567
    assert filing["website_domain"] == "example.org"
    assert people[0]["name"] == "JANE DOE" and people[0]["is_officer"]
    assert people[0]["comp_org"] == 150000 and people[0]["comp_other"] == 12000


def test_missing_ein_is_rejected():
    with pytest.raises(ValueError, match="EIN"):
        parse_return(build(ein=""))


def test_unsupported_return_type_is_rejected():
    with pytest.raises(ValueError, match="return type"):
        parse_return(build(rtype="990PF"))


def test_batch_label_pattern():
    assert LABEL.match("2026_TEOS_XML_05A").groups() == ("2026", "05")
    assert LABEL.match("2025_TEOS_XML_11D")
    assert not LABEL.match("download990xml_2020_1")
