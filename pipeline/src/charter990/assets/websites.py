"""Charter holders named on the schools' own websites.

A school whose nonprofit has a different name, address and website from the
school's own is invisible to the name, address and website signals. The school
site often says who holds it anyway, in a footer, privacy, about or donate
page: "(c) 2025 Somerset Academy, Inc.", "X is a 501(c)(3) nonprofit",
"Tax ID 12-3456789", "a public charter school governed by X".

For each unmatched school with a site of its own (not a district's, not a site
builder's) this reads at most MAX_PAGES public pages: the home page and the
links whose text or address suggests about, privacy, donate, board, governance
and the like. It respects robots.txt, waits HOST_INTERVAL between requests to
one host, never leaves the site's registrable domain, and caches every crawl
under data/raw/websites/ so a rerun reads the cache. HTML is parsed with lxml,
never executed.

Each page yields candidates: EINs next to words like EIN or Tax ID, copyright
holders, "X is a 501(c)(3)", "operated by / governed by X", and legal-looking
names ending in Inc, Corporation, Foundation or Association. Management
companies and website vendors (Academica, Charter Schools USA, Stride, Edlio,
Finalsite...) name themselves in footers too: they are dropped, as are
authorizers ("authorized by"), and "managed by" names, which are operators
under contract rather than holders.

website_rows turns the rest into external_links rows (source 'website'): the
holder's name, its EIN when the site gives one, and the page and snippet.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import lxml.html
import requests

from ..settings import HTTP_HEADERS, RAW_DIR
from ..util import EXCLUDED_DOMAINS, GENERIC_WORDS, US_STATES, _EXTRACT, holder_names, is_support_org, name_key

log = logging.getLogger(__name__)

WEB_DIR = RAW_DIR / "websites"
MAX_PAGES = 5
PAGE_TIMEOUT = 15
MAX_PAGE_BYTES = 1 << 20
MAX_REDIRECTS = 5
# Seconds between two requests to the same host.
HOST_INTERVAL = 1.0
WORKERS = 16
# A cached crawl is reused for this long.
WEB_MAX_AGE_DAYS = 180

# Unmatched for these reasons, the school has no nonprofit to find.
SKIP_REASONS = ("district_governed_state", "public_body_state", "government_auditee", "uses_district_website",
                "district_run_charter")

ELIGIBLE_SCHOOLS = """
    SELECT s.ncessch, s.st, s.sch_name, s.website, s.website_domain
    FROM school_status st JOIN schools s USING (ncessch)
    WHERE ((st.status = 'unmatched' AND NOT (coalesce(st.reason, '') = ANY(%(skip)s)))
           -- Once a school's site has named a holder it stays in, so the link
           -- it makes doesn't take the school out of the next run and back.
           OR s.ncessch IN (SELECT ncessch FROM external_links WHERE source = 'website'))
      AND s.website_domain IS NOT NULL AND NOT (s.website_domain = ANY(%(excluded)s))
      AND s.website_domain NOT IN (SELECT domain FROM district_domains)
    ORDER BY s.ncessch
