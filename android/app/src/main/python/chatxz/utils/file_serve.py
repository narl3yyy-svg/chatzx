"""Chunked HTTP file responses (avoids Windows sendfile / WinError 87 on large files)."""

import asyncio
import os
import mimetypes

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

_CLIENT_ABORT_ERRORS = (
    ClientConnectionResetError,
    ConnectionResetError,
    ConnectionError,
    BrokenPipeError,
    asyncio.CancelledError,
)


async def stream_file_response(request, path, content_type=None, chunk_size=256 * 1024):
    """Stream a local file with optional Range support; never uses OS sendfile."""
    if not path or not os.path.isfile(path):
        return None

    total = os.path.getsize(path)
    if content_type is None:
        content_type, _ = mimetypes.guess_type(path)
    if not content_type:
        content_type = "application/octet-stream"

    range_hdr = request.headers.get("Range", "")
    start = 0
    end = total - 1
    if range_hdr.startswith("bytes="):
        spec = range_hdr.split("=", 1)[1].strip()
        if "-" in spec:
            left, right = spec.split("-", 1)
            try:
                if left:
                    start = max(0, int(left))
                if right:
                    end = min(total - 1, int(right))
            except ValueError:
                start = 0
                end = total - 1
        if start > end or start >= total:
            return web.Response(status=416, text="range not satisfiable")

    length = max(0, end - start + 1)
    status = 206 if (start or end != total - 1) and range_hdr else 200
    resp = web.StreamResponse(status=status)
    resp.headers["Content-Type"] = content_type
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(length)
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    await resp.prepare(request)

    try:
        with open(path, "rb") as src:
            if start:
                src.seek(start)
            remaining = length
            while remaining > 0:
                chunk = src.read(min(chunk_size, remaining))
                if not chunk:
                    break
                await resp.write(chunk)
                remaining -= len(chunk)
    except _CLIENT_ABORT_ERRORS:
        return resp
    await resp.write_eof()
    return resp