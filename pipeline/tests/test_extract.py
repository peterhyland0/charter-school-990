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
      <CYTotalExpensesAmt>1100000</CYTotalExpensesAmt>
      <TotalAssetsEOYAmt>900000</TotalAssetsEOYAmt>
      <TotalLiabilitiesEOYAmt>400000</TotalLiabilitiesEOYAmt>
      <NetAssetsOrFundBalancesEOYAmt>500000</NetAssetsOrFundBalancesEOYAmt>
      <Form990PartVIISectionAGrp>
        <PersonNm>JANE DOE</PersonNm><TitleTxt>CEO</TitleTxt>
        <AverageHoursPerWeekRt>40.00</AverageHoursPerWeekRt><OfficerInd>X</OfficerInd>
        <ReportableCompFromOrgAmt>150000</ReportableCompFromOrgAmt>
        <ReportableCompFromRltdOrgAmt>0</ReportableCompFromRltdOrgAmt>
        <OtherCompensationAmt>12000</OtherCompensationAmt>
      </Form990PartVIISectionAGrp>
      <ContractorCompensationGrp>
        <ContractorName><BusinessName><BusinessNameLine1Txt>ACADEMICA DADE LLC</BusinessNameLine1Txt></BusinessName></ContractorName>
        <ServicesDesc>EDUCATIONAL SERVICE PROVIDER</ServicesDesc><CompensationAmt>10511248</CompensationAmt>
      </ContractorCompensationGrp>
      <ContractorCompensationGrp>
        <ContractorName><BusinessName><BusinessNameLine1Txt>CHENEY BROTHERS INC</BusinessNameLine1Txt></BusinessName></ContractorName>
        <ServicesDesc>FOOD SERVICE MANAGEMENT</ServicesDesc><CompensationAmt>4205214</CompensationAmt>
      </ContractorCompensationGrp>
    </IRS990>
    <IRS990ScheduleO>
      <SupplementalInformationDetail>
        <FormAndLineReferenceDesc>FORM 990, PART VI, SECTION A, LINE 3</FormAndLineReferenceDesc>
        <ExplanationTxt>ACADEMICA DADE, LLC PROVIDES PROFESSIONAL SERVICES TO THE SCHOOL.</ExplanationTxt>
      </SupplementalInformationDetail>
      <SupplementalInformationDetail>
        <FormAndLineReferenceDesc>FORM 990, PART VI, LINE 13</FormAndLineReferenceDesc>
        <ExplanationTxt>WHISTLEBLOWER POLICY.</ExplanationTxt>
      </SupplementalInformationDetail>
    </IRS990ScheduleO>
  </ReturnData>
</Return>"""


def build(rtype="990", ein="<EIN>123456789</EIN>") -> bytes:
    return RETURN.format(rtype=rtype, ein=ein).encode("utf-8")


def test_parses_core_facts():
    filing, people, contractors = parse_return(build())
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


def test_filer_contact_details():
    data = build(ein="<EIN>123456789</EIN><PhoneNum>6462777170</PhoneNum><USAddress><AddressLine1Txt>95 PINE ST</AddressLine1Txt><CityNm>NEW YORK</CityNm><StateAbbreviationCd>NY</StateAbbreviationCd><ZIPCd>100051234</ZIPCd></USAddress>")
    filing, _, _ = parse_return(data)
    assert filing["phone10"] == "6462777170"
    assert (filing["street"], filing["city"], filing["state"], filing["zip5"]) == ("95 PINE ST", "NEW YORK", "NY", "10005")
    assert filing["name_key"] == "EXAMPLE CHARTER SCHOOL"


def test_return_without_namespace_parses():
    data = build().replace(b' xmlns="http://www.irs.gov/efile"', b"")
    filing, people, _ = parse_return(data)
    assert filing["ein"] == "123456789" and people[0]["name"] == "JANE DOE"


def test_finances_contractors_and_management_explanation():
    filing, _, contractors = parse_return(build())
    assert (filing["total_expenses"], filing["total_assets"], filing["total_liabilities"], filing["net_assets"]) == (
        1100000, 900000, 400000, 500000)
    # Only the Part VI line 3 explanation, not line 13's.
    assert filing["mgmt_explanation"] == "ACADEMICA DADE, LLC PROVIDES PROFESSIONAL SERVICES TO THE SCHOOL."
    assert [(c["name"], c["compensation"], c["is_management"]) for c in contractors] == [
        ("ACADEMICA DADE LLC", 10511248, True),
        ("CHENEY BROTHERS INC", 4205214, False),
    ]


EZ_RETURN = """<Return xmlns="http://www.irs.gov/efile">
  <ReturnHeader><ReturnTypeCd>990EZ</ReturnTypeCd><TaxPeriodEndDt>2025-06-30</TaxPeriodEndDt>
    <Filer><EIN>987654321</EIN><BusinessName><BusinessNameLine1Txt>SMALL CHARTER</BusinessNameLine1Txt></BusinessName></Filer>
  </ReturnHeader>
  <ReturnData><IRS990EZ>
    <TotalRevenueAmt>150000</TotalRevenueAmt><TotalExpensesAmt>140000</TotalExpensesAmt>
    <Form990TotalAssetsGrp><BOYAmt>1</BOYAmt><EOYAmt>60000</EOYAmt></Form990TotalAssetsGrp>
    <SumOfTotalLiabilitiesGrp><BOYAmt>1</BOYAmt><EOYAmt>10000</EOYAmt></SumOfTotalLiabilitiesGrp>
    <NetAssetsOrFundBalancesGrp><BOYAmt>1</BOYAmt><EOYAmt>50000</EOYAmt></NetAssetsOrFundBalancesGrp>
  </IRS990EZ></ReturnData>
</Return>"""


def test_990ez_finances():
    filing, _, contractors = parse_return(EZ_RETURN.encode())
    assert (filing["total_expenses"], filing["total_assets"], filing["total_liabilities"], filing["net_assets"]) == (
        140000, 60000, 10000, 50000)
    assert filing["mgmt_delegated"] is None and filing["mgmt_explanation"] is None and contractors == []
