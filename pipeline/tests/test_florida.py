from charter990.assets.florida import (
    FIC_OWNER_LEN,
    FIC_OWNERS_AT,
    SELF,
    ag_reports,
    ag_school_pages,
    county_key,
    fic_record,
    report_county,
    report_entity,
    report_school_numbers,
)


def test_index_and_school_pages():
    index = """<li><a href="chschools_efile%20pages/academy%20at%20the%20farm.htm">Academy
        at the Farm</a></li><a href="../index.html">Home</a>"""
    assert ag_school_pages(index) == [
        ("https://flauditor.gov/pages/chschools_efile%20pages/academy%20at%20the%20farm.htm", "Academy at the Farm")]
    page = """<h5>2022-23 Fiscal Year</h5><a href="../chschools_efile%20rpts/2023%20x.pdf">Original Report</a>
        <h5>2021-22 Fiscal Year</h5><a href="../chschools_efile%20rpts/2022%20x.pdf">Original Report</a>"""
    url = "https://flauditor.gov/pages/chschools_efile%20pages/x.htm"
    assert ag_reports(page, url) == [
        (2023, "https://flauditor.gov/pages/chschools_efile%20rpts/2023%20x.pdf"),
        (2022, "https://flauditor.gov/pages/chschools_efile%20rpts/2022%20x.pdf")]


def test_report_entity_patterns():
    # Operated by another corporation, with an abbreviation and broken spacing.
    text = ("NOTE – 1 ORGANIZATION Organization Academy for Innovative Education (the \"School\") is operated by "
            "Miami- Dade Foundation for Educational Innovation, Inc. (MDFEI), a not -for-profit corporation")
    assert report_entity(text) == ("Miami-Dade Foundation for Educational Innovation, Inc.", 1)
    # The cover page's "charter school under".
    cover = "ADVANTAGE ACADEMY OF MATH\nAND SCIENCE AT WATERSTONE\n(A CHARTER SCHOOL UNDER\nADVANTAGE ACADEMY OF MIAMI, INC.)"
    assert report_entity(cover)[0] == "ADVANTAGE ACADEMY OF MIAMI, INC."
    # A D/B/A on the cover.
    assert report_entity("ACADEMIR CHARTER SCHOOLS, INC.\nD/B/A ACADEMIR CHARTER SCHOOL WEST")[0] == (
        "ACADEMIR CHARTER SCHOOLS, INC.")
    # The school is the corporation; headings that run into the name are dropped.
    text = ("NOTE A – SUMMARY OF SIGNIFICANT ACCOUNTING POLICIES 1. Reporting Entity Academy at the Farm, Inc., "
            "(the \"School\") is a not-for-profit corporation organized pursuant to Chapter 617")
    assert report_entity(text) == ("Academy at the Farm, Inc.", 5)
    # A word split by the PDF's spacing, mended from its spelling elsewhere.
    text = ("Beulah Academy of Science Beulah Academy of Science NOTES T he Reporting Entity: Be ulah Academy of "
            "Science, Inc. (the “Academy”) is a not-for-profit corporation")
    assert report_entity(text)[0] == "Beulah Academy of Science, Inc."
    # "The School is a not-for-profit corporation": its own holder.
    assert report_entity("Reporting Entity The School is a not-for-profit corporation organized") == (SELF, 7)
    # A sentence before the subject is not part of the name.
    assert report_entity("Income Taxes The Academy is a nonprofit corporation whose revenues") == (SELF, 7)


def test_report_entity_last_resort_skips_management_companies():
    text = "Charter Schools USA, Inc. manages. Charter Schools USA, Inc. again. Sample School, Inc. Sample School, Inc."
    assert report_entity(text)[0] == "Sample School, Inc."


def test_report_county_and_numbers():
    assert report_county("A Charter School and Component Unit of the District School Board of Miami-Dade County, "
                         "Florida") == "MIAMIDADE"
    assert report_county("(A CHARTER SCHOOL AND COMPONENT UNIT OF PALM BEACH\nCOUNTY DISTRICT SCHOOL BOARD)") == (
        "PALMBEACH")
    assert county_key("ST. LUCIE") == county_key("Saint Lucie") == "STLUCIE"
    assert county_key("Dade") == county_key("MIAMI-DADE")
    assert report_school_numbers("Academic Solutions Academy - A #5233 (the School) and School No. 0123") == {
        "5233", "0123"}


def _owner(name, fmt, fei=""):
    block = [" "] * FIC_OWNER_LEN
    for start, value in ((12, name), (67, fmt), (150, fei)):
        block[start:start + len(value)] = value
    return "".join(block)


def test_fic_record_reads_corporate_owners():
    head = "L19000012345" + "SOMERSET ACADEMY AVENIR".ljust(192) + "DADE".ljust(12)
    line = head.ljust(FIC_OWNERS_AT) + _owner("SOMERSET ACADEMY, INC.", "C", "650812434") + _owner("JANE DOE", "P")
    assert fic_record(line) == ("SOMERSET ACADEMY AVENIR", "DADE", [("SOMERSET ACADEMY, INC.", "650812434")])
    # Only people own it: nothing to link.
    assert fic_record(head.ljust(FIC_OWNERS_AT) + _owner("JANE DOE", "P")) is None


def test_report_entity_trims_addressees_and_appositives():
    assert report_entity("To the Board of Directors of United Cerebral Palsy of South Florida, Inc. D/B/A Early "
                         "Beginnings Academy")[0] == "United Cerebral Palsy of South Florida, Inc."
    assert report_entity("Marco Island Academy, A Public Charter High School, Inc. (the School) is a "
                         "not-for-profit corporation")[0] == "Marco Island Academy"
    assert report_entity("is a charter school under Literacy/Leadership/Technology Academy, Inc., a "
                         "not-for-profit")[0] == "Literacy/Leadership/Technology Academy, Inc."
    # A legal name beats a short form found as often.
    text = ("The Pepin Academies, Inc. (“Pepin” or “School”) is a not-for-profit corporation formed. The governing "
            "body of Pepin is a not-for-profit corporation board")
    assert report_entity(text)[0] == "The Pepin Academies, Inc."
