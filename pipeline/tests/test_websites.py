import lxml.html

from charter990.assets import websites as w
from charter990.assets.websites import (
    Candidate, Fetched, clean_name, eins_in, extract, holder_candidates, pick_links, site_candidates,
)

PAGE = """<html><head>
<script type="application/ld+json">{"@type": "School", "legalName": "Lamad Education Corp.", "taxID": "27-1234567"}</script>
<script>document.write("Copyright 2025 Evil Script Inc.")</script><style>.x{}</style></head><body>
<nav><a href="/about-us">About</a><a href="https://other.org/privacy">Privacy</a></nav>
<p>Lamad Academy Charter School is a 501(c)(3) nonprofit organization. Our Tax ID is 13-3456789.</p>
<p>A public charter school governed by the Brooklyn Lamad Education Corp.</p>
<p>Managed by Academica Dade, LLC</p>
<p>Authorized by the State University of New York</p>
<footer>&copy; 2019&ndash;2025 Somerset Academy, Inc. | All Rights Reserved | Website by Edlio</footer>
<footer>Copyright 2025 LAMAD ACADEMY CHARTER SCHOOL. Powered by Finalsite</footer>
</body></html>"""


def kinds(cands):
    return {(c.kind, c.name, c.ein) for c in cands}


def test_extract_finds_each_kind_and_ignores_scripts():
    got = kinds(extract(PAGE, "https://lamad.org/"))
    assert ("ld_json", "Lamad Education Corp.", None) in got
    assert ("ein", "Lamad Education Corp.", "271234567") in got
    assert ("c3", "Lamad Academy Charter School", None) in got
    assert ("ein", "Lamad Academy Charter School", "133456789") in got
    assert ("governed_by", "Brooklyn Lamad Education Corp.", None) in got
    assert ("managed_by", "Academica Dade, LLC", None) in got
    assert ("authorizer", "State University of New York", None) in got
    assert ("copyright", "Somerset Academy, Inc.", None) in got
    assert ("copyright", "LAMAD ACADEMY CHARTER SCHOOL", None) in got
    assert not any("Evil" in (n or "") for _, n, _ in got)


def test_board_pages_name_the_governing_organisation():
    got = kinds(extract("<p>NorthStar Academies, Inc. Governing Board Meetings</p>"))
    assert ("board_of", "NorthStar Academies, Inc.", None) in got


def test_clean_name_trims_footer_noise():
    assert clean_name("LAMAD ACADEMY CHARTER SCHOOL. Powered") == "LAMAD ACADEMY CHARTER SCHOOL"
    assert clean_name("Somerset Academy Inc. Website") == "Somerset Academy Inc."
    assert clean_name("St. Paul City School") == "St. Paul City School"
    assert clean_name("Welcome Foo Bar Academy 2025") == "Foo Bar Academy"
    assert clean_name("All Rights Reserved") is None
    assert clean_name("") is None


def test_eins_need_a_label_and_a_real_prefix():
    assert [e for e, *_ in eins_in("Federal Tax ID: 12-3456789")] == ["123456789"]
    assert [e for e, *_ in eins_in("EIN 271234567")] == ["271234567"]
    assert eins_in("Call 12-3456789 today") == []           # no label
    assert eins_in("EIN 07-1234567") == []                  # 07 is never assigned
    assert eins_in("Order 123456789 for the EIN") == []     # undashed must follow the label


def test_pick_links_stays_on_the_domain_and_prefers_donate_and_privacy():
    doc = lxml.html.fromstring("""<div>
        <a href="/contact">Contact</a><a href="/about">About Us</a><a href="/give">Donate</a>
        <a href="https://evil.com/privacy">Privacy</a><a href="https://www.lamad.org/privacy-policy">Privacy</a>
        <a href="/board.pdf">Board</a><a href="mailto:x@lamad.org">Email</a><a href="/calendar">Calendar</a></div>""")
    links = pick_links(doc, "https://lamad.org/", "lamad.org")
    assert links == ["https://lamad.org/give", "https://www.lamad.org/privacy-policy",
                     "https://lamad.org/about", "https://lamad.org/contact"]


def test_holder_candidates_drop_vendors_public_bodies_phrases_and_one_page_names():
    def c(kind, name, ein=None, pages=1):
        return Candidate(kind, name, ein, "", keys=[k for k in [w.name_key(name)] if k] if name else [], pages=pages)

    keep = [c("operated_by", "Alliance College-Ready Public Schools"), c("ein", None, "123456789"),
            c("legal", "Brooklyn Lamad Foundation", pages=3)]
    drop = [c("copyright", "SchoolMessenger Corporation"), c("governed_by", "Los Angeles Unified School District"),
            c("operated_by", "Excellence"), c("operated_by", "Minnesota's"), c("legal", "Saint Francis Foundation"),
            c("managed_by", "Leona Arizona Management, LLC"), c("c3", "Lamad Parent Teacher Organization"),
            c("authorizer", "State Charter Schools Commission")]
    assert holder_candidates(keep + drop) == keep


