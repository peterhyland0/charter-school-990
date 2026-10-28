"""Fetch the latest return for each candidate nonprofit, one file at a time,
straight out of the IRS zips with HTTP range requests."""

import struct
import time
import zipfile
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import inflate64
import requests
from dagster import MaterializeResult, asset
from remotezip import RemoteZip

from ..resources import PostgresResource
from ..settings import HTTP_HEADERS, RAW_DIR
from .zips import STAGE as ZIPS_STAGE
from .zips import zip_members

XML_DIR = RAW_DIR / "xml"
STAGE = "fetch"
# Without a timeout a stalled range request hangs the run forever.
TIMEOUT = (15, 60)
# Errors that retrying can't fix, such as an unsupported compression method.
PERMANENT_ERRORS = (NotImplementedError, zipfile.BadZipFile, KeyError)
# Big zips are split so one worker isn't left reading thousands of returns alone.
CHUNK_SIZE = 500
# Extra bytes requested past a member in case its local header is longer.
LOCAL_EXTRA_SLACK = 1024
# Kept modest: the IRS host blocks clients that send too many requests at once.
MAX_WORKERS = 4

# Every return for each EIN's newest tax period, across all index years, so an
# amended return and the original it replaces are both fetched and the parser
# can pick between them. zip_name is NULL when no zip holds the return.
SELECT_LATEST = """
    WITH latest AS (
        SELECT ein, max(tax_period) AS tax_period FROM irs_index GROUP BY ein
    )
    SELECT i.object_id, z.zip_name, z.zip_url
    FROM irs_index i
    JOIN latest l ON l.ein = i.ein AND l.tax_period = i.tax_period
    LEFT JOIN zip_members z ON z.object_id = i.object_id
"""


def xml_path(object_id: str):
    return XML_DIR / f"{object_id}.xml"


def _range(session: requests.Session, url: str, start: int, length: int) -> bytes:
    resp = session.get(url, headers={**HTTP_HEADERS, "Range": f"bytes={start}-{start + length - 1}"},
                       timeout=TIMEOUT, stream=True)
    with resp:
        resp.raise_for_status()
        # A server that ignores Range would send the whole zip; refuse instead.
        if resp.status_code != 206:
            raise zipfile.BadZipFile(f"range request not honoured (HTTP {resp.status_code})")
        return resp.content


def read_member(session: requests.Session, url: str, info: zipfile.ZipInfo) -> bytes:
    """Read one member with a single range request, including Deflate64 ones.

    The IRS builds its large May zips with Deflate64 (method 9), which Python's
    zipfile can't decompress. So read the member's raw bytes and decompress here.
    """
    # The local header's extra field can differ from the central directory's,
    # so ask for some slack and fetch the rest only if that wasn't enough.
    head = 30 + len(info.filename.encode()) + len(info.extra)
    blob = _range(session, url, info.header_offset, head + info.compress_size + LOCAL_EXTRA_SLACK)
    if blob[:4] != b"PK\x03\x04":
        raise zipfile.BadZipFile(f"bad local header for {info.filename}")
    name_len, extra_len = struct.unpack("<HH", blob[26:30])
    data_start = 30 + name_len + extra_len
    if len(blob) < data_start + info.compress_size:
        blob += _range(session, url, info.header_offset + len(blob), data_start + info.compress_size - len(blob))
    raw = blob[data_start:data_start + info.compress_size]
    if info.compress_type == zipfile.ZIP_DEFLATED:
        data = zlib.decompress(raw, -15)
    elif info.compress_type == 9:  # Deflate64
        data = inflate64.Inflater().inflate(raw)
    elif info.compress_type == zipfile.ZIP_STORED:
        data = raw
    else:
        raise NotImplementedError(f"compression method {info.compress_type}")
    if zlib.crc32(data) != info.CRC:
        raise zipfile.BadZipFile(f"CRC mismatch for {info.filename}")
    return data