"""


def eligible_schools(conn) -> list[tuple]:
    return conn.execute(ELIGIBLE_SCHOOLS, {"skip": list(SKIP_REASONS), "excluded": sorted(EXCLUDED_DOMAINS)}).fetchall()


# ---------------------------------------------------------------------------
# Crawling

# Links worth following, best first: a donate page is where an EIN is printed,
# privacy and terms pages name the legal entity.
LINK_TOPICS = (
    ("donate", r"donat|\bgive\b|giving|support[- ]us"),
    ("privacy", r"privacy"),
    ("terms", r"terms|legal|disclaimer"),
    ("about", r"about"),
    ("board", r"board|trustee|directors"),
    ("governance", r"governance|governing"),
    ("financial", r"financ|budget|audit|990"),
    ("transparency", r"transparen|accountab|public[- ]records|notices"),
    ("contact", r"contact"),
)
_TOPIC_RES = [(t, re.compile(p, re.I)) for t, p in LINK_TOPICS]
_SKIP_EXT = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|jpe?g|png|gif|svg|zip|mp[34]|mov|ics|css|js)$", re.I)
_HTML_TYPES = ("text/html", "application/xhtml")


def start_url(website: str) -> str:
    url = website.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "http://" + url
    return urldefrag(url)[0]


def registrable(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    return _EXTRACT(host).top_domain_under_public_suffix or None if host else None


class Throttle:
    """At most one request per `interval` seconds to each host, across threads."""

    def __init__(self, interval: float = HOST_INTERVAL):
        self.interval, self._next, self._lock = interval, {}, Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next.get(host, 0.0))
            self._next[host] = at + self.interval
        if at > now:
            time.sleep(at - now)


@dataclass
class Fetched:
    url: str
    status: int | None = None
    html: str | None = None
    error: str | None = None


class Crawler:
    def __init__(self, throttle: Throttle | None = None):
        self.throttle = throttle or Throttle()
        self.session = requests.Session()
        self.session.headers.update(HTTP_HEADERS)
        self.agent = HTTP_HEADERS["User-Agent"]
        self._robots: dict[str, RobotFileParser] = {}
        self._robots_lock = Lock()

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._robots_lock:
            if origin in self._robots:
                return self._robots[origin]
        rp = RobotFileParser(origin + "/robots.txt")
        try:
            self.throttle.wait(parts.netloc)
            resp = self.session.get(origin + "/robots.txt", timeout=PAGE_TIMEOUT)
            if resp.status_code in (401, 403):
                rp.disallow_all = True
            elif resp.status_code < 400:
                rp.parse(resp.text[:MAX_PAGE_BYTES].splitlines())
            else:
                rp.allow_all = True
        except requests.RequestException:
            # No answer: robotparser's own convention is to allow.
            rp.allow_all = True
        with self._robots_lock:
            self._robots[origin] = rp
        return rp

    def get(self, url: str, domain: str) -> Fetched:
        """GET an HTML page, following redirects only within `domain` and only
        where robots.txt allows, reading at most MAX_PAGE_BYTES."""
        for _ in range(MAX_REDIRECTS + 1):
            if registrable(url) != domain:
                return Fetched(url, error="off_domain")
            if not self._robots_for(url).can_fetch(self.agent, url):
                return Fetched(url, error="robots")
            self.throttle.wait(urlsplit(url).netloc)
            try:
                with self.session.get(url, timeout=PAGE_TIMEOUT, stream=True, allow_redirects=False) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        url = urldefrag(urljoin(url, resp.headers["location"]))[0]
                        continue
                    if resp.status_code >= 400:
                        return Fetched(url, resp.status_code, error=f"http_{resp.status_code}")
                    ctype = resp.headers.get("content-type", "").lower()
                    if ctype and not ctype.startswith(_HTML_TYPES):
                        return Fetched(url, resp.status_code, error="not_html")
                    data, deadline = bytearray(), time.monotonic() + PAGE_TIMEOUT
                    for chunk in resp.iter_content(1 << 14):
                        data += chunk
                        if len(data) >= MAX_PAGE_BYTES or time.monotonic() > deadline:
                            break
                    return Fetched(url, resp.status_code, decode_html(bytes(data[:MAX_PAGE_BYTES]), resp.encoding))
            except requests.RequestException as exc:
                return Fetched(url, error=type(exc).__name__)
        return Fetched(url, error="redirect_loop")


def decode_html(data: bytes, declared: str | None = None) -> str:
    # requests guesses ISO-8859-1 for any text/html without a charset; most sites are UTF-8.
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
    for enc in filter(None, (meta and meta.group(1).decode("ascii", "ignore"),
                             declared if declared and declared.lower() != "iso-8859-1" else None, "utf-8")):
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("cp1252", errors="replace")


def parse(html: str):
    try:
        return lxml.html.document_fromstring(html)
    except ValueError:  # a str with an XML encoding declaration
        return lxml.html.document_fromstring(html.encode("utf-8"))
    except Exception:  # lxml.etree.ParserError on empty or broken documents
        return None


def pick_links(doc, base_url: str, domain: str, limit: int = MAX_PAGES - 1) -> list[str]:
    """Up to `limit` same-domain links about the school's organisation, best topics first."""
    found: dict[str, int] = {}
    base = urldefrag(base_url)[0]
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if urlsplit(url).scheme not in ("http", "https") or url == base or _SKIP_EXT.search(urlsplit(url).path):
            continue
        if registrable(url) != domain:
            continue
        label = f"{a.text_content()} {urlsplit(url).path}"
        for rank, (_, rx) in enumerate(_TOPIC_RES):
            if rx.search(label):
                found[url] = min(found.get(url, rank), rank)
                break
    # One link per topic first, then fill with the rest.
    by_rank = sorted(found, key=found.get)
    chosen, topics = [], set()
    for url in by_rank:
        if found[url] not in topics:
            chosen.append(url)
            topics.add(found[url])
    chosen += [u for u in by_rank if u not in chosen]
    return chosen[:limit]


