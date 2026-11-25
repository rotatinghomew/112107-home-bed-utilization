"""Asset availability, cooldown, and no-double-booking rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .dates import Interval
from .models import Asset, Decision


@dataclass(frozen=True)
class Occupancy:
    asset: str
    home: str
    period: Interval
    source: str


def earliest_available(asset: Asset) -> date | None:
    """Earliest date a *new household* may use an asset.

    Returned equipment must have a warehouse receipt (``received_back``) and
    then complete its disinfection/turnaround period.  A reservation expiry is
    not a warehouse return.  Assets already in idle stock have no return clock.
    """

    if asset.received_back is not None:
        return asset.received_back + timedelta(days=asset.cooldown_days)
    if not asset.held_for:
        return asset.recorded_at or date.min
    # Still held at a home with no observed return.
    return None


def decision_occupancies(decisions: Iterable[Decision], only_signed: bool = True) -> list[Occupancy]:
    result = []
    for d in decisions:
        if only_signed and d.status != "signed":
            continue
        if d.asset:
            result.append(Occupancy(d.asset, d.home, d.period, d.document))
    return result


def _overlaps(a: Interval, b: Interval) -> bool:
    return a.intersect(b) is not None


def find_assignments(
    requirements: list[tuple[str, Interval, str]],
    # requirement id, desired interval, asset kind
    assets: list[Asset],
    occupancies: list[Occupancy],
    *,
    unavailable_asset_ids: set[str] | None = None,
) -> dict[str, str] | None:
    """Backtrack over asset assignments.

    The tiny committee data has only a few assets, but backtracking correctly
    handles cases where choosing the first compatible asset would block a later
    requirement of the same kind.
    """

    unavailable_asset_ids = unavailable_asset_ids or set()
    by_kind: dict[str, list[Asset]] = {}
    for asset in assets:
        by_kind.setdefault(asset.kind, []).append(asset)
    for rows in by_kind.values():
        rows.sort(key=lambda a: a.asset)

    def compatible(asset: Asset, period: Interval) -> bool:
        if asset.asset in unavailable_asset_ids:
            return False
        earliest = earliest_available(asset)
        if earliest is None or earliest > period.start:
            return False
        for occ in occupancies:
            if occ.asset == asset.asset and _overlaps(occ.period, period):
                return False
        return True

    assignment: dict[str, str] = {}
    used_assets: set[str] = set()

    def visit(index: int) -> bool:
        if index == len(requirements):
            return True
        req_id, period, kind = requirements[index]
        for asset in by_kind.get(kind, []):
            if asset.asset in used_assets or not compatible(asset, period):
                continue
            # Two requirements in the same plan cannot share an asset, even if
            # their intervals happen to be represented identically here.
            used_assets.add(asset.asset)
            assignment[req_id] = asset.asset
            if visit(index + 1):
                return True
            used_assets.remove(asset.asset)
            assignment.pop(req_id, None)
        return False

    requirements = sorted(requirements, key=lambda x: x[2])
    return assignment if visit(0) else None