def _directory(zip_url: str, object_ids: list[str]) -> tuple[dict[str, zipfile.ZipInfo], list[tuple[str, str]]]:
    """Read a zip's table of contents once and pick out the wanted members.

    Never raises: failures are returned."""
    try:
        with RemoteZip(zip_url, headers=HTTP_HEADERS, timeout=TIMEOUT) as zf:
            infos = {n.split("_", 1)[0]: zf.getinfo(n) for n in zf.namelist() if n.endswith("_public.xml")}
    except Exception as exc:
        return {}, [(object_id, f"could not open {zip_url}: {exc!r}") for object_id in object_ids]
    found = {o: infos[o] for o in object_ids if o in infos}
    missing = [(o, f"not in {zip_url}") for o in object_ids if o not in infos]
    return found, missing


def _fetch_chunk(zip_url: str, infos: list[tuple[str, zipfile.ZipInfo]]) -> tuple[int, list[tuple[str, str]]]:
    """Fetch the given returns from one zip. Never raises: failures are returned."""
    fetched, errors = 0, []
    with requests.Session() as session:
        for object_id, info in infos:
            for attempt in range(3):
                try:
                    data = read_member(session, zip_url, info)
                    tmp = xml_path(object_id).with_suffix(".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(xml_path(object_id))
                    fetched += 1
                    break
                except PERMANENT_ERRORS as exc:
                    errors.append((object_id, repr(exc)))
                    break
                except Exception as exc:  # network errors, truncated reads
                    if attempt == 2:
                        errors.append((object_id, repr(exc)))
                    else:
                        time.sleep(2 ** attempt)
    return fetched, errors


@asset(group_name="pipeline", deps=[zip_members], description="Cache the latest return per candidate EIN as XML on disk.")
def raw_returns(pg: PostgresResource) -> MaterializeResult:
    XML_DIR.mkdir(parents=True, exist_ok=True)
    with pg.connect() as conn:
        selected = conn.execute(SELECT_LATEST).fetchall()
        unreachable = [z for (z,) in conn.execute(
            "SELECT key FROM load_errors WHERE stage = %s ORDER BY key", (ZIPS_STAGE,)
        ).fetchall()]
    # A return is only truly missing if every zip it could be in was readable.
    unlocated = (
        f"not located: these zips were unreachable this run: {', '.join(unreachable)}"
        if unreachable else "listed in the IRS index but not found in any zip"
    )

    todo, errors = defaultdict(list), []
    for object_id, _, zip_url in selected:
        if zip_url is None:
            errors.append((object_id, unlocated))
        elif not xml_path(object_id).exists():
            todo[zip_url].append(object_id)

    fetched = 0
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, len(todo)))) as pool:
        # One table-of-contents read per zip, then the members in parallel chunks.
        directories = list(pool.map(lambda item: _directory(*item), todo.items()))
        chunks = []
        for zip_url, (infos, errs) in zip(todo, directories):
            errors.extend(errs)
            items = sorted(infos.items(), key=lambda item: item[1].header_offset)
            chunks += [(zip_url, items[i:i + CHUNK_SIZE]) for i in range(0, len(items), CHUNK_SIZE)]
        for n, errs in pool.map(lambda chunk: _fetch_chunk(*chunk), chunks):
            fetched += n
            errors.extend(errs)

    with pg.connect() as conn:
        conn.execute("DELETE FROM load_errors WHERE stage = %s", (STAGE,))
        for object_id, error in errors:
            pg.log_error(conn, STAGE, object_id, error)

    return MaterializeResult(metadata={
        "selected": len(selected),
        "already_cached": len(selected) - sum(len(v) for v in todo.values()) - sum(1 for r in selected if r[2] is None),
        "fetched": fetched,
        "failed": len(errors),
        "failure_rate": round(len(errors) / len(selected), 4) if selected else 0.0,
    })
