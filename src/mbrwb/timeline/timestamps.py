"""Timestamp parsing with explicit precision and timezone semantics.

Instants are stored as integer *ticks*: 100-nanosecond units since 1601-01-01T00:00:00Z (the
Windows FILETIME scale). Integer arithmetic keeps FILETIME-level precision that Python's
``datetime`` (microseconds) would silently truncate. The original text is always kept next
to the parsed value.

Status values (``TimestampStatus``) distinguish: explicit UTC, explicit offset, the tool's
documented UTC convention, an operator-supplied source timezone, unresolved local-looking
values, DST-ambiguous and non-existent local times, zero/sentinel values and invalid text.
Display-timezone changes never reinterpret the stored instant.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from ..domain.enums import TimestampStatus

TICKS_PER_SECOND = 10_000_000
_EPOCH_ORD = dt.date(1601, 1, 1).toordinal()
EPOCH_1601 = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)

_TS = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?\s*(Z|UTC|[+-]\d{2}:?\d{2})?\s*$",
    re.IGNORECASE,
)
_ZERO_PREFIXES = ("0001-01-01 00:00:00", "1601-01-01 00:00:00", "0001-01-01T00:00:00", "1601-01-01T00:00:00")


@dataclass(frozen=True)
class ParsedTimestamp:
    raw: str
    status: TimestampStatus
    ticks: int | None  # UTC ticks when resolved
    precision: int  # fractional-second digits present in the source text (0-9)
    offset_minutes: int | None = None  # explicit offset in the value
    note: str = ""

    @property
    def resolved(self) -> bool:
        return self.status.is_resolved and self.ticks is not None


def _ticks_from_fields(y, mo, d, h, mi, s, frac_digits: str) -> int:
    days = dt.date(y, mo, d).toordinal() - _EPOCH_ORD
    secs = days * 86400 + h * 3600 + mi * 60 + s
    frac = (frac_digits or "")[:7].ljust(7, "0")
    return secs * TICKS_PER_SECOND + int(frac)


_DAYS_CACHE: dict[str, int] = {}


def _fast_tool_utc(text: str) -> ParsedTimestamp | None:
    """Fast path for the EZ Tools default formats 'YYYY-MM-DD HH:MM:SS' and
    'YYYY-MM-DD HH:MM:SS.fffffff' under the tool UTC convention. Returns None for anything else
    (the general parser then handles it), so semantics are identical, only faster."""
    n = len(text)
    if n not in (19, 27) or text[4] != "-" or text[7] != "-" or text[10] != " " or text[13] != ":" or text[16] != ":" or (n == 27 and text[19] != "."):
        return None
    date = text[:10]
    if date in ("0001-01-01", "1601-01-01") and text[11:19] == "00:00:00" and (n == 19 or text[20:] == "0000000"):
        return None  # zero/sentinel: let the general parser label it
    days = _DAYS_CACHE.get(date)
    try:
        if days is None:
            days = dt.date(int(date[:4]), int(date[5:7]), int(date[8:10])).toordinal() - _EPOCH_ORD
            if len(_DAYS_CACHE) < 200_000:
                _DAYS_CACHE[date] = days
        h, mi, s = int(text[11:13]), int(text[14:16]), int(text[17:19])
        frac = int(text[20:27]) if n == 27 else 0
    except ValueError:
        return None
    if h > 23 or mi > 59 or s > 59:
        return None
    return ParsedTimestamp(text, TimestampStatus.TOOL_UTC_CONVENTION, (days * 86400 + h * 3600 + mi * 60 + s) * TICKS_PER_SECOND + frac, 7 if n == 27 else 0)


def parse_timestamp(raw: str | None, *, convention: str = "unknown", source_tz: str | None = None) -> ParsedTimestamp:
    """Parse ``raw``.

    ``convention`` describes values *without* an explicit zone: ``"utc"`` when the producing tool
    documents UTC output (status ``TOOL_UTC_CONVENTION``), otherwise ``"unknown"``. When unknown,
    an operator-supplied IANA ``source_tz`` resolves the value (``OPERATOR_ASSUMPTION``) unless
    the local time is ambiguous or non-existent in that zone.
    """
    if raw is None or not str(raw).strip():
        return ParsedTimestamp(raw or "", TimestampStatus.EMPTY, None, 0)
    text = str(raw)
    if convention == "utc" and not source_tz:
        fast = _fast_tool_utc(text)
        if fast is not None:
            return fast
    m = _TS.match(text)
    if not m:
        return ParsedTimestamp(text, TimestampStatus.INVALID, None, 0, note="unrecognised format")
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    s = int(m.group(6) or 0)
    frac = m.group(7) or ""
    zone = m.group(8)
    precision = len(frac)
    if text.strip().startswith(_ZERO_PREFIXES) and not frac.strip("0"):
        return ParsedTimestamp(text, TimestampStatus.ZERO, None, precision, note="sentinel/zero value")
    try:
        local_ticks = _ticks_from_fields(y, mo, d, h, mi, s, frac)
    except ValueError as exc:
        return ParsedTimestamp(text, TimestampStatus.INVALID, None, precision, note=str(exc))
    note = "digits beyond 100 ns kept in raw text only" if precision > 7 else ""
    if zone:
        z = zone.upper()
        if z in ("Z", "UTC") or z.replace(":", "") in ("+0000", "-0000"):
            return ParsedTimestamp(text, TimestampStatus.UTC, local_ticks, precision, 0, note)
        sign = 1 if z[0] == "+" else -1
        hh, mm = int(z[1:3]), int(z[-2:])
        off = sign * (hh * 60 + mm)
        return ParsedTimestamp(text, TimestampStatus.OFFSET, local_ticks - off * 60 * TICKS_PER_SECOND, precision, off, note)
    if convention == "utc":
        return ParsedTimestamp(text, TimestampStatus.TOOL_UTC_CONVENTION, local_ticks, precision, None, note)
    if source_tz:
        return _resolve_local(text, y, mo, d, h, mi, s, local_ticks, precision, source_tz, note)
    return ParsedTimestamp(text, TimestampStatus.UNRESOLVED, None, precision, None, "no offset and no verified timezone rule")


def _resolve_local(text, y, mo, d, h, mi, s, local_ticks, precision, tzname, note) -> ParsedTimestamp:
    tz = ZoneInfo(tzname)
    naive = dt.datetime(y, mo, d, h, mi, s)
    a0, a1 = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    o0, o1 = a0.utcoffset(), a1.utcoffset()
    if o0 != o1:
        back0 = a0.astimezone(dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
        back1 = a1.astimezone(dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
        if back0 != naive and back1 != naive:
            return ParsedTimestamp(text, TimestampStatus.NONEXISTENT, None, precision, None, f"local time does not exist in {tzname} (DST gap)")
        return ParsedTimestamp(text, TimestampStatus.AMBIGUOUS, None, precision, None,
                               f"local time occurs twice in {tzname} (offsets {o0} and {o1})")
    off_s = int(o0.total_seconds())
    return ParsedTimestamp(text, TimestampStatus.OPERATOR_ASSUMPTION, local_ticks - off_s * TICKS_PER_SECOND, precision, off_s // 60,
                           (note + "; " if note else "") + f"interpreted as {tzname} (operator assumption)")


def ticks_to_datetime(ticks: int) -> tuple[dt.datetime, int]:
    """(UTC datetime at whole-second resolution, remaining ticks 0..9_999_999)."""
    secs, rem = divmod(ticks, TICKS_PER_SECOND)
    return EPOCH_1601 + dt.timedelta(seconds=secs), rem


def format_ticks(ticks: int | None, tz: str = "UTC", precision: int = 7, *, with_zone: bool = True) -> str:
    if ticks is None:
        return ""
    base, rem = ticks_to_datetime(ticks)
    if tz and tz != "UTC":
        base = base.astimezone(ZoneInfo(tz))
    s = base.strftime("%Y-%m-%d %H:%M:%S")
    p = max(0, min(precision, 7))
    if p:
        s += "." + f"{rem:07d}"[:p]
    if with_zone:
        off = base.utcoffset() or dt.timedelta(0)
        total = int(off.total_seconds() // 60)
        sign = "+" if total >= 0 else "-"
        s += f" {sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"
    return s


def ticks_from_datetime(value: dt.datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("aware datetime required")
    u = value.astimezone(dt.timezone.utc)
    return _ticks_from_fields(u.year, u.month, u.day, u.hour, u.minute, u.second, f"{u.microsecond:06d}0")


def parse_operator_bound(text: str, tz: str) -> int | None:
    """Parse a user-entered range bound like ``2024-03-01 10:00`` interpreted in ``tz``."""
    p = parse_timestamp(text, convention="utc" if tz == "UTC" else "unknown", source_tz=None if tz == "UTC" else tz)
    return p.ticks if p.resolved else None
