"""The one place the app asks what time it is.

Every time in this app is a local wall-clock time with no timezone attached (the Pi runs
on America/New_York; see docs/SPEC.md). Tests replace `now` to freeze time.
"""
from datetime import datetime

MINUTE_FORMAT = '%Y-%m-%dT%H:%M'      # event times, e.g. 2026-10-14T18:30
SECOND_FORMAT = '%Y-%m-%dT%H:%M:%S'   # timestamps, e.g. 2026-09-22T14:10:00


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def stamp(moment: datetime | None = None) -> str:
    """A timestamp string for created_at, generated_at, the audit log, etc."""
    return (moment or now()).strftime(SECOND_FORMAT)


def minute(moment: datetime) -> str:
    """An event time string: 'YYYY-MM-DDTHH:MM'."""
    return moment.strftime(MINUTE_FORMAT)


def parse(text: str) -> datetime:
    """Read a stored time string ('YYYY-MM-DDTHH:MM' or with seconds) back into a datetime."""
    return datetime.fromisoformat(text)
