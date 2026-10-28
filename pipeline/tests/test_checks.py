from charter990.checks import DRIFT_FIELDS, empty_rate_jumps, version_key


def _row(return_type, version, n, **empty):
    return (return_type, version, n, {f: empty.get(f, 0) for f in DRIFT_FIELDS})


def test_versions_sort_numerically_and_unknown_last():
    versions = ["2024v5.5", None, "2024v10.0", "2023v6.0", "2025v4.0"]
    assert sorted(versions, key=version_key) == ["2023v6.0", "2024v5.5", "2024v10.0", "2025v4.0", None]


def test_renamed_field_is_flagged():
    rows = [
        _row("990", "2024v5.2", 7000, website=500),
        _row("990", "2025v4.0", 1200, website=100),
        # The new version's website element was renamed: every return comes back empty.
        _row("990", "2026v1.0", 300, website=300),
    ]
    assert empty_rate_jumps(rows) == [("990", "2026v1.0", "website", 1.0, 600 / 8200)]


def test_natural_variation_is_not_flagged():
    # Phone is missing on 47% of one real 990-EZ version against about 11% before.
    rows = [_row("990EZ", "2024v5.0", 1080, phone10=123), _row("990EZ", "2024v5.1", 339, phone10=158)]
    assert empty_rate_jumps(rows) == []


def test_ez_never_has_the_delegation_answer():
    rows = [_row("990EZ", "2024v5.0", 900, mgmt_delegated=900), _row("990EZ", "2025v4.0", 1000, mgmt_delegated=1000)]
    assert empty_rate_jumps(rows) == []


def test_delegation_drift_is_flagged_on_the_990():
    rows = [_row("990", "2024v5.0", 4000), _row("990", "2025v4.0", 1200, mgmt_delegated=1200)]
    assert [j[2] for j in empty_rate_jumps(rows)] == ["mgmt_delegated"]


def test_small_versions_are_skipped_but_still_count_as_earlier():
    rows = [
        _row("990", "2023v6.0", 400),
        _row("990", "2024v5.0", 10, zip5=10),  # too few returns to judge
        _row("990", "2025v4.0", 60, zip5=60),
    ]
    assert [(j[1], j[2]) for j in empty_rate_jumps(rows)] == [("2025v4.0", "zip5")]


def test_forms_are_compared_separately():
    # Website has always been empty on these 990-EZs; the full 990s in the
    # same version must not make the newer 990-EZ look like a change.
    rows = [_row("990", "2024v5.0", 500), _row("990EZ", "2024v5.0", 500, website=500),
            _row("990EZ", "2025v4.0", 500, website=500)]
    assert empty_rate_jumps(rows) == []
