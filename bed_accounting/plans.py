"""Bundle construction, feasible scheme enumeration, and Pareto trade-offs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import Any

from .analogues import Forecast, summarise, forecast_household
from .assets import Occupancy, earliest_available, find_assignments
from .dates import Interval
from .models import Budget, Dataset, Decision, Household


@dataclass(frozen=True)
class AssetRequirement:
    requirement_id: str
    quote: str
    kind: str


@dataclass(frozen=True)
class Bundle:
    id: str
    home: str
    district: str
    quote_ids: list[str]
    line_total: int
    bundle_price: int
    start: date
    end: date
    valid_until: date | None
    asset_requirements: list[AssetRequirement]

    @property
    def whole_bundle_saving(self) -> int:
        return self.line_total - self.bundle_price


def build_bundles(dataset: Dataset) -> list[Bundle]:
    groups: dict[str, list] = {}
    for q in dataset.quotes:
        groups.setdefault(q.bundle, []).append(q)

    bundles: list[Bundle] = []
    for bundle_id, lines in sorted(groups.items()):
        homes = {q.home for q in lines}
        if len(homes) != 1:
            raise ValueError(f"bundle {bundle_id} spans multiple households: {homes}")
        home = next(iter(homes))
        household = dataset.household(home)
        prices = {q.bundle_price for q in lines}
        if len(prices) != 1:
            raise ValueError(f"bundle {bundle_id} has inconsistent bundle_price values")
        valid_dates = [q.valid_until for q in lines if q.valid_until]
        reqs = [
            AssetRequirement(f"{bundle_id}:{q.quote}:{q.asset_kind}", q.quote, q.asset_kind)
            for q in lines
            if q.asset_kind
        ]
        bundles.append(
            Bundle(
                id=bundle_id,
                home=home,
                district=household.district,
                quote_ids=[q.quote for q in lines],
                line_total=sum(q.line_price for q in lines),
                bundle_price=next(iter(prices)),
                start=min(q.work_period.start for q in lines),
                end=max(q.work_period.end for q in lines),
                valid_until=min(valid_dates) if valid_dates else None,
                asset_requirements=reqs,
            )
        )
    return sorted(bundles, key=lambda b: b.id)


def price_quote_selection(dataset: Dataset, quote_ids: list[str] | set[str]) -> dict[str, Any]:
    """Price arbitrary quote-line selection.

    The joint ``bundle_price`` is used only when *every* line of a bundle is
    selected.  Partial selection is priced at sum of selected line prices.
    """

    selected = set(quote_ids)
    groups: dict[str, list] = {}
    for quote in dataset.quotes:
        if quote.quote in selected:
            groups.setdefault(quote.bundle, []).append(quote)

    total = 0
    details: dict[str, dict[str, Any]] = {}
    all_by_bundle: dict[str, list] = {}
    for quote in dataset.quotes:
        all_by_bundle.setdefault(quote.bundle, []).append(quote)

    for bundle_id, lines in sorted(groups.items()):
        all_lines = all_by_bundle[bundle_id]
        whole_bundle = len(lines) == len(all_lines) and {q.quote for q in lines} == {
            q.quote for q in all_lines
        }
        if whole_bundle:
            prices = {q.bundle_price for q in lines}
            if len(prices) != 1:
                raise ValueError(f"inconsistent bundle_price for {bundle_id}")
            price = next(iter(prices))
            pricing = "bundle_price"
        else:
            price = sum(q.line_price for q in lines)
            pricing = "line_price_partial"
        total += price
        details[bundle_id] = {
            "selected_quotes": [q.quote for q in lines],
            "price_cents": price,
            "pricing": pricing,
        }
    return {"total_cents": total, "bundles": details}


def _latest_team_windows(dataset: Dataset, district: str, as_of: date | None) -> list:
    latest: dict[str, Any] = {}
    for w in dataset.visit_windows:
        if w.district != district:
            continue
        if as_of is not None and (w.recorded_at is None or w.recorded_at > as_of):
            continue
        old = latest.get(w.team)
        if old is None or (w.revision, w.recorded_at or date.min) > (
            old.revision,
            old.recorded_at or date.min,
        ):
            latest[w.team] = w
    return list(latest.values())


def _hard_start(
    bundle: Bundle,
    dataset: Dataset,
    assignments: dict[str, str],
    as_of: date | None,
) -> date:
    starts = [bundle.end]
    asset_by_id = {a.asset: a for a in dataset.assets}
    for req in bundle.asset_requirements:
        asset_id = assignments.get(req.requirement_id)
        if asset_id:
            available = earliest_available(asset_by_id[asset_id])
            if available:
                starts.append(available)
    windows = _latest_team_windows(dataset, bundle.district, as_of)
    if windows:
        starts.append(min(w.open_period.start for w in windows))
    return max(starts)


def _commitment_end(
    bundle: Bundle,
    household: Household,
    decisions_by_quote: dict[str, Decision],
) -> date:
    ends = [household.period.end]
    for quote_id in bundle.quote_ids:
        decision = decisions_by_quote.get(quote_id)
        if decision:
            ends.append(decision.period.end)
    return min(ends)


@dataclass
class PlanOptionsResult:
    district: str
    budget_cents: int
    minimum_days: int
    as_of: date | None
    locked_bundle_ids: list[str]
    locked_cost_cents: int = 0
    options: list[dict[str, Any]] = field(default_factory=list)
    pareto: list[dict[str, Any]] = field(default_factory=list)
    recommended: dict[str, Any] | None = None
    budget_row: Budget | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "district": self.district,
            "budget_cents": self.budget_cents,
            "minimum_days": self.minimum_days,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "locked_bundle_ids": self.locked_bundle_ids,
            "locked_cost_cents": self.locked_cost_cents,
            "options": self.options,
            "pareto": self.pareto,
            "recommended": self.recommended,
        }


def enumerate_plan_options(
    dataset: Dataset,
    *,
    district: str,
    budget_cents: int | None = None,
    minimum_days: int | None = None,
    season: str | None = None,
    as_of: date | None = None,
    base_occupancies: list[Occupancy] | None = None,
    unavailable_asset_ids: set[str] | None = None,
    excluded_bundle_ids: set[str] | None = None,
    service_delay_days: int = 0,
) -> PlanOptionsResult:
    budget_row = None
    if budget_cents is None or minimum_days is None:
        budget_row = dataset.budget(district, season)
        budget_cents = budget_cents if budget_cents is not None else budget_row.budget
        minimum_days = minimum_days if minimum_days is not None else budget_row.minimum_days
    min_analogues = budget_row.min_analogues if budget_row else 6

    bundles = [b for b in build_bundles(dataset) if b.district == district]
    bundles_by_id = {b.id: b for b in bundles}
    decisions_by_quote = {d.quote: d for d in dataset.decisions if d.status == "signed"}

    locked_ids: set[str] = set()
    locked_cost = 0
    occupancies = list(base_occupancies or [])
    fixed_assignments: dict[str, str] = {}
    for decision in dataset.decisions:
        if decision.status != "signed":
            continue
        quote_to_bundle = {q.quote: q.bundle for q in dataset.quotes}
        bundle_id = quote_to_bundle.get(decision.quote)
        if bundle_id and bundle_id in bundles_by_id:
            locked_ids.add(bundle_id)
            locked_cost += decision.amount
            if decision.asset:
                occupancies.append(Occupancy(decision.asset, decision.home, decision.period, decision.document))
                # Find matching requirement id.
                bundle = bundles_by_id[bundle_id]
                for req in bundle.asset_requirements:
                    if req.quote == decision.quote:
                        fixed_assignments[req.requirement_id] = decision.asset

    optional = [
        b
        for b in bundles
        if b.id not in locked_ids and b.id not in (excluded_bundle_ids or set())
    ]
    result = PlanOptionsResult(
        district=district,
        budget_cents=budget_cents,
        minimum_days=minimum_days,
        as_of=as_of,
        locked_bundle_ids=sorted(locked_ids),
        locked_cost_cents=locked_cost,
        budget_row=budget_row,
    )

    def evaluate(selected_optional: tuple[Bundle, ...]) -> dict[str, Any] | None:
        selected_bundles = [bundles_by_id[i] for i in sorted(locked_ids)] + list(selected_optional)
        selected_ids = [b.id for b in selected_bundles]
        cost = locked_cost + sum(b.bundle_price for b in selected_optional)

        quote_valid = True
        validity_problems: list[str] = []
        for b in selected_optional:
            if as_of and b.valid_until and as_of > b.valid_until:
                quote_valid = False
                validity_problems.append(f"{b.id}报价有效期至{b.valid_until.isoformat()}")

        requirements: list[tuple[str, Interval, str]] = []
        for b in selected_optional:
            household = dataset.household(b.home)
            horizon_end = _commitment_end(b, household, decisions_by_quote)
            needed_start = b.end
            period = Interval(needed_start, horizon_end) if needed_start < horizon_end else None
            if period is None:
                validity_problems.append(f"{b.id}在承诺期末尾之后才具备条件")
                continue
            for req in b.asset_requirements:
                requirements.append((req.requirement_id, period, req.kind))

        assignments = find_assignments(
            requirements,
            dataset.assets,
            occupancies,
            unavailable_asset_ids=unavailable_asset_ids,
        )
        resource_feasible = assignments is not None and not validity_problems
        if assignments is None:
            assignments = {}
        all_assignments = {**fixed_assignments, **assignments}

        bundle_results: dict[str, dict[str, Any]] = {}
        support_ok = True
        for b in selected_bundles:
            household = dataset.household(b.home)
            horizon_end = _commitment_end(b, household, decisions_by_quote)
            hard_start = _hard_start(b, dataset, all_assignments, as_of)
            summary = summarise(dataset.analogues, household, min_analogues, as_of)
            forecast = forecast_household(
                household,
                summary,
                horizon_end=horizon_end,
                hard_start=hard_start,
                delay_days=service_delay_days,
            )
            # Hard ceiling uses no analogue distribution; it asks only what is
            # not ruled out by schedule, cooldown and team availability.
            ceiling_start = hard_start
            if service_delay_days:
                from datetime import timedelta

                ceiling_start = ceiling_start + timedelta(days=service_delay_days)
            hard_days = max(0, (horizon_end - ceiling_start).days) if ceiling_start < horizon_end else 0
            if not summary.supported:
                support_ok = False
            bundle_results[b.id] = {
                "bundle": b.id,
                "home": b.home,
                "district": household.district,
                "horizon_end": horizon_end.isoformat(),
                "assigned_assets": {
                    req.requirement_id: all_assignments.get(req.requirement_id)
                    for req in b.asset_requirements
                },
                "hard_start": hard_start.isoformat(),
                "hard_ceiling_days": hard_days,
                "forecast": forecast.as_dict(),
                "conservative_days": forecast.conservative_days if summary.supported else None,
                "median_days": forecast.median_days if summary.supported else None,
                "optimistic_days": forecast.optimistic_days if summary.supported else None,
            }

        # Days are counted once per household.  Multiple bid sections/bundles
        # serving the same bed are joint conditions, so the controlling section
        # is the one with the fewest days (latest common activation).
        household_days: dict[str, dict[str, Any]] = {}
        for home in sorted({b.home for b in selected_bundles}):
            rows = [row for row in bundle_results.values() if row["home"] == home]

            def min_supported(key: str) -> int | None:
                values = [row[key] for row in rows if row[key] is not None]
                return min(values) if values else None

            controlling = min(
                rows,
                key=lambda row: row["conservative_days"] if row["conservative_days"] is not None else 10**9,
            )
            merged_assets: dict[str, str | None] = {}
            for row in rows:
                merged_assets.update(row["assigned_assets"])
            household_days[home] = {
                **controlling,
                "bundle": "+".join(row["bundle"] for row in rows),
                "bundles": [row["bundle"] for row in rows],
                "assigned_assets": merged_assets,
                "conservative_days": min_supported("conservative_days"),
                "median_days": min_supported("median_days"),
                "optimistic_days": min_supported("optimistic_days"),
                "hard_ceiling_days": min(row["hard_ceiling_days"] for row in rows),
            }

        total_conservative = sum(
            row["conservative_days"] or 0 for row in household_days.values()
        )
        total_median = sum(row["median_days"] or 0 for row in household_days.values())
        total_optimistic = sum(row["optimistic_days"] or 0 for row in household_days.values())
        total_hard_ceiling = sum(row["hard_ceiling_days"] for row in household_days.values())

        budget_feasible = cost <= budget_cents
        district_meets_minimum = support_ok and total_conservative >= minimum_days
        protected_breaches: list[dict[str, Any]] = []
        for bundle in selected_bundles:
            if bundle.id not in locked_ids:
                continue
            detail = household_days[bundle.home]
            days = detail["conservative_days"]
            if days is None or days < minimum_days:
                protected_breaches.append(
                    {
                        "home": bundle.home,
                        "bundle": bundle.id,
                        "conservative_days": days,
                        "minimum_days": minimum_days,
                    }
                )
        protected_meets_minimum = not protected_breaches
        meets_minimum = district_meets_minimum and protected_meets_minimum
        approvable = (
            budget_feasible
            and resource_feasible
            and quote_valid
            and district_meets_minimum
            and protected_meets_minimum
        )
        return {
            "bundles": selected_ids,
            "optional_bundles": [b.id for b in selected_optional],
            "locked_cost_cents": locked_cost,
            "cost_cents": cost,
            "budget_remaining_cents": budget_cents - cost,
            "budget_feasible": budget_feasible,
            "resource_feasible": resource_feasible,
            "quote_valid": quote_valid,
            "validity_problems": validity_problems,
            "assignments": all_assignments,
            "requirements": [
                {"id": rid, "start": p.start.isoformat(), "end": p.end.isoformat(), "kind": kind}
                for rid, p, kind in requirements
            ],
            "households": household_days,
            "conservative_days": total_conservative if support_ok else None,
            "median_days": total_median if support_ok else None,
            "optimistic_days": total_optimistic if support_ok else None,
            "hard_ceiling_days": total_hard_ceiling,
            "district_meets_minimum": district_meets_minimum,
            "protected_commitments_meet_minimum": protected_meets_minimum,
            "protected_floor_breaches": protected_breaches,
            "meets_minimum": meets_minimum,
            "approvable": approvable,
            "service_delay_days": service_delay_days,
            "unavailable_asset_ids": sorted(unavailable_asset_ids or []),
        }

    for r in range(len(optional) + 1):
        for combo in combinations(optional, r):
            option = evaluate(combo)
            if option is not None:
                result.options.append(option)

    feasible = [o for o in result.options if o["budget_feasible"] and o["resource_feasible"] and o["quote_valid"]]
    result.pareto = _pareto(feasible)
    approvable = [o for o in feasible if o["approvable"]]
    if approvable:
        result.recommended = sorted(
            approvable, key=lambda o: (-int(o["conservative_days"] or 0), o["cost_cents"])
        )[0]
    return result


def _pareto(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier: list[dict[str, Any]] = []
    for option in options:
        dominated = False
        days = option["conservative_days"]
        for other in options:
            other_days = other["conservative_days"]
            if other_days is None or days is None:
                continue
            if (
                other["cost_cents"] <= option["cost_cents"]
                and other_days >= days
                and (other["cost_cents"] < option["cost_cents"] or other_days > days)
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(option)
    return sorted(frontier, key=lambda o: o["cost_cents"])
