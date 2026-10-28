import io

import openpyxl

from charter990.assets.governance import ca_charters, mps_charters, mps_status, wi_charters

CA_HEADER = "CDSCode\tNCESDist\tNCESSchool\tStatusType\tDistrict\tSchool\tCharter\tCharterNum\tFundingType\tDOCType"


def _ca(*rows: str) -> str:
    return "\n".join([CA_HEADER, *rows]) + "\n"


def test_ca_charters_reads_funding_type_as_governance():
    text = _ca(
        "34752830108860\t0634410\t11751\tActive\tNatomas Unified\tWestlake Charter\tY\t0857\tLocally funded\tUnified School District",
        "01100170106906\t0691051\t10720\tActive\tAlameda COE\tBay Area Technology\tY\t0661\tDirectly funded\tCounty Office of Education (COE)",
        # Not a charter, no funding model, or a district row: skipped.
        "01100170130401\t0691051\t13008\tActive\tAlameda COE\tA District School\tN\tNo Data\tNo Data\tCOE",
        "19647330000000\t0622710\tNo Data\tActive\tLos Angeles Unified\tNo Data\tY\t0001\tLocally funded\tUnified",
        "01100170000001\t\t\tClosed\tX\tOld Charter\tY\t0002\tNo Data\tUnified",
    )
    rows = ca_charters(text)
    assert set(rows) == {"0108860", "0106906"}
    assert rows["0108860"]["governance"] == "district_run"
    assert rows["0108860"]["nces_id"] == "063441011751"
    assert rows["0108860"]["authorizer"] == "Natomas Unified"
    assert rows["0106906"]["governance"] == "independent"


def test_ca_charters_prefers_the_active_row_for_a_reused_code():
    text = _ca(
        "11111110123456\t\t\tActive\tA\tNew\tY\t1\tLocally funded\tUnified",
        "22222220123456\t\t\tClosed\tB\tOld\tY\t2\tDirectly funded\tUnified",
    )
    assert ca_charters(text)["0123456"]["authorizer"] == "A"


def _wi_xlsx(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None])
    ws.append(["2026-27 Charter Schools"])
    ws.append(["District/Authorizer", "Charter School Name", "New School", "LEA Code", "CESA", "School Code",
               "School Type", "Agency Type Description"])
    for r in rows:
        ws.append(r)
    ws.append(["Last Updated September 2026"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_wi_charters_keys_on_the_nces_state_id():
    data = _wi_xlsx([
        ["Algoma", "Algoma Venture Academy", "No", "0070", "07", "0400", "High School", "Public School"],
        ["City of Milwaukee", "Darrell Lynn Hines Academy", "No", 8009, "01", 8109, "Elementary School",
         "Independent Charter School"],
    ])
    rows = wi_charters(data)
    assert rows["WI-0070-0400"]["governance"] == "district_run"
    assert rows["WI-0070-0400"]["authorizer"] == "Algoma"
    # Excel hands codes back as numbers: zero-padded to four digits.
    assert rows["WI-8009-8109"]["governance"] == "independent"
    assert len(rows) == 2


MPS_PAGE = """
<h2>Charter Schools</h2>
<h3 class="fsElementTitle">Instrumentality Charter Schools</h3>
<a role="tab" href="#p1">Academia de Lenguaje y Bellas Artes (ALBA): K3&ndash;8</a>
<a role="tab" href="#p2">GreenTree Preparatory Academy: 6–12</a>
<a role="tab" href="#p3">Whittier Elementary School: K4–5</a>
<a role="tab" href="#p4">Individualized Developmental Educational Approaches to Learning (I.D.E.A.L.): K4–8</a>
<h3 class="fsElementTitle">Non-Instrumentality Charter Schools </h3>
<a role="tab" href="#p5">La Causa Charter School (two locations)</a>
<a role="tab" href="#p6">Next Door Charter School: K4 (multiple locations) </a>
<h3>Partnership Schools</h3>
<a role="tab" href="#p7">Some Partnership School</a>
"""


def test_mps_charters_reads_both_lists():
    mps = mps_charters(MPS_PAGE)
    assert mps == {
        "Academia de Lenguaje y Bellas Artes (ALBA)": True,
        "GreenTree Preparatory Academy": True,
        "Whittier Elementary School": True,
        "Individualized Developmental Educational Approaches to Learning (I.D.E.A.L.)": True,
        "La Causa Charter School": False,
        "Next Door Charter School": False,
    }


def test_mps_status_matches_dpi_names_to_the_mps_list():
    mps = mps_charters(MPS_PAGE)
    assert mps_status("ALBA - Academia de Lenguaje y Bellas", mps)[1] is True
    assert mps_status("Green Tree Preparatory Academy", mps)[0] == "GreenTree Preparatory Academy"
    assert mps_status("Whittier Elementary", mps)[1] is True
    assert mps_status("IDEAL Individualized Developmental", mps)[1] is True
    assert mps_status("Next Door Charter", mps) == ("Next Door Charter School", False)
    assert mps_status("La Causa Charter School", mps)[1] is False
    # One shared word is not enough.
    assert mps_status("Milwaukee Academy of Chinese Language", mps) is None
    assert mps_status("Charter School", mps) is None