def cache_path(url: str):
    host = re.sub(r"[^a-z0-9.-]", "_", (urlsplit(url).hostname or "nohost").lower())
    return WEB_DIR / host / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.json.gz"


def crawl_site(website: str, crawler: Crawler, refresh: bool = False) -> dict:
    """The site's home page and up to MAX_PAGES - 1 linked pages, from the cache when fresh."""
    url = start_url(website)
    path = cache_path(url)
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < WEB_MAX_AGE_DAYS * 86400:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f) | {"cached": True}
    began = time.monotonic()
    domain = registrable(url)
    site = {"url": url, "domain": domain, "pages": [], "error": None}
    if domain is None:
        site["error"] = "bad_url"
    else:
        home = crawler.get(url, domain)
        if home.html is None and url.startswith("http://"):
            # Some servers only answer on https.
            alt = crawler.get("https://" + url[len("http://"):], domain)
            home = alt if alt.html is not None else home
        if home.html is None:
            site["error"] = home.error
        else:
            site["pages"].append({"url": home.url, "html": home.html})
            doc = parse(home.html)
            for link in pick_links(doc, home.url, domain) if doc is not None else []:
                page = crawler.get(link, domain)
                if page.html is not None and all(p["url"] != page.url for p in site["pages"]):
                    site["pages"].append({"url": page.url, "html": page.html})
    site["seconds"] = round(time.monotonic() - began, 2)
    if site["error"] not in ("bad_url",) and not (site["error"] or "").startswith(("Connect", "Read", "Timeout")):
        # Cache answers, including "robots says no" and 404s; not network failures, which are retried.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(site, f)
        tmp.replace(path)
    return site | {"cached": False}


def crawl_all(websites: list[str], workers: int = WORKERS, refresh: bool = False) -> dict[str, dict]:
    """Crawl each distinct website once, hosts in parallel."""
    crawler = Crawler()
    unique = sorted(set(websites))

    def one(w: str) -> tuple[str, dict]:
        try:
            return w, crawl_site(w, crawler, refresh)
        except Exception as exc:  # a broken site must not stop the crawl
            log.warning("crawl of %s failed: %r", w, exc)
            return w, {"url": w, "pages": [], "error": repr(exc)[:200], "seconds": 0, "cached": False}

    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, unique))


# ---------------------------------------------------------------------------
# Extraction

_BLOCK = frozenset("""p div li ul ol br tr td th table h1 h2 h3 h4 h5 h6 header footer section article nav
    aside main address blockquote form dd dt dl hr figure figcaption option""".split())
_DROP = ("script", "style", "noscript", "template", "svg", "iframe", "head")


def page_lines(doc) -> tuple[list[str], list[dict]]:
    """Visible text lines (block elements end a line) and the page's JSON-LD objects."""
    ld = []
    for s in doc.iter("script"):
        if (s.get("type") or "").lower() == "application/ld+json" and s.text:
            try:
                ld.append(json.loads(s.text))
            except ValueError:
                pass
    for el in list(doc.iter(*_DROP)):
        if el.getparent() is not None:
            el.drop_tree()
    for el in doc.iter():
        if isinstance(el.tag, str) and el.tag in _BLOCK:
            el.tail = "\n" + (el.tail or "")
            if el.tag != "br":
                el.text = "\n" + (el.text or "")
    text = doc.text_content().replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return [line for line in lines if line], ld


_W = r"[A-Z0-9][\w'’&.\-]*"
_CONNECT = r"(?:of|for|the|and|&|at|in|on|de|la|del|los|las|y|du)"
_SUFFIX = r"(?:Inc|INC|Incorporated|INCORPORATED|Corp|CORP|Corporation|CORPORATION|LLC|L\.L\.C|Ltd|PBC|NFP)\b\.?"
NAME = rf"{_W}(?:[ \t]+(?:{_CONNECT}[ \t]+)*{_W}){{0,9}}(?:,?[ \t]+{_SUFFIX})?"

_YEAR = r"(?:19|20)\d{2}"
COPYRIGHT = re.compile(
    rf"(?:©|\((?i:c)\)|&copy;|(?i:\bcopyright\b))[ \t]*(?:©|\((?i:c)\))?[ \t]*"
    rf"(?:{_YEAR}(?:[ \t]*[-–—][ \t]*(?:{_YEAR}|\d{{2}}|(?i:present)))?[ \t]*)?[,.:]?[ \t]*(?:(?i:by)[ \t]+)?"
    rf"(?P<name>{NAME})")
