"""Which zip file physically holds each return.

The index's XML_BATCH_ID is a label, not a locator: May's label covers both
05A.zip and 05B.zip. So read each relevant zip's table of contents and record
where every wanted return actually sits.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor

from dagster import MaterializeResult, asset
from remotezip import RemoteZip

from ..resources import PostgresResource
from ..settings import HTTP_HEADERS, IRS_XML_BASE, RAW_DIR
from ..util import SourceUnavailable, head_with_retry
from .irs_index import irs_index

SUFFIXES = "ABCD"
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
    temporary outage doesn't stop the run; a zip never seen before is skipped.
    """
    try:
        resp = head_with_retry(_zip_url(name))
    except SourceUnavailable:
        return name, _cached_listing(name)
    if resp.status_code == 404:
        return name, None
    return name, resp.headers.get("ETag", "").strip('"') or "no-etag"


def _members(name: str, etag: str) -> list[str]:
    """Object ids in a zip, from a cached copy of its table of contents."""
    cache = RAW_DIR / "zipdirs" / f"{name}.{re.sub(r'[^A-Za-z0-9]', '', etag)}.json"
    if not cache.exists() and (RAW_DIR / "zipdirs" / f"{name}.{etag}.json").exists():
        cache = RAW_DIR / "zipdirs" / f"{name}.{etag}.json"  # an already-sanitised cached ETag
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
        zips = {name: etag for name, etag in pool.map(_probe, candidates) if etag is not None}
        listings = dict(zip(zips, pool.map(lambda item: _members(*item), zips.items())))

    rows = [
        (object_id, name, _zip_url(name), zips[name])
        for name, ids in listings.items()
        for object_id in ids
        if object_id in wanted
    ]
    with pg.connect() as conn:
        pg.replace_table(conn, "zip_members", ("object_id", "zip_name", "zip_url", "zip_etag"), rows)

    located = {r[0] for r in rows}
    return MaterializeResult(metadata={
        "zips_found": ", ".join(sorted(zips)),
        "unrecognised_labels": ", ".join(unrecognised) or "none",
        "wanted_returns": len(wanted),
        "located": len(located),
        "not_located": len(wanted - located),
    })