def test_site_candidates_count_pages_and_crawl_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "WEB_DIR", tmp_path)
    footer = "<footer>&copy; 2025 Lamad Education Corp.</footer>"

    class StubCrawler:
        calls = []

        def get(self, url, domain):
            self.calls.append(url)
            if url == "http://lamad.org":
                return Fetched(url, 200, f"<a href='/about'>About</a><a href='https://x.com/give'>Give</a>{footer}")
            return Fetched(url, 200, f"<p>About us</p>{footer}")

    crawler = StubCrawler()
    site = w.crawl_site("lamad.org", crawler)
    assert [p["url"] for p in site["pages"]] == ["http://lamad.org", "http://lamad.org/about"]
    assert crawler.calls == ["http://lamad.org", "http://lamad.org/about"]
    [cand] = [c for c in site_candidates(site) if c.kind == "copyright"]
    assert cand.pages == 2 and cand.name == "Lamad Education Corp."
    again = w.crawl_site("lamad.org", crawler)
    assert again["cached"] and len(crawler.calls) == 2


def test_crawler_refuses_off_domain_redirects_and_robots(monkeypatch):
    crawler = w.Crawler(w.Throttle(0))

    class Resp:
        def __init__(self, status, headers=None, text=""):
            self.status_code, self.headers, self.text = status, headers or {}, text
            self.is_redirect = status in (301, 302)
            self.encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, n):
            yield b"<p>hi</p>"

    def fake_get(url, **kw):
        if url.endswith("/robots.txt"):
            return Resp(200, text="User-agent: *\nDisallow: /private\n")
        if url == "http://a.org/":
            return Resp(301, {"location": "https://www.a.org/home"})
        if url == "https://www.a.org/home":
            return Resp(200, {"content-type": "text/html; charset=utf-8"})
        return Resp(302, {"location": "https://b.com/"})

    monkeypatch.setattr(crawler.session, "get", fake_get)
    assert crawler.get("http://a.org/", "a.org").html == "<p>hi</p>"
    assert crawler.get("http://a.org/elsewhere", "a.org").error == "off_domain"
    assert crawler.get("http://a.org/private/x", "a.org").error == "robots"


def test_resolve_candidates_swaps_foundations_and_needs_a_distinctive_name():
    filers = [("205593485", "MASTERY CHARTER SCHOOLS FOUNDATION", "PA", "MASTERY CHARTER SCHOOLS FOUNDATION", 5),
              ("233060542", "MASTERY CHARTER HIGH SCHOOL", "PA", "MASTERY CHARTER HIGH SCHOOL", 300),
              ("824221501", "MASTERY PREP ELEMENTARY CHARTER", "PA", "MASTERY PREP ELEMENTARY CHARTER", 20),
              ("300000001", "LEHIGH ARTS FOUNDATION", "PA", "LEHIGH ARTS FOUNDATION", 1),
              ("300000002", "LEHIGH ARTS CHARTER SCHOOL", "PA", "LEHIGH ARTS CHARTER SCHOOL", 9),
              ("111111111", "LONE ARTS FOUNDATION", "PA", "LONE ARTS FOUNDATION", 1),
              ("222222222", "PERSPECTIVES CHARTER SCHOOLS", "PA", "PERSPECTIVES CHARTER SCHOOLS", 1)]

    class Conn:
        def execute(self, sql, params):
            class R:
                def fetchall(_):
                    return filers
            return R()

    def c(name, ein=None):
        return Candidate("ein" if ein else "copyright", name, ein, "", keys=[w.name_key(name)] if name else [])

    got = w.resolve_candidates(Conn(), [
        ("PA", c("Lehigh Arts Foundation", "300000001")),               # one sibling: swapped, with its EIN
        ("PA", c("Mastery Charter Schools Foundation", "205593485")),  # two siblings: dropped
        ("PA", c("Lone Arts Foundation")),                             # no sibling: dropped
        ("PA", c("Mastery Charter Schools")),                          # MASTERY starts three filers: dropped
        ("PA", c("Perspectives Charter Schools")),                     # one filer has the word: kept
        ("PA", c("Corolla Education Foundation")),                     # no filer has the word: kept
        ("PA", c("Lamad Brooklyn Corp"))])
    assert [(x.name, x.ein, note.get("instead_of_ein")) for _, x, note in got] == [
        ("LEHIGH ARTS CHARTER SCHOOL", "300000002", "300000001"),
        ("Perspectives Charter Schools", None, None),
        ("Corolla Education Foundation", None, None),
        ("Lamad Brooklyn Corp", None, None)]