C3 = re.compile(
    rf"(?P<name>{NAME})(?:[ \t]+(?i:is|are))?,?[ \t]+(?i:a|an)[ \t]+(?:[\w-]+[ \t]+){{0,6}}?"
    r"501[ \t]*\(?[ \t]*(?i:c)[ \t]*\)?[ \t]*\(?[ \t]*3[ \t]*\)?")
ROLE = re.compile(
    r"(?i:\b(?P<verb>operated|managed|governed|run|overseen|administered|authorized|authorised|chartered|sponsored|"
    r"a program|a project|an initiative|a division|a campus|a member|a school|a part|part)"
    r"[ \t]+(?:by|of|under)[ \t]+(?:the[ \t]+)?)"
    rf"(?P<name>{NAME})")
_ROLE_KIND = {"operated": "operated_by", "run": "operated_by", "administered": "operated_by",
              "a program": "operated_by", "a project": "operated_by", "an initiative": "operated_by",
              "a division": "operated_by", "a campus": "operated_by", "a school": "operated_by",
              "a part": "operated_by", "part": "operated_by",
              "governed": "governed_by", "overseen": "governed_by", "managed": "managed_by",
              "authorized": "authorizer", "authorised": "authorizer", "chartered": "authorizer",
              "sponsored": "authorizer", "a member": "member_of"}
LEGAL = re.compile(NAME)
_BOARD = re.compile(r",?[ \t]+(?:Governing[ \t]+Board|GOVERNING[ \t]+BOARD|Board[ \t]+of[ \t]+(?:Directors|Trustees)|"
                    r"BOARD[ \t]+OF[ \t]+(?:DIRECTORS|TRUSTEES)|Board[ \t]+(?:Meeting|Minutes|Agenda)|BOARD[ \t]+(?:MEETING|MINUTES))")
_LEGAL_ENDS = frozenset("INC INCORPORATED CORPORATION CORP FOUNDATION ASSOCIATION".split())

_EIN_WORDS = re.compile(
    r"\b(?:EIN|E\.I\.N\b\.?|FEIN|TIN|tax[\s-]*(?:ID|I\.D\.|identification|exempt)|federal[\s-]+(?:tax[\s-]+)?(?:ID|I\.D\.|identification)"
    r"|employer[\s-]+identification)", re.I)
_EIN_DASHED = re.compile(r"(?<![\d-])(\d{2})-(\d{7})(?![\d-])")
_EIN_PLAIN = re.compile(r"(?<![\d-])(\d{9})(?![\d-])")
# Prefixes the IRS assigns (IRS "How EINs are assigned").
_EIN_PREFIXES = frozenset(
    [f"{i:02d}" for i in (*range(1, 7), *range(10, 17), *range(20, 28), *range(30, 49), *range(50, 69),
                          *range(71, 78), *range(80, 89), *range(90, 96), 98, 99)])

# Names that are not organisations, cut from the end of a captured name.
_CUT = re.compile(
    r"\s*(?:\||•|·|»|\s[-–—]\s|(?i:all rights|rights reserved|powered by|site by|website by|web design|designed by|"
    r"developed by|privacy|terms of|sitemap|site map|accessibility|non-?discrimination|contact us|follow us|"
    r"\bis\b|\bare\b|\bwas\b|\bwill\b)).*$")
_SENTENCE_END = re.compile(
    r"(?<!\b[A-Za-z])(?<!Inc)(?<!INC)(?<!Corp)(?<!CORP)(?<!Ltd)(?<!Co)(?<!CO)(?<!St)(?<!ST)(?<!Jr)(?<!Mt)\.[ \t]+(?=\S)")
_LEAD = re.compile(r"^(?:(?:Support|Donate|Give|Welcome|About|Contact|Join|Visit|Learn|Our|Your|Copyright|All|"
                   r"The Board of|Board of Directors of|The)\s+)+")
_JUNK_KEYS = frozenset(["ALL RIGHTS RESERVED", "RIGHTS RESERVED", "ALL", "HOME", "MENU", "SEARCH", "SKIP TO MAIN CONTENT"])

