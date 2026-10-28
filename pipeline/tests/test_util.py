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
