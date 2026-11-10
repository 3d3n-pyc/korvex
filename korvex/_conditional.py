"""Pure helpers for conditional and range requests on static files."""

from __future__ import annotations

import re
from datetime import timezone
from email.utils import formatdate, parsedate_to_datetime
from typing import Any, Optional, Union

UNSATISFIABLE = "unsatisfiable"
"""Returned by `byte_range` when the caller must answer 416."""

_MAX_RANGE_HEADER = 200
_RANGE_SPEC = re.compile(r"([0-9]{1,19})?-([0-9]{1,19})?")

ByteRange = Union[tuple[int, int], str, None]


def etag(mtime_ns: int, size: int) -> str:
    """Return a strong ETag built from the modification time and size."""
    return f'"{mtime_ns:x}-{size:x}"'


def last_modified(mtime: float) -> str:
    """Return `mtime` as an HTTP date (IMF-fixdate)."""
    return formatdate(mtime, usegmt=True)


def _opaque(tag: str) -> str:
    """Strip the weak prefix from an entity tag."""
    return tag[2:] if tag.startswith("W/") else tag


def _etag_matches(header: str, current: str) -> bool:
    """Tell whether an If-None-Match value matches `current` (weak comparison)."""
    for part in header.split(","):
        part = part.strip()
        if part == "*" or _opaque(part) == current:
            return True
    return False


def _not_modified_since(header: str, mtime: float) -> bool:
    """Tell whether the file is not newer than an If-Modified-Since date."""
    try:
        since = parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    try:
        return int(mtime) <= since.timestamp()
    except (OverflowError, OSError, ValueError):
        return False


def not_modified(method: str, headers: Any, etag: str, mtime: float) -> bool:
    """Tell whether a GET or HEAD can be answered with 304 Not Modified."""
    if method not in ("GET", "HEAD"):
        return False
    none_match = headers.get("if-none-match")
    if none_match is not None:
        return _etag_matches(none_match, etag)
    since = headers.get("if-modified-since")
    return since is not None and _not_modified_since(since, mtime)


def _parse_range(value: str) -> Optional[tuple[Optional[int], Optional[int]]]:
    """Parse a single `bytes=` range into (first, last) with None for open ends."""
    if len(value) > _MAX_RANGE_HEADER:
        return None
    unit, sep, spec = value.partition("=")
    if not sep or unit.strip().lower() != "bytes":
        return None
    match = _RANGE_SPEC.fullmatch(spec.strip())
    if match is None or match.group(1) is None and match.group(2) is None:
        return None
    first, last = match.groups()
    return (None if first is None else int(first), None if last is None else int(last))


def _if_range_holds(value: Optional[str], current: str, mtime: float) -> bool:
    """Tell whether an optional If-Range value still matches the file."""
    return value is None or value == current or value == last_modified(mtime)


def _resolve(first: Optional[int], last: Optional[int], size: int) -> ByteRange:
    """Turn parsed range bounds into absolute offsets for a file of `size`."""
    if first is None:
        if not last or not size:
            return UNSATISFIABLE
        return (max(size - last, 0), size - 1)
    if first >= size:
        return UNSATISFIABLE
    if last is not None and last < first:
        return None
    return (first, size - 1 if last is None else min(last, size - 1))


def byte_range(method: str, headers: Any, etag: str, mtime: float, size: int) -> ByteRange:
    """Return (start, end) to serve, `UNSATISFIABLE` for 416, or None for the whole file."""
    value = headers.get("range")
    if method != "GET" or value is None:
        return None
    if not _if_range_holds(headers.get("if-range"), etag, mtime):
        return None
    parsed = _parse_range(value)
    if parsed is None:
        return None
    return _resolve(parsed[0], parsed[1], size)
