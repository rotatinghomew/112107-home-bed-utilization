"""Typed records for the family-bed accounting data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .dates import Interval, interval, parse_date


@dataclass(frozen=True)
class Household:
    home: str
    district: str
    kind: str
    access: str
    period: Interval
    assessed_at: Optional[date]


@dataclass(frozen=True)
class QuoteLine:
    quote: str
    bundle: str
    home: str
    work: str
    asset_kind: str
    line_price: int
    bundle_price: int
    work_period: Interval
    valid_until: Optional[date]

    @property
    def id(self) -> str:
        return self.quote


@dataclass(frozen=True)
class Asset:
    asset: str
    kind: str
    held_for: str
    reserved_until: Optional[date]
    received_back: Optional[date]
    cooldown_days: int
    recorded_at: Optional[date]

    @property
    def id(self) -> str:
        return self.asset


@dataclass(frozen=True)
class Evidence:
    evidence: str
    home: str
    kind: str
    related: str
    effective: Optional[date]
    recorded_at: Optional[date]
    replaces: str

    @property
    def id(self) -> str:
        return self.evidence


@dataclass(frozen=True)
class Decision:
    document: str
    home: str
    quote: str
    asset: str
    status: str
    period: Interval
    amount: int


@dataclass(frozen=True)
class Budget:
    season: str
    district: str
    budget: int
    minimum_days: int
    min_analogues: int


@dataclass(frozen=True)
class VisitWindow:
    team: str
    district: str
    open_period: Interval
    recorded_at: Optional[date]
    revision: int


@dataclass(frozen=True)
class AnalogueCase:
    home: str
    district: str
    kind: str
    access: str
    approved_period: Interval
    verified: Optional[date]
    device_ready: Optional[date]
    first_visit: Optional[date]
    observation_end: Optional[date]
    recorded_at: Optional[date]


@dataclass(frozen=True)
class Dataset:
    households: list[Household]
    quotes: list[QuoteLine]
    assets: list[Asset]
    evidence: list[Evidence]
    decisions: list[Decision]
    budgets: list[Budget]
    visit_windows: list[VisitWindow]
    analogues: list[AnalogueCase]

    def household(self, home: str) -> Household:
        for row in self.households:
            if row.home == home:
                return row
        raise KeyError(f"unknown household {home}")

    def budget(self, district: str, season: str | None = None) -> Budget:
        rows = [b for b in self.budgets if b.district == district and (season is None or b.season == season)]
        if not rows:
            raise KeyError(f"no budget for district {district}")
        if season is None and len(rows) > 1:
            raise ValueError(f"multiple budget seasons for {district}; specify season")
        return rows[0]