# Companies that run or serve charter schools without holding them, and
# website vendors whose names sit in footers. Matched on name keys.
NOT_HOLDERS = re.compile(r"\b(?:" + "|".join("""
    ACADEMICA CHARTER\\ SCHOOLS\\ USA CSUSA K12 STRIDE PEARSON CONNECTIONS\\ ACADEMY CONNECTIONS\\ EDUCATION
    NATIONAL\\ HERITAGE\\ ACADEMIES IMAGINE\\ SCHOOLS MOSAICA EDISON\\ LEARNING LEONA\\ GROUP ACCEL\\ SCHOOLS
    CHARTER\\ SCHOOL\\ ASSOCIATES CSMI EDUCATION\\ MANAGEMENT SCHOOL\\ MANAGEMENT LEARNING\\ MANAGEMENT
    CHOICE\\ ONE\\ SCHOOLS EDTEC ED\\ TEC SOMERSET\\ ACADEMY\\ BOARD BRIDGEPREP\\ ACADEMY\\ BOARD
    FINALSITE EDLIO BLACKBOARD SCHOOLMESSENGER SCHOOLWIRES APPTEGY THRILLSHARE CAMPUS\\ SUITE CAMPUSSUITE
    SCHOOLPOINTE WEEBLY WIX SQUARESPACE WORDPRESS GOOGLE MICROSOFT APPLE FACEBOOK META\\ PLATFORMS YOUTUBE
    POWERSCHOOL INFINITE\\ CAMPUS SKYWARD SCHOOLMINT REVTRAK MYSCHOOLBUCKS PAYPAL STRIPE GIVEBUTTER BLOOMERANG
    DONORBOX NETWORK\\ FOR\\ GOOD BENEVITY CLASSY GIVELIFY AUTOMATTIC GODADDY SCHOOLINSITES SCHOOL\\ WEBMASTERS
    EDUBLOGS GABBART SHARPSCHOOL SCHOOLBLOCKS CATAPULT\\ K12 ROCKET\\ SCIENCE PARENTSQUARE REMIND101 CLASSDOJO
    COGNIA ADVANCED\\ AD WASC MIDDLE\\ STATES\\ ASSOCIATION NEW\\ ENGLAND\\ ASSOCIATION COLLEGE\\ BOARD
    NATIONAL\\ SCHOOL\\ LUNCH UNITED\\ STATES\\ DEPARTMENT DEPARTMENT\\ OF\\ EDUCATION DEPARTMENT\\ OF\\ AGRICULTURE
    USDA STATE\\ BOARD BOARD\\ OF\\ EDUCATION
""".split()) + r")\b")


# Districts, universities, state bodies and charter associations: named on
# school sites as overseers, authorizers or members, never as a nonprofit holder.
PUBLIC_BODIES = re.compile(
    r"\b(?:SCHOOL DISTRICT|UNIFIED|ISD|USD|UNIVERSITY|STATE OF|DEPARTMENT|COUNTY|CITY OF|COMMISSION|"
    r"BOARD OF EDUCATION|PUBLIC SCHOOLS SYSTEM|CHARTER (?:PUBLIC )?SCHOOLS? ASSOCIATION|LEAGUE OF CHARTER|"
    r"NATIONAL ALLIANCE FOR PUBLIC CHARTER|CHARTER SCHOOL INSTITUTE|EDUCATION ASSOCIATION)\b")


@dataclass
class Candidate:
    kind: str  # ein, copyright, c3, operated_by, governed_by, managed_by, authorizer, member_of, legal, ld_json
    name: str | None
    ein: str | None
    snippet: str
    url: str = ""
    keys: list[str] = field(default_factory=list)
    # How many of the site's pages carry it: a footer is on all of them.
    pages: int = 1

    @property
    def is_vendor(self) -> bool:
        return bool(self.name and NOT_HOLDERS.search(name_key(self.name) or ""))

    @property
    def is_public_body(self) -> bool:
        return bool(self.name and PUBLIC_BODIES.search(name_key(self.name) or ""))


