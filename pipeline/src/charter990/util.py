"""Small helpers shared by the assets."""

from __future__ import annotations

import logging
import re
import time
import shutil
import tempfile
from pathlib import Path

import requests
import tldextract

from .settings import HTTP_HEADERS

log = logging.getLogger(__name__)

# Bundled public suffix list only: deterministic and works offline.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

# Registrable domains that say nothing about who runs a school: site builders,
# social media, government umbrellas and a for-profit virtual school operator.
EXCLUDED_DOMAINS = frozenset(
    {
        "google.com",
        "wixsite.com",
        "wix.com",
        "weebly.com",
        "squarespace.com",
        "wordpress.com",
        "facebook.com",
        "nyc.gov",
        "nysed.gov",
        "k12.com",
    }
)

_PLACEHOLDERS = {"", "n/a", "na", "none", "-", "null", "0"}
_TRUE_VALUES = {"1", "true", "x"}


def normalize_site(raw: str | None) -> tuple[str | None, str | None]:
    """Return (host, registrable domain) for a website value, or (None, None).

    Handles values such as "WWW.X.ORG", "HTTPS://NYC.UNCOMMONSCHOOLS.ORG/SCHOOLS/"
    and placeholders like "N/A".
    """
    if raw is None:
        return None, None
    value = raw.strip().lower()
    if value in _PLACEHOLDERS:
        return None, None
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
    host = re.split(r"[/?#:\s]", value, maxsplit=1)[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    if "." not in host or host in _PLACEHOLDERS:
        return None, None
    domain = _EXTRACT(host).top_domain_under_public_suffix or None
    return host, domain


def is_excluded(domain: str | None) -> bool:
    return domain is None or domain in EXCLUDED_DOMAINS


def parse_bool(value: str | None) -> bool | None:
    """IRS yes/no elements are 0/1 or true/false; checkboxes are a literal X."""
    if value is None:
        return None
    return value.strip().lower() in _TRUE_VALUES


# Responses that mean "try again later" rather than "this doesn't exist".
# Not 403: when the IRS host blocks a client it stays blocked for far longer
# than a retry waits, so retrying only stalls the run.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SourceUnavailable(Exception):
    """The source refused or failed repeatedly; callers may fall back to a cached copy."""


def head_with_retry(url: str, attempts: int = 3, backoff: float = 5.0) -> requests.Response:
    """HEAD with polite exponential backoff. Returns 2xx and 404 responses as is."""
    for attempt in range(attempts):
        resp = requests.head(url, headers=HTTP_HEADERS, timeout=30, allow_redirects=True)
        if resp.status_code < 400 or resp.status_code == 404:
            return resp
        if resp.status_code not in RETRYABLE_STATUS or attempt == attempts - 1:
            raise SourceUnavailable(f"HTTP {resp.status_code} for {url}")
        time.sleep(backoff * 3 ** attempt)
    raise SourceUnavailable(url)


def _remote_stamp(url: str) -> str | None:
    """The server's ETag, or Last-Modified when there is no ETag."""
    resp = head_with_retry(url)
    if resp.status_code == 404:
        raise SourceUnavailable(f"HTTP 404 for {url}")
    return resp.headers.get("ETag") or resp.headers.get("Last-Modified")


def download_cached(url: str, dest: Path, refresh: bool = False) -> Path:
    """Download url to dest, writing atomically.

    Without refresh, an existing file is reused as is. With refresh, the file is
    downloaded again whenever the server's ETag or Last-Modified has changed,
    which is how files the IRS rewrites in place every month stay current.
    """
    stamp_file = dest.with_name(dest.name + ".stamp")
    if dest.exists() and dest.stat().st_size > 0:
        if not refresh:
            return dest
        try:
            stamp = _remote_stamp(url)
        except (SourceUnavailable, requests.RequestException) as exc:
            # The source is unreachable right now: keep using the last good copy.
            log.warning("using cached %s: %s", dest.name, exc)
            return dest
        if stamp and stamp_file.exists() and stamp_file.read_text() == stamp:
            return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=HTTP_HEADERS, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        # Some servers gzip the transfer; save the decoded file, not the wire bytes.
        resp.raw.decode_content = True
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                shutil.copyfileobj(resp.raw, tmp, length=1 << 20)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
        stamp = resp.headers.get("ETag") or resp.headers.get("Last-Modified")
    tmp_path.replace(dest)
    if stamp:
        stamp_file.write_text(stamp)
    else:
        stamp_file.unlink(missing_ok=True)
    return dest


def decode_text(data: bytes) -> str:
    """CCD encoding is undocumented: try UTF-8 first, then Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


_WORD = re.compile(r"[A-Z0-9]+")

# Generic school words. The matcher compares names with these removed (the SQL
# name_core function takes this list as an argument), so "LAMAD ACADEMY CHARTER
# SCHOOL" is compared on LAMAD.
GENERIC_WORDS = tuple(
    """CHARTER CHARTERS SCHOOL SCHOOLS ACADEMY ACADEMIES PUBLIC PREPARATORY PREP
    COLLEGE HIGH MIDDLE ELEMENTARY COMMUNITY LEARNING CENTER CENTRE EDUCATION
    EDUCATIONAL FOUNDATION INTERNATIONAL OF FOR AND AT IN CS PCS""".split()
)

# State names by postal code. A name whose only distinctive word is a state
# ("TEXAS COLLEGE PREPARATORY ACADEMIES" reduces to TEXAS) says nothing about
# which organisation it is, and a filer named for another state ("IDEA PUBLIC
# SCHOOLS ARKANSAS") is that network's affiliate there.
US_STATES = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS", "CA": "CALIFORNIA",
    "CO": "COLORADO", "CT": "CONNECTICUT", "DE": "DELAWARE", "DC": "DISTRICT OF COLUMBIA",
    "FL": "FLORIDA", "GA": "GEORGIA", "HI": "HAWAII", "ID": "IDAHO", "IL": "ILLINOIS",
    "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY", "LA": "LOUISIANA",
    "ME": "MAINE", "MD": "MARYLAND", "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA",
    "MS": "MISSISSIPPI", "MO": "MISSOURI", "MT": "MONTANA", "NE": "NEBRASKA", "NV": "NEVADA",
    "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK",
    "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA", "OH": "OHIO", "OK": "OKLAHOMA", "OR": "OREGON",
    "PA": "PENNSYLVANIA", "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE", "TX": "TEXAS", "UT": "UTAH", "VT": "VERMONT", "VA": "VIRGINIA",
    "WA": "WASHINGTON", "WV": "WEST VIRGINIA", "WI": "WISCONSIN", "WY": "WYOMING",
}

# Words that open many unrelated names, so they can't identify a network.
NAME_STOPWORDS = frozenset(GENERIC_WORDS) | {"THE", "NEW", "YORK", "CITY", "INC", "FRIENDS"}


def first_word(name: str | None) -> str | None:
    """First word of a name that could identify a network, e.g. KIPP or SUCCESS."""
    if not name:
        return None
    for word in _WORD.findall(name.upper()):
        if len(word) >= 3 and not word.isdigit() and word not in NAME_STOPWORDS:
            return word
    return None


_LEGAL_SUFFIXES = frozenset("INC INCORPORATED CORP CORPORATION LLC LTD CO THE".split())


def name_key(name: str | None) -> str | None:
    """A comparable form of an organisation or school name.

    Uppercase, "&" as AND, punctuation removed, legal suffixes dropped. Generic
    words like CHARTER and SCHOOL stay: the ZIP or city requirement on every
    name signal already keeps unrelated schools apart.
    """
    if not name:
        return None
    words = [w for w in _WORD.findall(name.upper().replace("&", " AND ")) if w not in _LEGAL_SUFFIXES]
    return " ".join(words) or None


def phone10(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def zip5(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return digits[:5] if len(digits) >= 5 else None


# Parent groups, boosters, alumni and similar support organisations. They often
# share a school's name and website but never run the school.
_SUPPORT_ORG = re.compile(
    r"\b(BOOSTERS?|PTA|PTO|PTSA|PTSO|PARENT|PARENTS|ALUMNI|ATHLETIC|ATHLETICS|BAND|FRIENDS OF)\b"
)


def is_support_org(name: str | None) -> bool:
    return bool(name and _SUPPORT_ORG.search(name.upper()))


def meaningful_words(name: str | None) -> list[str]:
    if not name:
        return []
    return [w for w in _WORD.findall(name.upper()) if len(w) >= 3 and not w.isdigit() and w not in NAME_STOPWORDS]


def first_two(name: str | None) -> str | None:
    words = meaningful_words(name)
    return " ".join(words[:2]) if len(words) >= 2 else None


def network_key(name: str | None, first_counts, two_counts, limit: int) -> str | None:
    """The word or two that identify a network, such as KIPP or GREAT HEARTS.

    Use the first meaningful word when it is distinctive (at most `limit`
    nonprofits start with it); otherwise fall back to the first two words.
    """
    word = first_word(name)
    if word is None:
        return None
    if first_counts.get(word, 0) <= limit:
        return word
    two = first_two(name)
    if two is not None and two_counts.get(two, 0) <= limit:
        return two
    return None


# Part VII Section B contractors whose service is running schools: management
# companies and education service providers. Measured on the cached returns'
# service descriptions; "management" also describes investments, food service,
# facilities and construction, which are excluded. Bare "educational services"
# is mostly curriculum and special-education vendors (5 of 20 flagged in a spot
# check), so it counts only when the Schedule O explanation names the vendor.
_MGMT_SERVICE = re.compile(
    # MANAGE?ME?NT? also covers the misspellings and truncations filers type:
    # MANAGMENT, MANAGEMNT, "EDUCATION MANAGMEN".
    r"\b(MANAGE?ME?NT?|MGMT|MANAGER|OPERATOR|EMO|CMO)\b"
    r"|\bEDUCATION(AL)? (SERVICE PROVIDER|SUPPORT)\b"
)
_NOT_MGMT_SERVICE = re.compile(
    r"\b(INVESTMENTS?|FOOD|CAFETERIA|FACILITY|FACILITIES|PROPERTY|PROPERTIES|REAL ESTATE|CONSTRUCTION|"
    r"PROJECT|BUILDING|FINANCIAL|FINANCE|IT|TECHNOLOGY|EVENTS?|ENROLLMENT|RISK|ASSETS?|BUSINESS|"
    r"CONSULT\w*|SPECIAL|AFTER ?SCHOOL|EARLY CHILDHOOD|ONLINE|BUS|TRANSPORT\w*|LEGAL|ACCOUNT\w*|"
    r"PAYROLL|MARKETING|DATA|RECORDS|HEALTH|NURS\w*|THERAP\w*|ENERGY|WASTE|JANITORIAL|CLEANING|"
    r"SECURITY|FUND\w*|LEASE|RENT\w*)\b"
)


def is_management_service(services: str | None) -> bool:
    """Whether a contractor's service description reads like running schools."""
    text = " ".join((services or "").upper().split())
    return bool(_MGMT_SERVICE.search(text)) and not _NOT_MGMT_SERVICE.search(text)


# Schedule O line references to Part VI line 3, the delegation question, as
# filers write them: "Form 990, Part VI, Section A, line 3", "Part VI Line 3",
# "Core form, Part VI, Section A; question 3", "VI-3". Not line 13 or Part VII.
MGMT_EXPLANATION_REF = re.compile(
    r"PART\s*VI\b(?!I).*?\b(LINE|LN|L|QUESTION|Q)\s*\.?\s*3(?![0-9])"
    r"|\bVI[\s,-]*(SECTION\s*A[\s,-]*)?(LINE\s*)?3(?![0-9])",
    re.I | re.S,
)


def named_in(name: str | None, text: str | None) -> bool:
    """Whether an organisation's name appears in free text, compared as name keys
    ("ACADEMICA DADE LLC" is found in "Academica Dade, LLC, a professional...")."""
    key, haystack = name_key(name), name_key(text)
    return bool(key and haystack and len(key) >= 5 and f" {key} " in f" {haystack} ")


_STREET_TYPES = frozenset(
    """ST STREET AVE AV AVENUE RD ROAD BLVD BOULEVARD DR DRIVE LN LANE PL PLACE CT COURT PKWY PARKWAY
    HWY HIGHWAY CIR CIRCLE WAY TER TERRACE TRL TRAIL SQ SQUARE PIKE TPKE TURNPIKE EXPY EXPRESSWAY
    LOOP ROW ALY ALLEY PLZ PLAZA FWY FREEWAY""".split()
)
_DIRECTIONS = frozenset("N S E W NE NW SE SW NORTH SOUTH EAST WEST NORTHEAST NORTHWEST SOUTHEAST SOUTHWEST".split())
_UNIT_WORDS = frozenset("SUITE STE UNIT APT FL FLOOR BLDG BUILDING ROOM RM PO BOX".split())


def street_key(street: str | None) -> str | None:
    """Street number plus street name: "3725 Airport Blvd., Suite 100" -> "3725 AIRPORT".

    Street types, directions and unit numbers are dropped, so "542 7TH AVE W"
    and "542 West 7th Avenue" agree. Post office boxes have no key.
    """
    words = _WORD.findall((street or "").upper())
    if not words or not words[0][0].isdigit():
        return None
    name, directions = [], []
    for w in words[1:]:
        if w in _UNIT_WORDS or w in _STREET_TYPES:
            break
        (directions if w in _DIRECTIONS else name).append(w)
    # "108 NORTH STREET": the direction is the name.
    name = name or directions
    return f"{words[0]} {' '.join(name)}" if name else None


_HOLDER_ID = re.compile(r"\s*\(\d+\)\s*$")
_DBA = re.compile(r"\s+(?:DBA|D/B/A|D\.B\.A\.)\s+", re.I)


def holder_names(name: str | None) -> list[str]:
    """The legal names in a holder record: "Kaizen Education Foundation dba
    Vista Grove Prep (1234)" -> ["Kaizen Education Foundation", "Vista Grove Prep"].
    Drops the state ID that Arizona appends, and splits a DBA."""
    if not name:
        return []
    return [p.strip(" ,") for p in _DBA.split(_HOLDER_ID.sub("", name)) if p.strip(" ,")]
