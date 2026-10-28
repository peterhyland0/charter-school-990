from charter990.assets.related import parse_schedule_r, transaction_letters

RETURN = b"""<?xml version="1.0" encoding="utf-8"?>
<Return xmlns="http://www.irs.gov/efile" returnVersion="2024v5.0">
  <ReturnHeader><ReturnTypeCd>990</ReturnTypeCd></ReturnHeader>
  <ReturnData>
    <IRS990><DelegationOfMgmtDutiesInd>1</DelegationOfMgmtDutiesInd></IRS990>
    <IRS990ScheduleR>
      <IdRelatedTaxExemptOrgGrp>
        <DisregardedEntityName><BusinessNameLine1Txt>SUMMIT ACADEMY MANAGEMENT</BusinessNameLine1Txt></DisregardedEntityName>
        <USAddress><StateAbbreviationCd>OH</StateAbbreviationCd></USAddress>
        <EIN>341234567</EIN>
        <PrimaryActivitiesTxt>MANAGEMENT SERVICES</PrimaryActivitiesTxt>
        <LegalDomicileStateCd>OH</LegalDomicileStateCd>
        <DirectControllingNACd>N/A</DirectControllingNACd>
        <ControlledOrganizationInd>0</ControlledOrganizationInd>
      </IdRelatedTaxExemptOrgGrp>
      <IdRelatedTaxExemptOrgGrp>
        <DisregardedEntityName><BusinessNameLine1Txt>SUMMIT ACADEMY BUILDING CORP</BusinessNameLine1Txt></DisregardedEntityName>
        <EIN>34-1234</EIN>
        <PrimaryActivitiesTxt>REAL ESTATE</PrimaryActivitiesTxt>
        <LegalDomicileStateCd>OH</LegalDomicileStateCd>
        <DirectControllingEntityName><BusinessNameLine1Txt>SUMMIT ACADEMY - TOLEDO</BusinessNameLine1Txt></DirectControllingEntityName>
        <ControlledOrganizationInd>true</ControlledOrganizationInd>
      </IdRelatedTaxExemptOrgGrp>
      <IdRelatedOrgTxblCorpTrGrp>
        <RelatedOrganizationName><BusinessNameLine1Txt>APEX CHARTER SERVICES</BusinessNameLine1Txt><BusinessNameLine2Txt>LLC</BusinessNameLine2Txt></RelatedOrganizationName>
        <EIN>861234567</EIN>
        <PrimaryActivitiesTxt>Management company</PrimaryActivitiesTxt>
      </IdRelatedOrgTxblCorpTrGrp>
      <TransactionsRelatedOrgGrp>
        <OtherOrganizationName><BusinessNameLine1Txt>SUMMIT ACADEMY MANAGEMENT</BusinessNameLine1Txt></OtherOrganizationName>
        <TransactionTypeTxt>M</TransactionTypeTxt>
        <InvolvedAmt>1250000</InvolvedAmt>
      </TransactionsRelatedOrgGrp>
      <TransactionsRelatedOrgGrp>
        <OtherOrganizationName><BusinessNameLine1Txt>SUMMIT ACADEMY BUILDING CORP</BusinessNameLine1Txt></OtherOrganizationName>
        <TransactionTypeTxt>rent</TransactionTypeTxt>
        <InvolvedAmt>90000</InvolvedAmt>
      </TransactionsRelatedOrgGrp>
    </IRS990ScheduleR>
  </ReturnData>
</Return>
"""


def test_parses_related_orgs_from_each_part():
    orgs, _ = parse_schedule_r(RETURN)
    management, building, apex = orgs
    assert management == {
        "part": "II", "name": "SUMMIT ACADEMY MANAGEMENT", "name_key": "SUMMIT ACADEMY MANAGEMENT",
        "ein": "341234567", "state": "OH", "activity": "MANAGEMENT SERVICES", "controlled_by": None, "controlled": False,
    }
    # A malformed EIN is dropped rather than kept as a key that joins to nothing.
    assert building["ein"] is None and building["controlled_by"] == "SUMMIT ACADEMY - TOLEDO" and building["controlled"]
    # Part IV names its organisation differently, sometimes over two lines.
    assert apex["part"] == "IV" and apex["name"] == "APEX CHARTER SERVICES LLC"


def test_parses_transactions_with_form_letters():
    _, transactions = parse_schedule_r(RETURN)
    assert [(t["name"], t["type"], t["amount"]) for t in transactions] == [
        ("SUMMIT ACADEMY MANAGEMENT", "m", 1250000),
        ("SUMMIT ACADEMY BUILDING CORP", None, 90000),
    ]


def test_return_without_schedule_r_is_empty():
    assert parse_schedule_r(b"<Return><ReturnData><IRS990/></ReturnData></Return>") == ([], [])


def test_transaction_letters_read_free_text():
    assert transaction_letters("M") == "m"
    assert transaction_letters("1m") == "m"  # the line 1 prefix
    assert transaction_letters("b,c") == "bc"
    assert transaction_letters("o, p,") == "op"
    assert transaction_letters("LMQS") == "lmqs"
    assert transaction_letters("q(reimbursement)") == "q"


def test_transaction_words_are_not_letters():
    for value in ("rent", "loan", "contribution", "N/A", "", None):
        assert transaction_letters(value) is None, value
