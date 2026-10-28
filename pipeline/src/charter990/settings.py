"""Pipeline settings."""

import os
from pathlib import Path

# Every state by default. Set STATE_FILTER=NY (or another code) for a small,
# fast run, for example during a live demo.
STATE_FILTER = (os.environ.get("STATE_FILTER") or "").strip().upper() or None
# Read the previous year's index too: a nonprofit's newest return can sit there
# while the current year's index only holds a late filing for an older year.
INDEX_YEARS = (2025, 2026)

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "raw"

CCD_SCHOOL_URL = "https://nces.ed.gov/ccd/data/zip/ccd_sch_029_2526_w_0a_050626.zip"
# District (LEA) directory: tells us whether a school sits in its own charter
# district (LEA_TYPE 7) or inside a regular school district.
CCD_LEA_URL = "https://nces.ed.gov/ccd/data/zip/ccd_lea_029_2526_w_0a_050626.zip"
# The IRS Business Master File comes in four regional files that together
# cover every exempt organisation in the country.
BMF_URLS = tuple(f"https://www.irs.gov/pub/irs-soi/eo{i}.csv" for i in range(1, 5))
IRS_XML_BASE = "https://apps.irs.gov/pub/epostcard/990/xml"

# CCD UPDATED_STATUS codes that mean the school is operating:
# 1 Open, 3 New, 4 Added, 5 Changed boundary/agency, 8 Reopened.
OPERATING_STATUSES = {1, 3, 4, 5, 8}

HTTP_HEADERS = {"User-Agent": "charter990-takehome/0.1"}

# Statewide charter districts that hold unrelated schools under one NCES LEA:
# the district is an authorizer, not a network, so its name and its other
# schools say nothing about who runs a school in it.
UMBRELLA_LEAIDS = (
    "3200001",  # NV State Sponsored Charter Schools
    "0800020",  # CO State Charter School Institute
    "4503909",  # SC Charter Institute at Erskine
    "4503901",  # SC Public Charter School District
    "4700157",  # TN Public Charter School Commission
    "4503919",  # SC Limestone Charters Association
)

# Evidence about charter holders from outside the IRS data (assets/external.py).
# Federal Audit Clearinghouse, every single audit since 2016 (public domain).
FAC_GENERAL_URL = "https://app.fac.gov/dissemination/public-data/gsa/full/general.csv"
# Texas Education Agency "Charter Holder Information Report", exported to Excel
# by posting the report viewer's export request back to it.
TX_CHARTER_HOLDERS_URL = "https://pryor.tea.state.tx.us/Charter/Forms/ReportViewerPublic.aspx?reportid=rpt_sponsor.rpt"
# Louisiana Department of Education charter contact list (names each school's nonprofit).
LA_CHARTER_LIST_URL = (
    "https://doe.louisiana.gov/docs/default-source/school-choice/charter-contact-list.xlsx?sfvrsn=45ce6318_3"
)
# These sources publish without ETags we can rely on: re-download after this many days.
EXTERNAL_MAX_AGE_DAYS = 25
