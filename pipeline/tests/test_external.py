import io

import openpyxl

from charter990.assets.external import la_nonprofits, tx_holder_row
from charter990.util import holder_names


def test_holder_names_drop_the_state_id_and_split_a_dba():
    assert holder_names("Center for Academic Success Inc. (4191)") == ["Center for Academic Success Inc."]
    assert holder_names("Kaizen Education Foundation dba Vista Grove Prepar (1234)") == [
        "Kaizen Education Foundation", "Vista Grove Prepar"]
    assert holder_names("FIT KIDS, INC. DBA CHAMPION SCHOOLS") == ["FIT KIDS, INC.", "CHAMPION SCHOOLS"]
    # A number that isn't a bracketed ID is part of the name.
    assert holder_names("ICAHN CHARTER SCHOOL 4") == ["ICAHN CHARTER SCHOOL 4"]
    assert holder_names(None) == [] and holder_names("") == []


def test_tx_holder_rows():
    assert tx_holder_row(["A+ ACADEMY", "057829", "A+ CHARTER SCHOOLS, INC.", "10327 Rylie Rd"]) == (
        "057829", "A+ ACADEMY", "A+ CHARTER SCHOOLS, INC.")
    # Excel can hand the county-district number back as a number.
    assert tx_holder_row(["GEORGE GERVIN ACADEMY", 15802.0, "GEORGE GERVIN YOUTH CENTER"])[0] == "015802"
    assert tx_holder_row(["CHARTER HOLDER", "", ""]) is None
    assert tx_holder_row(["x"]) is None


def test_la_nonprofits_reads_name_nonprofit_and_zip():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["School Name", "Non-Profit Name", "Grades", "Charter Type", "School Address"])
    ws.append(["Abramson Sci Academy", "Collegiate Academies ", "9-12", "3B", "5552 Read Boulevard\nNew Orleans, LA 70127"])
    ws.append(["No Nonprofit Listed", None, "K-8", "2", "1 Main St, Monroe, LA 71201"])
    buf = io.BytesIO()
    wb.save(buf)
    assert la_nonprofits(buf.getvalue()) == [("Abramson Sci Academy", "Collegiate Academies", "70127")]