def clean_name(raw: str | None) -> str | None:
    """A captured run of capitalised words, trimmed to the organisation's name, or None."""
    if not raw:
        return None
    name = _CUT.sub("", raw).strip(" ,;:-–—")
    # A legal suffix ends the name; so does a sentence's period ("SCHOOL. Powered").
    if m := re.match(rf"^(.*?,?[ \t]+{_SUFFIX})(?=[ \t])", name):
        name = m.group(1)
    name = _SENTENCE_END.split(name, maxsplit=1)[0]
    name = _LEAD.sub("", name)
    name = re.sub(rf"(?:[ \t,]+(?:{_YEAR}|\d{{1,2}}))+$", "", name).strip(" ,;:")
    name = re.sub(r"^(?:\d+[ \t]+)+", "", name)
    # A sentence-ending period, not "Inc." or "Co.".
    if name.endswith(".") and not re.search(r"\b(?:Inc|INC|Corp|CORP|Ltd|Co|CO|St|Jr|L\.L\.C)\.$", name):
        name = name[:-1]
    key = name_key(name)
    if not key or key in _JUNK_KEYS or len(name) < 4 or len(name) > 90 or not re.search(r"[A-Za-z]{3}", name):
        return None
    if len(key.split()) > 12:
        return None
    return name


def _snippet(line: str, start: int, end: int, pad: int = 80) -> str:
    return line[max(0, start - pad): end + pad].strip()


def _ein_ok(digits: str) -> bool:
    return digits[:2] in _EIN_PREFIXES and len(set(digits)) > 2


def eins_in(line: str) -> list[tuple[str, int, int]]:
    """EINs printed next to a word like EIN or Tax ID."""
    out = []
    for m in _EIN_DASHED.finditer(line):
        around = line[max(0, m.start() - 80): m.end() + 40]
        if _EIN_WORDS.search(around) and _ein_ok(m.group(1) + m.group(2)):
            out.append((m.group(1) + m.group(2), m.start(), m.end()))
    for m in _EIN_PLAIN.finditer(line):
        if _EIN_WORDS.search(line[max(0, m.start() - 30): m.start()]) and _ein_ok(m.group(1)):
            out.append((m.group(1), m.start(), m.end()))
    return out


def _ld_candidates(obj, url: str, out: list) -> None:
    if isinstance(obj, list):
        for o in obj:
            _ld_candidates(o, url, out)
    elif isinstance(obj, dict):
        legal, tax = obj.get("legalName"), obj.get("taxID")
        if isinstance(legal, str) and (n := clean_name(legal)):
            out.append(Candidate("ld_json", n, None, f"legalName: {legal}"[:200], url))
        if isinstance(tax, str) and (d := re.sub(r"\D", "", tax)) and len(d) == 9 and _ein_ok(d):
            out.append(Candidate("ein", obj.get("legalName") or obj.get("name"), d, f"taxID: {tax}"[:200], url))
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _ld_candidates(v, url, out)


def extract(html: str, url: str = "") -> list[Candidate]:
    """Every holder candidate on one page."""
    doc = parse(html)
    if doc is None:
        return []
    lines, ld = page_lines(doc)
    out: list[Candidate] = []
    _ld_candidates(ld, url, out)
    for line in lines:
        if len(line) > 2000:
            line = line[:2000]
        for ein, a, b in eins_in(line):
            # The name printed before the EIN on the same line, if any.
            names = [n for m in LEGAL.finditer(line[:a]) if (n := clean_name(m.group()))
                     and not _EIN_WORDS.search(n) and len(name_key(n).split()) >= 2]
            out.append(Candidate("ein", names[-1] if names else None, ein, _snippet(line, a, b), url))
        for m in COPYRIGHT.finditer(line):
            if n := clean_name(m.group("name")):
                out.append(Candidate("copyright", n, None, _snippet(line, m.start(), m.end()), url))
        for m in C3.finditer(line):
            if n := clean_name(m.group("name")):
                out.append(Candidate("c3", n, None, _snippet(line, m.start(), m.end()), url))
        for m in ROLE.finditer(line):
            kind = _ROLE_KIND[" ".join(m.group("verb").lower().split())]
            if n := clean_name(m.group("name")):
                out.append(Candidate(kind, n, None, _snippet(line, m.start(), m.end()), url))
        for m in LEGAL.finditer(line):
            # "NorthStar Academies, Inc. Governing Board Meetings": the board's organisation.
            b = _BOARD.search(m.group())
            if b or _BOARD.match(line, m.end()):
                if (n := clean_name(m.group()[:b.start()] if b else m.group())) and len((name_key(n) or "").split()) >= 2:
                    out.append(Candidate("board_of", n, None, _snippet(line, m.start(), m.end()), url))
            words = name_key(m.group()) or ""
            last = re.findall(r"[A-Za-z]+", m.group())[-1:] or [""]
            if last[0].upper() in _LEGAL_ENDS and (n := clean_name(m.group())) and len(words.split()) >= 2:
                out.append(Candidate("legal", n, None, _snippet(line, m.start(), m.end()), url))
    for c in out:
        c.keys = [k for p in holder_names(c.name) if (k := name_key(p))] if c.name else []
    return out


