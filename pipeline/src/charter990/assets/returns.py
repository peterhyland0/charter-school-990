"""Fetch the latest return for each candidate nonprofit, one file at a time,
straight out of the IRS zips with HTTP range requests."""

import struct
import time
import zipfile
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import inflate64
from dagster import MaterializeResult, asset
from remotezip import RemoteZip

from ..resources import PostgresResource
from ..settings import HTTP_HEADERS, RAW_DIR
from .zips import zip_members

XML_DIR = RAW_DIR / "xml"
STAGE = "fetch"
# Without a timeout a stalled range request hangs the run forever.
TIMEOUT = (15, 60)
# Errors that retrying can't fix, such as an unsupported compression method.
PERMANENT_ERRORS = (NotImplementedError, zipfile.BadZipFile, KeyError)

# Every return for each EIN's newest tax period, so an amended return and the
# original it replaces are both fetched and the parser can pick between them.
SELECT_LATEST = """
    WITH latest AS (
        SELECT ein, max(tax_period) AS tax_period FROM irs_index GROUP BY ein
    )
    SELECT i.object_id, z.zip_name, z.zip_url
    FROM irs_index i
    JOIN latest l ON l.ein = i.ein AND l.tax_period = i.tax_period
    JOIN zip_members z ON z.object_id = i.object_id
"""


def xml_path(object_id: str):
    return XML_DIR / f"{object_id}.xml"


def read_member(zf: RemoteZip, name: str) -> bytes:
    """Read one member, including Deflate64 ones.

    The IRS builds its large May zips with Deflate64 (method 9), which Python's
    zipfile can't decompress. So read the member's raw bytes and decompress here.
    """
    info = zf.getinfo(name)
    zf.fp.seek(info.header_offset)
    header = zf.fp.read(30)
    if header[:4] != b"PK\x03\x04":
        raise zipfile.BadZipFile(f"bad local header for {name}")
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    zf.fp.seek(info.header_offset + 30 + name_len + extra_len)
    raw = zf.fp.read(info.compress_size)
    if info.compress_type == zipfile.ZIP_DEFLATED:
        data = zlib.decompress(raw, -15)
    elif info.compress_type == 9:  # Deflate64
        data = inflate64.Inflater().inflate(raw)
    elif info.compress_type == zipfile.ZIP_STORED:
        data = raw
    else:
        raise NotImplementedError(f"compression method {info.compress_type}")
    if zlib.crc32(data) != info.CRC:
        raise zipfile.BadZipFile(f"CRC mismatch for {name}")
    return data


def _fetch_zip(zip_url: str, object_ids: list[str]) -> tuple[int, list[tuple[str, str]]]:
    fetched, errors = 0, []
    with RemoteZip(zip_url, headers=HTTP_HEADERS, timeout=TIMEOUT) as zf:
        for object_id in object_ids:
            member = f"{object_id}_public.xml"
            for attempt in range(3):
                try:
                    data = read_member(zf, member)
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


@asset(group_name="phase0", deps=[zip_members], description="Cache the latest return per candidate EIN as XML on disk.")
def raw_returns(pg: PostgresResource) -> MaterializeResult:
    XML_DIR.mkdir(parents=True, exist_ok=True)
    with pg.connect() as conn:
        selected = conn.execute(SELECT_LATEST).fetchall()

    todo = defaultdict(list)
    for object_id, _, zip_url in selected:
        if not xml_path(object_id).exists():
            todo[zip_url].append(object_id)

    fetched, errors = 0, []
    with ThreadPoolExecutor(max_workers=max(1, len(todo))) as pool:
        for n, errs in pool.map(lambda item: _fetch_zip(*item), todo.items()):
            fetched += n
            errors.extend(errs)

    with pg.connect() as conn:
        conn.execute("DELETE FROM load_errors WHERE stage = %s", (STAGE,))
        for object_id, error in errors:
            pg.log_error(conn, STAGE, object_id, error)

    return MaterializeResult(metadata={
        "selected": len(selected),
        "already_cached": len(selected) - sum(len(v) for v in todo.values()),
        "fetched": fetched,
        "failed": len(errors),
        "failure_rate": round(len(errors) / len(selected), 4) if selected else 0.0,
    })
