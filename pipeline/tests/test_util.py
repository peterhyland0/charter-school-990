from charter990.util import first_word, is_excluded, normalize_site, parse_bool


def test_normalize_strips_scheme_subdomain_and_path():
    assert normalize_site("HTTPS://NYC.UNCOMMONSCHOOLS.ORG/SCHOOLS/") == (
        "nyc.uncommonschools.org",
        "uncommonschools.org",
    )


def test_normalize_handles_www_and_bare_hosts():
    assert normalize_site("WWW.SUCCESSACADEMIES.ORG")[1] == "successacademies.org"
    assert normalize_site("http://www.successacademies.org/")[1] == "successacademies.org"


def test_placeholders_are_empty():
    for value in ["N/A", "n/a", "", "NONE", "-", "http://N/A", "http://www.", None]:
        assert normalize_site(value) == (None, None), value


def test_site_builders_are_excluded():
    _, domain = normalize_site("sites.google.com/x")
    assert domain == "google.com"
    assert is_excluded(domain)
    assert not is_excluded("successacademies.org")


def test_parse_bool_covers_irs_encodings():
    assert parse_bool("1") and parse_bool("true") and parse_bool("X")
    assert parse_bool("0") is False and parse_bool("false") is False
    assert parse_bool(None) is None


def test_first_word_skips_filler():
    assert first_word("KIPP NYC College Prep") == "KIPP"
    assert first_word("The New York City Charter School of the Arts") == "ARTS"
    assert first_word("Success Academy Charter School - Harlem 1") == "SUCCESS"


from charter990.util import first_two, is_support_org, name_key, network_key, phone10, zip5


def test_name_key_drops_legal_suffixes_and_punctuation():
    assert name_key("KIPP NYC College Prep") == "KIPP NYC COLLEGE PREP"
    assert name_key("The Academy Charter School, Inc.") == "ACADEMY CHARTER SCHOOL"
    assert name_key("Arts & Sciences Academy") == "ARTS AND SCIENCES ACADEMY"
    assert name_key("") is None


def test_phone_and_zip():
    assert phone10("(718)635-3294") == "7186353294"
    assert phone10("1-646-277-7170") == "6462777170"
    assert phone10("635-3294") is None
    assert zip5("10005-1234") == "10005"
    assert zip5("123") is None


def test_support_orgs():
    assert is_support_org("BASIS Scottsdale Boosters")
    assert is_support_org("FRIENDS OF THE HARLEM ACADEMY")
    assert is_support_org("Lincoln Charter School PTO Inc")
    assert not is_support_org("Great Hearts Academies - Veritas")
    assert not is_support_org("Bandera Charter School")  # BAND is a whole word only


def test_network_key_falls_back_to_two_words():
    firsts = {"KIPP": 56, "GREAT": 1724}
    twos = {"GREAT HEARTS": 12}
    assert network_key("KIPP NYC College Prep", firsts, twos, 200) == "KIPP"
    assert network_key("Great Hearts Academies - Veritas", firsts, twos, 200) == "GREAT HEARTS"
    assert first_two("Great Hearts Academies - Veritas") == "GREAT HEARTS"
    assert network_key("Great Oaks Charter School", firsts, {"GREAT OAKS": 500}, 200) is None


def test_management_service_rule():
    from charter990.util import is_management_service

    for yes in ("EDUCATIONAL SERVICE PROVIDER", "MANAGEMENT SERVICES", "Management fees", "CHARTER SCHOOL MANAGEMENT",
                "EDUCATION MGMT", "EDUCATIONAL SUPPORT", "CMO MANAGEMENT", "CHARTER MANAGMENT ORGANIZATION",
                "EDUCATION MANAGMEN"):
        assert is_management_service(yes), yes
    for no in ("PROPERTY MANAGEMENT", "CONSTRUCTION MANAGEMENT", "FOOD SERVICE MANAGEMENT", "INVESTMENT MANAGEMENT",
               "SPECIAL EDUCATION SERVICES", "EDUCATION CONSULTING", "EDUCATIONAL SERVICES", "MANAGED IT", "MANAGE", "JANITORIAL SERVICES", "MANAGED IT SERVICES", None):
        assert not is_management_service(no), no


def test_management_explanation_reference_and_names():
    from charter990.util import MGMT_EXPLANATION_REF, named_in

    for ref in ("FORM 990, PART VI, SECTION A, LINE 3", "Part VI Line 3", "Form 990, Part VI, Line 3 Delegation of management duties",
                "CORE FORM, PART VI, SECTION A; QUESTION 3", "VI-3"):
        assert MGMT_EXPLANATION_REF.search(ref), ref
    for ref in ("Form 990, Part VI, Line 13", "Form 990, Part VII, Section A, Line 3", "Part VI, Section B, Line 11b"):
        assert not MGMT_EXPLANATION_REF.search(ref), ref
    assert named_in("ACADEMICA DADE LLC", "Academica Dade, LLC, a professional charter school support organization")
    assert not named_in("ADP", "ADP is too short to trust")
    assert not named_in("ACADEMICA BROWARD LLC", "Academica Dade, LLC")
