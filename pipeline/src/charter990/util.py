"""Small helpers shared by the assets."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import requests
import tldextract

from .settings import HTTP_HEADERS

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


def download_cached(url: str, dest: Path) -> Path:
    """Download url to dest unless it is already there. Writes atomically."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=HTTP_HEADERS, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        # Some servers gzip the transfer; save the decoded file, not the wire bytes.
        resp.raw.decode_content = True
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            shutil.copyfileobj(resp.raw, tmp, length=1 << 20)
            tmp_path = Path(tmp.name)
    tmp_path.replace(dest)
    return dest


def decode_text(data: bytes) -> str:
    """CCD encoding is undocumented: try UTF-8 first, then Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


_WORD = re.compile(r"[A-Z0-9]+")

# Words that open many unrelated names, so they can't identify a network.
NAME_STOPWORDS = frozenset(
    """THE NEW YORK CITY CHARTER CHARTERS SCHOOL SCHOOLS ACADEMY ACADEMIES PUBLIC
    HIGH PREP PREPARATORY COMMUNITY INTERNATIONAL OF FOR AND AT IN CS INC
    ELEMENTARY MIDDLE LEARNING CENTER CENTRE EDUCATION EDUCATIONAL FOUNDATION
    FRIENDS""".split()
)


def first_word(name: str | None) -> str | None:
    """First word of a name that could identify a network, e.g. KIPP or SUCCESS."""
    if not name:
        return None
    for word in _WORD.findall(name.upper()):
        if len(word) >= 3 and not word.isdigit() and word not in NAME_STOPWORDS:
            return word
    return None