def site_candidates(site: dict) -> list[Candidate]:
    """Candidates from every page of a crawled site, one per (kind, name, EIN)."""
    seen: dict[tuple, Candidate] = {}
    for page in site.get("pages", []):
        on_page = set()
        for c in extract(page["html"], page["url"]):
            key = (c.kind, name_key(c.name), c.ein)
            if key in seen and key not in on_page:
                seen[key].pages += 1
            seen.setdefault(key, c)
            on_page.add(key)
    return list(seen.values())


_GENERIC = frozenset(GENERIC_WORDS)
_STATE_WORDS = frozenset(w for name in US_STATES.values() for w in name.split())


def name_core(key: str | None) -> str:
    """The SQL name_core: a name key without the generic school words (all of it if nothing is left)."""
    words = (key or "").split()
    return " ".join(w for w in words if w not in _GENERIC) or " ".join(words)


def distinctive(key: str | None) -> list[str]:
    """The words of a name key that can tell one organisation from another."""
    return [w for w in (key or "").split() if w not in _GENERIC and w not in _STATE_WORDS]


# Network entities that hold money or buildings rather than run schools (match.SUPPORT_NAME).
SUPPORT_NAME = re.compile(
    r"\b(?:FOUNDATION|FACILITY|FACILITIES|HOLDING|HOLDINGS|LENDER|REAL ESTATE|PROPERTY|PROPERTIES|BUILDING)\b")


# Kinds that can name the holder; the others are recorded only for review.
HOLDER_KINDS = ("ein", "ld_json", "c3", "governed_by", "board_of", "operated_by", "copyright", "legal")


def holder_candidates(cands: list[Candidate]) -> list[Candidate]:
    """The candidates that may name the school's holder: not a management
    company, vendor, authorizer or booster club."""
    out = []
    for c in cands:
        if c.kind not in HOLDER_KINDS or c.is_vendor or c.is_public_body or (c.name and is_support_org(c.name)):
            continue
        # "Excellence" and "Minnesota's" are phrases, not names. (resolve_candidates
        # then asks a one-word name to be unique among the state's filers.)
        if not c.ein and not any(len(distinctive(k)) >= 2 or (distinctive(k) and not k.endswith(" S")
                                                               and len(k.split()) >= 2) for k in c.keys):
            continue
        # A bare legal-looking name counts only in a site-wide header or footer:
        # on one page it is mostly a board member's employer or a donor.
        if c.kind == "legal" and not c.ein and c.pages < 2:
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Checking candidates against the filers

FILER_MATCH = """
    WITH c AS (
        SELECT i, st, k, name_core(k, %(generic)s) AS core, name_nums(k) AS nums
        FROM unnest(%(ids)s::int[], %(sts)s::text[], %(keys)s::text[]) AS c(i, st, k)
    ), lf AS (
        SELECT ein, org_name, state, name_key, dba_key,
               name_core(name_key, %(generic)s) AS core, name_core(dba_key, %(generic)s) AS dba_core,
               name_nums(name_key) AS nums
        FROM latest_filings WHERE state = ANY(%(sts)s::text[])
    )
    SELECT c.i, f.ein, f.org_name,
           round(greatest(similarity(c.core, f.core), coalesce(similarity(c.core, f.dba_core), 0))::numeric, 2) AS sim
    FROM c JOIN lf f ON f.state = c.st
    WHERE c.core <> '' AND upper(c.core) NOT IN (SELECT unnest(%(states)s::text[]))
      AND (c.k = f.name_key OR c.k = f.dba_key
           OR greatest(similarity(c.core, f.core), coalesce(similarity(c.core, f.dba_core), 0)) >= %(sim)s)
      AND (c.nums = '' OR f.nums = '' OR c.nums = f.nums)
      AND f.org_name !~* '\\mUNIVERSITY OF\\M'
    ORDER BY c.i, sim DESC
"""
HOLDER_SIMILARITY = 0.8


