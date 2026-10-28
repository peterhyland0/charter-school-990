"""Phase 0 settings. Phase 1 removes the single-state restriction."""

from pathlib import Path

STATE = "NY"
# Read the previous year's index too: a nonprofit's newest return can sit there
# while the current year's index only holds a late filing for an older year.
INDEX_YEARS = (2025, 2026)

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "raw"

CCD_SCHOOL_URL = "https://nces.ed.gov/ccd/data/zip/ccd_sch_029_2526_w_0a_050626.zip"
# Region 1 (Northeast) of the IRS Business Master File. Covers New York and
# the neighbouring states where some New York networks are registered.
BMF_URL = "https://www.irs.gov/pub/irs-soi/eo1.csv"
IRS_XML_BASE = "https://apps.irs.gov/pub/epostcard/990/xml"

# CCD UPDATED_STATUS codes that mean the school is operating:
# 1 Open, 3 New, 4 Added, 5 Changed boundary/agency, 8 Reopened.
OPERATING_STATUSES = {1, 3, 4, 5, 8}

HTTP_HEADERS = {"User-Agent": "charter990-takehome/0.1"}
