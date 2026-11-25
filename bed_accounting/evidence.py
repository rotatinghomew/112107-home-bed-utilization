"""Evidence validity, supersession, and strict activation assessment."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .dates import Interval
from .models import Dataset, Evidence, Household, VisitWindow


def known_evidence(rows: list[Evidence], as_of: date | None = None) -> list[Evidence]:
    """Return evidence records that had been recorded by ``as_of``."""

    result: list[Evidence] = []
    for row in rows:
        if as_of is not None:
            if row.recorded_at is None or row.recorded_at > as_of:
                continue
        result.append(row)
    return result


def active_evidence(rows: list[Evidence], as_of: date | None = None) -> list[Evidence]:
    """Drop records replaced by a known correction.

    A replacement such as ``wrong_scope`` is retained in the source data but the
    record named in its ``replaces`` field can no longer support a claim.
    """

    known = known_evidence(rows, as_of)
    replaced_ids = {row.replaces for row in known if row.replaces}
    return [row for row in known if row.evidence not in replaced_ids]


def windows_as_of(windows: list[VisitWindow], as_of: date | None = None) -> list[VisitWindow]:
    if as_of is None:
        return list(windows)
    return [w for w in windows if w.recorded_at is not None and w.recorded_at <= as_of]


def current_team_window(
    windows: list[VisitWindow], team: str, as_of: date | None = None
) -> VisitWindow | None:
    rows = [w for w in windows_as_of(windows, as_of) if w.team == team]
    if not rows:
        return None
    return max(rows, key=lambda w: (w.revision, w.recorded_at or date.min))


@dataclass(frozen=True)
class DeviceRequirement:
    key: str
    candidate_asset_ids: list[str]


@dataclass
class ActivationAssessment:
    home: str
    as_of: date | None
    confirmed_range: Interval | None = None
    upper_bound_range: Interval | None = None
    missing_acceptances: list[str] = field(default_factory=list)
    invalid_or_missing_devices: list[str] = field(default_factory=list)
    missing_first_visit: bool = True
    window_ok: bool = False
    window: VisitWindow | None = None
    used_evidence: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    hard_start: date | None = None

    @property
    def confirmed_days(self) -> int:
        return self.confirmed_range.days if self.confirmed_range else 0

    @property
    def possible_upper_bound_days(self) -> int:
        return self.upper_bound_range.days if self.upper_bound_range else 0

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_range is not None

    def committed_days(self, commitment: Interval | None) -> int:
        if commitment is None or self.confirmed_range is None:
            return 0
        rng = self.confirmed_range.intersect(commitment)
        return rng.days if rng else 0

    def committed_upper_bound_days(self, commitment: Interval | None) -> int:
        if commitment is None or self.upper_bound_range is None:
            return 0
        rng = self.upper_bound_range.intersect(commitment)
        return rng.days if rng else 0

    def blockers(self) -> list[str]:
        problems: list[str] = []
        if self.missing_acceptances:
            problems.append("缺少有效适配验收: " + ",".join(self.missing_acceptances))
        if self.invalid_or_missing_devices:
            problems.append("缺少设备可用证据: " + ",".join(self.invalid_or_missing_devices))
        if self.missing_first_visit:
            problems.append("缺少责任机构首个服务记录")
        elif not self.window_ok:
            problems.append("首访不在责任团队可用窗口内")
        return problems


def assess_household(
    dataset: Dataset,
    household: Household,
    *,
    acceptance_targets: list[str],
    device_requirements: list[DeviceRequirement],
    as_of: date | None = None,
    hard_start: date | None = None,
    end_cap: date | None = None,
) -> ActivationAssessment:
    """Assess strict enabled-bed interval and an evidence-gap upper bound."""

    rows = active_evidence([e for e in dataset.evidence if e.home == household.home], as_of)
    corrections = [
        f"{e.evidence}:{e.kind}->{e.replaces}"
        for e in known_evidence(dataset.evidence, as_of)
        if e.replaces and e.home == household.home
    ]

    accepted = [e for e in rows if e.kind == "accepted" and e.related in acceptance_targets]
    accepted_targets = {e.related for e in accepted if e.effective is not None}
    missing_acceptances = [q for q in acceptance_targets if q not in accepted_targets]

    ready = [e for e in rows if e.kind == "device_ready"]
    ready_assets = {e.related for e in ready if e.effective is not None}
    missing_devices: list[str] = []
    used_device_evidence: list[Evidence] = []
    for requirement in device_requirements:
        hits = [
            e
            for e in ready
            if e.related in requirement.candidate_asset_ids and e.effective is not None
        ]
        if not hits:
            missing_devices.append(requirement.key)
            continue
        used_device_evidence.extend(hits)

    visits = [e for e in rows if e.kind == "first_visit" and e.effective is not None]
    first_visit = min(visits, key=lambda e: e.effective) if visits else None  # type: ignore[arg-type]

    window = None
    window_ok = False
    if first_visit is not None:
        window = current_team_window(dataset.visit_windows, first_visit.related, as_of)
        if window is not None and first_visit.effective is not None:
            # The named team must serve this district and the visit must fall
            # in its currently observed availability interval.
            window_ok = window.district == household.district and window.open_period.contains(
                first_visit.effective
            )

    used = [e.evidence for e in accepted + used_device_evidence]
    if first_visit is not None:
        used.append(first_visit.evidence)

    known_dates = [
        e.effective
        for e in accepted + used_device_evidence + ([first_visit] if first_visit else [])
        if e.effective is not None
    ]
    lower_bounds = [household.period.start]
    if hard_start is not None:
        lower_bounds.append(hard_start)
    upper_start = max(lower_bounds + known_dates)
    effective_end = household.period.end
    if end_cap is not None:
        effective_end = min(effective_end, end_cap)
    upper_range: Interval | None = None
    if upper_start < effective_end:
        upper_range = Interval(upper_start, effective_end)

    result = ActivationAssessment(
        home=household.home,
        as_of=as_of,
        upper_bound_range=upper_range,
        missing_acceptances=missing_acceptances,
        invalid_or_missing_devices=missing_devices,
        missing_first_visit=first_visit is None,
        window_ok=window_ok,
        window=window,
        used_evidence=sorted(set(used)),
        corrections=corrections,
        hard_start=hard_start,
    )

    if not missing_acceptances and not missing_devices and first_visit is not None and window_ok:
        start = max(known_dates)
        if start < effective_end:
            result.confirmed_range = Interval(start, effective_end)

    return result