def match_filers(conn, items: list[tuple[int, str, str]]) -> dict[int, list[tuple[str, str, float]]]:
    """In-state 990 filers named like each (id, state, name key): same key, or name
    cores at HOLDER_SIMILARITY, the holder signal's test."""
    if not items:
        return {}
    ids, sts, keys = zip(*items)
    out: dict[int, list] = {}
    for i, ein, org, sim in conn.execute(FILER_MATCH, {
        "generic": list(GENERIC_WORDS), "ids": list(ids), "sts": list(sts), "keys": list(keys),
        "states": list(US_STATES.values()), "sim": HOLDER_SIMILARITY,
    }).fetchall():
        out.setdefault(i, []).append((ein, org, float(sim)))
    return out


def resolve_candidates(conn, cands: list[tuple[str, Candidate]]) -> list[tuple[str, Candidate, dict]]:
    """(state, candidate) pairs checked against the in-state filers. Returns
    (state, candidate, note) for those kept.

    A name without an EIN needs two distinctive words, or one that no more than
    one in-state filer has: "NORTHSTAR ACADEMIES" is NorthStar's in Florida,
    while "MASTERY" or "AMERICAN" starts several filers' names.

    A donate page often prints the network's fundraising foundation ("Mastery
    Charter Schools Foundation, tax ID 20-5593485"). When a candidate's EIN, or
    its name, resolves to an in-state filer named like a foundation, it becomes
    the one in-state filer with the same name core that isn't a foundation, with
    that filer's EIN; with none, or several, it is dropped.
    """
    states = sorted({st for st, _ in cands})
    if not states:
        return []
    filers = conn.execute(
        "SELECT ein, org_name, state, name_key, coalesce(total_revenue, 0) FROM latest_filings WHERE state = ANY(%s)",
        (states,)).fetchall()
    by_ein = {f[0]: f for f in filers}
    by_core: dict[tuple[str, str], list] = {}
    word_filers: dict[tuple[str, str], int] = {}
    for f in filers:
        by_core.setdefault((f[2], name_core(f[3])), []).append(f)
        for word in set(distinctive(f[3])):
            word_filers[(f[2], word)] = word_filers.get((f[2], word), 0) + 1

    def is_foundation(name: str | None) -> bool:
        return bool(SUPPORT_NAME.search(name_key(name) or ""))

    def named_enough(st: str, key: str) -> bool:
        words = distinctive(key)
        return len(words) >= 2 or (len(words) == 1 and word_filers.get((st, words[0]), 0) <= 1)

    out = []
    for st, c in cands:
        if not c.ein and not any(named_enough(st, k) for k in c.keys):
            continue
        found = None
        if c.ein and (f := by_ein.get(c.ein)) and f[2] == st and is_foundation(f[1]):
            found = f
        elif not c.ein and is_foundation(c.name):
            found = next((f for k in c.keys for f in by_core.get((st, name_core(k)), [])
                          if is_foundation(f[1])), None)
        if found is None:
            out.append((st, c, {}))
            continue
        others = [f for f in by_core.get((st, name_core(found[3])), []) if not is_foundation(f[1])]
        if len(others) != 1:
            continue
        ein, org = others[0][0], others[0][1]
        swapped = Candidate(c.kind, org, ein, c.snippet, c.url, [k for k in [name_key(org)] if k], c.pages)
        out.append((st, swapped, {"instead_of": found[1], "instead_of_ein": found[0]}))
    return out


# ---------------------------------------------------------------------------
# external_links rows

def website_rows(conn, refresh: bool = False) -> list[tuple]:
    """external_links rows (see external.COLUMNS) from the eligible schools' sites.

    One row per school and distinct holder candidate: its name and EIN when the
    site printed one. The holder signal compares the names with in-state filers;
    bmf_candidates brings an EIN, or an exact in-state name, into the pool.
    Management companies, vendors, authorizers, districts and booster clubs
    are left out (holder_candidates). Sites already crawled come from the cache.
    """
    schools = eligible_schools(conn)
    sites = crawl_all([w for *_, w, _ in schools], refresh=refresh)
    rows = []
    for ncessch, st, sch_name, website, _ in schools:
        site = sites.get(website) or {}
        seen = set()
        cands = [(st, c) for c in holder_candidates(site_candidates(site))]
        for _, c, note in resolve_candidates(conn, cands):
            ident = (c.ein, tuple(c.keys))
            if ident in seen:
                continue
            seen.add(ident)
            name = c.name or f"EIN {c.ein}"
            rows.append((ncessch, "website", c.ein, name, c.keys, None, json.dumps({
                "kind": c.kind, "url": c.url, "pages": c.pages, "snippet": c.snippet[:300]} | note)))
    return rows
