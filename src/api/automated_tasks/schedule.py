from __future__ import annotations

from datetime import datetime
from datetime import timezone
from datetime import tzinfo

from croniter import croniter

try:  # zoneinfo needs the system tzdata or the tzdata package; degrade to UTC.
  from zoneinfo import ZoneInfo
  from zoneinfo import ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python <3.9 only
  ZoneInfo = None  # type: ignore[assignment]
  ZoneInfoNotFoundError = Exception  # type: ignore[assignment,misc]

# Matches contract.task_store.now_iso(): UTC, second precision, 'Z' suffix.
# Critical because next_fire_at is compared to now_iso() as a *string* in SQL,
# and this format sorts lexicographically the same as chronologically.
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_iso(value: str | None) -> datetime | None:
  if not value:
    return None
  try:
    return datetime.strptime(value, _ISO_FMT).replace(tzinfo=timezone.utc)
  except (TypeError, ValueError):
    return None


def _resolve_tz(name: str | None) -> tzinfo:
  if not name or ZoneInfo is None:
    return timezone.utc
  try:
    return ZoneInfo(name)
  except (ZoneInfoNotFoundError, ValueError, KeyError):
    return timezone.utc


def compute_next_fire(
    cron: str,
    timezone_name: str | None = None,
    *,
    after: str | None = None,
) -> str | None:
  """Next fire time strictly after ``after`` (default: now), as a UTC
  ``%Y-%m-%dT%H:%M:%SZ`` string matching ``now_iso()``.

  The cron is evaluated in ``timezone_name`` (IANA, e.g. "America/New_York";
  default UTC) so DST is handled correctly, then converted back to UTC for
  storage. Returns ``None`` for an empty/unparseable cron so a malformed
  trigger is simply inert rather than crashing the dispatch loop.
  """
  expr = (cron or "").strip()
  if not expr:
    return None
  tz = _resolve_tz(timezone_name)
  base_utc = _parse_iso(after) or datetime.now(timezone.utc)
  base_local = base_utc.astimezone(tz)
  try:
    iterator = croniter(expr, base_local)
    next_local = iterator.get_next(datetime)
  except (ValueError, KeyError, AttributeError):
    return None
  return next_local.astimezone(timezone.utc).strftime(_ISO_FMT)
