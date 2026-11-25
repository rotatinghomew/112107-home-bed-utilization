"""Point-in-time analogue estimates.

Waiting/censored households are deliberately kept separate from households
observed through the end of their approval period without a first visit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from .models import AnalogueCase, Household


@dataclass(frozen=True)
class AnalogueSummary:
    as_of: date | None
    sample_size: int
    activation_samples: int
    confirmed_inactive: int
    waiting: int
    min_analogues: int
    qualifying_offsets: tuple[int, ...]
    idle_lags: tuple[int, ...]
    supported: bool

    @property
    def median_offset(self) -> int | None:
        return int(median(self.qualifying_offsets)) if self.qualifying_offsets else None

    @property
    def conservative_offset(self) -> int | None:
        # Largest observed qualifying offset is the prudent planning bound.
        return max(self.qualifying_offsets) if self.qualifying_offsets else None

    @property
    def optimistic_offset(self) -> int | None:
        return min(self.qualifying_offsets) if self.qualifying_offsets else None


def comparable_cases(
    cases: list[AnalogueCase],
    household: Household,
    as_of: date | None = None,
    *,
    district_match: bool = True,
) -> list[AnalogueCase]:
    result = []
    for case in cases:
        if as_of is not None and (case.recorded_at is None or case.recorded_at > as_of):
            continue
        if case.kind != household.kind or case.access != household.access:
            continue
        if district_match and case.district != household.district:
            continue
        result.append(case)
    return result


def classify_cases(cases: list[AnalogueCase]) -> tuple[list[AnalogueCase], list[AnalogueCase], list[AnalogueCase]]:
    activated: list[AnalogueCase] = []
    inactive: list[AnalogueCase] = []
    waiting: list[AnalogueCase] = []

    for case in cases:
        if case.verified and case.device_ready and case.first_visit:
            activated.append(case)
        elif case.observation_end and case.observation_end >= case.approved_period.end:
            # The whole approved interval was observable and no qualifying
            # first visit was recorded.
            inactive.append(case)
        else:
            # Right-censored/waiting: absence of a date here is not a zero.
            waiting.append(case)
    return activated, inactive, waiting


def summarise(
    cases: list[AnalogueCase],
    household: Household,
    min_analogues: int,
    as_of: date | None = None,
    *,
    district_match: bool = True,
) -> AnalogueSummary:
    comparable = comparable_cases(cases, household, as_of, district_match=district_match)
    activated, inactive, waiting = classify_cases(comparable)
    offsets: list[int] = []
    idle_lags: list[int] = []
    for case in activated:
        assert case.verified and case.device_ready and case.first_visit
        qualifying_start = max(case.verified, case.device_ready, case.first_visit)
        offsets.append((qualifying_start - case.approved_period.start).days)
        idle_lags.append((case.first_visit - max(case.verified, case.device_ready)).days)

    offsets.sort()
    idle_lags.sort()
    supported = len(comparable) >= min_analogues and len(activated) >= min_analogues
    return AnalogueSummary(
        as_of=as_of,
        sample_size=len(comparable),
        activation_samples=len(activated),
        confirmed_inactive=len(inactive),
        waiting=len(waiting),
        min_analogues=min_analogues,
        qualifying_offsets=tuple(offsets),
        idle_lags=tuple(idle_lags),
        supported=supported,
    )


@dataclass(frozen=True)
class Forecast:
    optimistic_start: date | None
    median_start: date | None
    conservative_start: date | None
    optimistic_days: int
    median_days: int
    conservative_days: int
    hard_start: date | None
    summary: AnalogueSummary

    def as_dict(self) -> dict:
        return {
            "supported": self.summary.supported,
            "sample_size": self.summary.sample_size,
            "activation_samples": self.summary.activation_samples,
            "confirmed_inactive": self.summary.confirmed_inactive,
            "waiting": self.summary.waiting,
            "offsets": list(self.summary.qualifying_offsets),
            "idle_lags": list(self.summary.idle_lags),
            "optimistic_start": _iso(self.optimistic_start),
            "median_start": _iso(self.median_start),
            "conservative_start": _iso(self.conservative_start),
            "optimistic_days": self.optimistic_days,
            "median_days": self.median_days,
            "conservative_days": self.conservative_days,
            "hard_start": _iso(self.hard_start),
        }


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def forecast_household(
    household: Household,
    summary: AnalogueSummary,
    *,
    horizon_end: date | None = None,
    hard_start: date | None = None,
    delay_days: int = 0,
) -> Forecast:
    """Apply empirical offsets from the approved-period start.

    ``hard_start`` is a non-statistical lower bound from quote schedule, asset
    cooldown and responsible-team availability.  The same rule is applied to
    every candidate, avoiding plan-specific favourable assumptions.
    """

    end = horizon_end or household.period.end

    def build(offset: int | None) -> tuple[date | None, int]:
        if offset is None or not summary.supported:
            return None, 0
        start = household.period.start + timedelta(days=offset + delay_days)
        if hard_start is not None:
            start = max(start, hard_start + timedelta(days=delay_days))
        if start >= end:
            return start, 0
        return start, (end - start).days

    opt_s, opt_d = build(summary.optimistic_offset)
    med_s, med_d = build(summary.median_offset)
    con_s, con_d = build(summary.conservative_offset)
    return Forecast(
        optimistic_start=opt_s,
        median_start=med_s,
        conservative_start=con_s,
        optimistic_days=opt_d,
        median_days=med_d,
        conservative_days=con_d,
        hard_start=hard_start,
        summary=summary,
    )
