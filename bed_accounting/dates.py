"""Date interval helpers.

All business intervals in this project are half-open ``[start, end)``.  This
matches ``decisions.csv`` and makes the day count ``end - start``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional


def parse_date(value: str | None) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    # Source fields are dates, but tolerate ISO timestamps as well.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date() if "T" in value else date.fromisoformat(value)


def parse_recorded_date(value: str | None) -> Optional[date]:
    return parse_date(value)


def as_date(value: date | str | None) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    return parse_date(value)


@dataclass(frozen=True, order=True)
class Interval:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"interval end {self.end} precedes start {self.start}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def contains(self, day: date) -> bool:
        return self.start <= day < self.end

    def intersect(self, other: "Interval | None") -> "Interval | None":
        if other is None:
            return None
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        if start >= end:
            return None
        return Interval(start, end)

    def shifted(self, days: int) -> "Interval":
        delta = timedelta(days=days)
        return Interval(self.start + delta, self.end + delta)


def interval(start: date | str | None, end: date | str | None) -> Interval | None:
    start_d = as_date(start)
    end_d = as_date(end)
    if start_d is None or end_d is None:
        return None
    return Interval(start_d, end_d)
