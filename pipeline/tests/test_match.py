import re

from charter990.assets import match

PARAM = re.compile(r"%\((\w+)\)s")


def _statements():
    yield from match.SETUP
    yield from match.SIGNALS.values()
    yield from match.BUILD_PAIRS
    yield match.DISTRICT_GROUP
    yield match.BRAND_GROUP
    yield from match.ROLES
    yield from match.SAVE_ROLES
    yield from match.LINK_CORRECTIONS
    yield match.BUILD_STATUS


def test_every_parameter_is_defined():
    used = {name for sql in _statements() for name in PARAM.findall(sql)}
    assert used <= set(match.PARAMS), used - set(match.PARAMS)


def test_spread_signals_are_indirect():
    # brand_group and the district signals must lose ties to direct evidence.
    assert {"brand_group", "district_group", "district_name"} <= set(match.PARAMS["indirect"])


def test_holder_reads_every_holder_source():
    assert set(match.PARAMS["holder_sources"]) == {"tx_tea", "la_doe", "fl_ag", "fl_sunbiz", "website"}
    assert "%(holder_sources)s" in match.SIGNALS["holder"]


def test_website_and_phone_need_same_state_or_name():
    for signal in ("website", "phone"):
        assert match.SAME_STATE_OR_NAME in match.SIGNALS[signal]


def test_district_run_reason_sits_between_public_body_and_auditee():
    sql = match.BUILD_STATUS
    assert sql.index("'public_body_state'") < sql.index("'district_run_charter'") < sql.index("'government_auditee'")
