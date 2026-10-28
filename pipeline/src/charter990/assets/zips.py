"""Which zip file physically holds each return.

The index's XML_BATCH_ID is a label, not a locator: May's label covers both
05A.zip and 05B.zip. So read each relevant zip's table of contents and record
where every wanted return actually sits.
"""

import json
import re

import requests
from dagster import MaterializeResult, asset
from remotezip import RemoteZip

from ..resources import PostgresResource
from ..settings import HTTP_HEADERS, INDEX_YEAR, IRS_XML_BASE, RAW_DIR
from .irs_index import irs_index

SUFFIXES = "ABCD"


def _zip_url(name: str) -> str:
    return f"{IRS_XML_BASE}/{INDEX_YEAR}/{name}"


def _probe(name: str) -> str | None:
    """Return the zip's ETag if it exists, else None."""
    resp = requests.head(_zip_url(name), headers=HTTP_HEADERS, timeout=30, allow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.headers.get("ETag", "").strip('"') or "no-etag"


def _members(name: str, etag: str) -> list[str]:
    """Object ids in a zip, from a cached copy of its table of contents."""
    cache = RAW_DIR / "zipdirs" / f"{name}.{re.sub(r'[^A-Za-z0-9]', '', etag)}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    with RemoteZip(_zip_url(name), headers=HTTP_HEADERS, timeout=(15, 120)) as zf:
        ids = [n.split("_", 1)[0] for n in zf.namelist() if n.endswith("_public.xml")]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(ids))
    return ids


@asset(group_name="phase0", deps=[irs_index], description="Map each wanted return to the zip that holds it.")
def zip_members(pg: PostgresResource) -> MaterializeResult:
    with pg.connect() as conn:
        wanted = {o for (o,) in conn.execute("SELECT object_id FROM irs_index").fetchall()}
        labels = [l for (l,) in conn.execute("SELECT DISTINCT xml_batch_id FROM irs_index").fetchall()]

    # Each label like 2026_TEOS_XML_05A stands for a month; probe all its siblings.
    months = sorted({re.search(r"_(\d{2})[A-Z]$", l).group(1) for l in labels})
    zips = {}
    for month in months:
        for suffix in SUFFIXES:
            name = f"{INDEX_YEAR}_TEOS_XML_{month}{suffix}.zip"
            if (etag := _probe(name)) is not None:
                zips[name] = etag

    rows = []
    for name, etag in zips.items():
        for object_id in _members(name, etag):
            if object_id in wanted:
                rows.append((object_id, name, _zip_url(name), etag))

    with pg.connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE zip_members")
        with cur.copy("COPY zip_members (object_id, zip_name, zip_url, zip_etag) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)

    located = {r[0] for r in rows}
    return MaterializeResult(metadata={
        "zips_found": ", ".join(sorted(zips)),
        "wanted_returns": len(wanted),
        "located": len(located),
        "not_located": len(wanted - located),
    })
