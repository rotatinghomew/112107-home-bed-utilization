"""Persistent issued plans.

Source CSVs remain untouched.  Issued bundle prices and asset intervals are
snapshotted here so later settlement uses the exact commitment made by the
funding committee.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .assets import Occupancy
from .dates import Interval
from .models import Dataset

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = ROOT / "state" / "commitments.json"


class CommitmentStore:
    def __init__(self, path: str | Path = DEFAULT_STORE):
        self.path = Path(path)
        self.payload: dict[str, Any] = {"plans": {}}
        if self.path.exists():
            self.payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.payload.setdefault("plans", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, plan_id: str) -> dict[str, Any]:
        if plan_id not in self.payload["plans"]:
            raise KeyError(f"unknown issued plan {plan_id}")
        return self.payload["plans"][plan_id]

    def has(self, plan_id: str) -> bool:
        return plan_id in self.payload["plans"]

    def issue(self, plan: dict[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
        plan_id = plan["plan_id"]
        if self.has(plan_id) and not overwrite:
            raise ValueError(f"plan {plan_id} already exists; use a new id or overwrite explicitly")
        self.payload["plans"][plan_id] = plan
        self.save()
        return plan

    def occupancies(self, *, active_only: bool = True) -> list[Occupancy]:
        result: list[Occupancy] = []
        for plan_id, plan in self.payload["plans"].items():
            if active_only and plan.get("status") not in {"issued", "active", "signed"}:
                continue
            for slot in plan.get("asset_slots", []):
                if slot.get("released"):
                    continue
                result.append(
                    Occupancy(
                        asset=slot["asset"],
                        home=slot["home"],
                        period=Interval(date.fromisoformat(slot["start"]), date.fromisoformat(slot["end"])),
                        source=slot.get("source", plan_id),
                    )
                )
        return result

    def update(self, plan_id: str, **changes: Any) -> dict[str, Any]:
        plan = self.get(plan_id)
        plan.update(changes)
        self.save()
        return plan

    def new_commitment_cost(
        self,
        *,
        district: str | None = None,
        exclude_plan_id: str | None = None,
        active_only: bool = True,
    ) -> int:
        """Incremental signed amount not already present in source decisions."""

        total = 0
        for plan_id, plan in self.payload["plans"].items():
            if plan_id == exclude_plan_id:
                continue
            if district and plan.get("district") != district:
                continue
            if active_only and plan.get("status") not in {"issued", "active", "signed"}:
                continue
            total += int(plan.get("new_commitment_cost_cents", plan.get("cost_cents", 0)))
        return total


def make_plan_record(
    *,
    plan_id: str,
    district: str,
    issue_date: date,
    budget_cents: int,
    minimum_days: int,
    option: dict[str, Any],
    dataset: Dataset,
    season: str | None = None,
) -> dict[str, Any]:
    """Create the immutable-ish commitment record from a feasible option."""

    preexisting_locked_cost = int(option.get("locked_cost_cents", 0))
    total_cost = int(option["cost_cents"])
    selected_bundle_ids = set(option["bundles"])
    quote_snapshot: dict[str, dict[str, Any]] = {}
    for quote in dataset.quotes:
        if quote.bundle in selected_bundle_ids:
            quote_snapshot[quote.quote] = {
                "bundle": quote.bundle,
                "home": quote.home,
                "line_price_cents": quote.line_price,
                "bundle_price_cents": quote.bundle_price,
                "work_start": quote.work_period.start.isoformat(),
                "work_end": quote.work_period.end.isoformat(),
                "valid_until": quote.valid_until.isoformat() if quote.valid_until else None,
            }

    asset_slots: list[dict[str, Any]] = []
    requirement_periods = {r["id"]: r for r in option.get("requirements", [])}
    for req_id, asset_id in option.get("assignments", {}).items():
        if not asset_id:
            continue
        if req_id in requirement_periods:
            req = requirement_periods[req_id]
            start, end, home = req["start"], req["end"], _home_for_requirement(option, req_id)
        else:
            # Fixed/signed requirements retain the source decision interval.
            decision = next(
                (d for d in dataset.decisions if d.asset == asset_id and d.status == "signed"),
                None,
            )
            if decision is None:
                continue
            start, end, home = decision.period.start.isoformat(), decision.period.end.isoformat(), decision.home
        asset_slots.append(
            {
                "asset": asset_id,
                "home": home,
                "start": start,
                "end": end,
                "requirement": req_id,
                "released": False,
            }
        )

    return {
        "plan_id": plan_id,
        "status": "issued",
        "issued_at": issue_date.isoformat(),
        "district": district,
        "season": season,
        "budget_cents": budget_cents,
        "minimum_days": minimum_days,
        "selected_bundles": option["bundles"],
        "cost_cents": total_cost,
        "preexisting_locked_cost_cents": preexisting_locked_cost,
        "new_commitment_cost_cents": total_cost - preexisting_locked_cost,
        "budget_remaining_cents": option["budget_remaining_cents"],
        "original_forecast": {
            "conservative_days": option["conservative_days"],
            "median_days": option["median_days"],
            "optimistic_days": option["optimistic_days"],
            "hard_ceiling_days": option["hard_ceiling_days"],
            "households": option["households"],
        },
        "quote_snapshot": quote_snapshot,
        "asset_slots": asset_slots,
        "option_snapshot": option,
    }


def _home_for_requirement(option: dict[str, Any], requirement_id: str) -> str:
    bundle_id = requirement_id.split(":", 1)[0]
    details = option.get("households", {})
    for home, row in details.items():
        if row.get("bundle") == bundle_id:
            return home
    return ""
