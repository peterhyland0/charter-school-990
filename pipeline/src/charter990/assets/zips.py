"""Which zip file physically holds each return.

The index's XML_BATCH_ID is a label, not a locator: May's label covers both
05A.zip and 05B.zip. So read each relevant zip's table of contents and record
where every wanted return actually sits.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from dagster import MaterializeResult, asset
from remotezip import RemoteZip

from ..resources import PostgresResource
from ..settings import HTTP_HEADERS, IRS_XML_BASE, RAW_DIR
from ..util import SourceUnavailable, head_with_retry
from .irs_index import irs_index

SUFFIXES = "ABCD"
STAGE = "zips"
# Probe result for a zip that may exist but couldn't be checked this run.
UNREACHABLE = "unreachable"
LABEL = re.compile(r"^(\d{4})_TEOS_XML_(\d{2})[A-Z]$")


def _zip_url(name: str) -> str:
    return f"{IRS_XML_BASE}/{name[:4]}/{name}"


def _cached_listing(name: str) -> str | None:
    """The ETag of the newest cached table of contents for this zip, if any."""
    cached = sorted((RAW_DIR / "zipdirs").glob(f"{name}.*.json"), key=lambda p: p.stat().st_mtime)
    return cached[-1].name[len(name) + 1:-len(".json")] if cached else None


def _probe(name: str) -> tuple[str, str | None]:
    """Return (name, ETag) if the zip exists, else (name, None).

    When the IRS server refuses or fails, fall back to the cached listing so a
    temporary outage doesn't stop the run. A zip with no cached listing is
    reported as UNREACHABLE rather than treated as absent.
    """
    try:
        resp = head_with_retry(_zip_url(name))
    except (SourceUnavailable, requests.RequestException):
        return name, _cached_listing(name) or UNREACHABLE
    if resp.status_code == 404:
        return name, None
    # Kept to safe filename characters: the ETag names the cached listing file.
    return name, re.sub(r"[^A-Za-z0-9]", "", resp.headers.get("ETag", "")) or "noetag"


def _members(name: str, etag: str) -> list[str]:
    """Object ids in a zip, from a cached copy of its table of contents."""
    cache = RAW_DIR / "zipdirs" / f"{name}.{etag}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    with RemoteZip(_zip_url(name), headers=HTTP_HEADERS, timeout=(15, 120)) as zf:
        ids = [n.split("_", 1)[0] for n in zf.namelist() if n.endswith("_public.xml")]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(ids))
    return ids


@asset(group_name="pipeline", deps=[irs_index], description="Map each wanted return to the zip that holds it.")
def zip_members(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        wanted = {o for (o,) in conn.execute("SELECT object_id FROM irs_index").fetchall()}
        labels = [l for (l,) in conn.execute("SELECT DISTINCT xml_batch_id FROM irs_index").fetchall()]

    # Each label like 2026_TEOS_XML_05A stands for a year and month; probe all its siblings.
    months = {(m.group(1), m.group(2)) for l in labels if (m := LABEL.match(l))}
    unrecognised = sorted(l for l in labels if not LABEL.match(l))
    candidates = [f"{y}_TEOS_XML_{mm}{s}.zip" for y, mm in sorted(months) for s in SUFFIXES]
    # Few parallel requests: the IRS host rate-limits bursts.
    with ThreadPoolExecutor(max_workers=3) as pool:
        probed = dict(pool.map(_probe, candidates))
        unreachable = sorted(n for n, etag in probed.items() if etag == UNREACHABLE)
        zips = {n: etag for n, etag in probed.items() if etag not in (None, UNREACHABLE)}
        listings = dict(zip(zips, pool.map(lambda item: _members(*item), zips.items())))

    rows = [
        (object_id, name, _zip_url(name), zips[name])
        for name, ids in listings.items()
        for object_id in ids
        if object_id in wanted
    ]
    with pg.connect() as conn:
        pg.replace_table(conn, "zip_members", ("object_id", "zip_name", "zip_url", "zip_etag"), rows)
        # raw_returns reads these to explain returns it couldn't locate.
        conn.execute("DELETE FROM load_errors WHERE stage = %s", (STAGE,))
        for name in unreachable:
            pg.log_error(conn, STAGE, name, "IRS server unreachable and no cached listing")

    located = {r[0] for r in rows}
    return MaterializeResult(metadata={
        "zips_found": ", ".join(sorted(zips)),
        "zips_unreachable": ", ".join(unreachable) or "none",
        "unrecognised_labels": ", ".join(unrecognised) or "none",
        "wanted_returns": len(wanted),
        "located": len(located),
        "not_located": len(wanted - located),
    })
