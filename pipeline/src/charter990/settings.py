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
