"""Settlement and performance review against an issued plan snapshot."""

from __future__ import annotations

from datetime import date
from typing import Any

from .assets import Occupancy, earliest_available
from .dates import Interval
from .evidence import DeviceRequirement, assess_household
from .plans import _hard_start, build_bundles, enumerate_plan_options
from .models import Dataset


def settle_plan(
    dataset: Dataset,
    plan: dict[str, Any],
    *,
    as_of: date,
    paid_cents: int | None = None,
    base_occupancies: list[Occupancy] | None = None,
    other_new_commitment_cents: int = 0,
    exclude_failed_bundles_from_waitlist: bool = True,
) -> dict[str, Any]:
    assignment_snapshot = plan.get("option_snapshot", {}).get("assignments", {})
    original_households = plan.get("original_forecast", {}).get("households", {})
    household_reviews: list[dict[str, Any]] = []
    total_confirmed = 0
    total_upper_bound = 0
    total_committed_confirmed = 0
    total_committed_upper = 0
    exceptions: list[str] = []

    bundles_by_id = {b.id: b for b in build_bundles(dataset)}
    assets_by_id = {a.asset: a for a in dataset.assets}
    selected_bundles = [bundles_by_id[bundle_id] for bundle_id in plan["selected_bundles"]]
    homes = sorted({bundle.home for bundle in selected_bundles})

    for home in homes:
        home_bundles = [b for b in selected_bundles if b.home == home]
        household = dataset.household(home)
        acceptance_targets: list[str] = []
        device_requirements: list[DeviceRequirement] = []
        assigned_asset_ids: list[str] = []
        hard_starts: list[date] = []
        for bundle in home_bundles:
            acceptance_targets.extend(bundle.quote_ids)
            hard_starts.append(_hard_start(bundle, dataset, assignment_snapshot, as_of))
            for req in bundle.asset_requirements:
                asset_id = assignment_snapshot.get(req.requirement_id)
                if asset_id:
                    assigned_asset_ids.append(asset_id)
                device_requirements.append(
                    DeviceRequirement(
                        req.requirement_id,
                        [asset_id] if asset_id else [],
                    )
                )

        asset_reviews: list[dict[str, Any]] = []
        return_end_caps: list[date] = []
        slots_by_asset = {s["asset"]: s for s in plan.get("asset_slots", [])}
        for asset_id in sorted(set(assigned_asset_ids)):
            asset = assets_by_id.get(asset_id)
            if asset is None:
                continue
            slot = slots_by_asset.get(asset_id)
            slot_start = date.fromisoformat(slot["start"]) if slot else None
            available_after_cooldown = earliest_available(asset)
            returned_during_commitment = bool(
                asset.received_back
                and slot_start
                and asset.received_back >= slot_start
                and (not slot or not slot.get("released"))
            )
            if returned_during_commitment and asset.received_back:
                # On the warehouse receipt date the device is no longer in the
                # household; the next household additionally waits cooldown.
                return_end_caps.append(asset.received_back)
            asset_reviews.append(
                {
                    "asset": asset_id,
                    "kind": asset.kind,
                    "received_back": asset.received_back.isoformat() if asset.received_back else None,
                    "available_for_new_household": (
                        available_after_cooldown.isoformat() if available_after_cooldown else None
                    ),
                    "returned_during_commitment": returned_during_commitment,
                    "slot": slot,
                }
            )

        # Separate bid sections for one household are joint conditions.  They
        # must never be settled as additive day streams.
        hard_start = max(hard_starts) if hard_starts else None
        end_cap = min(return_end_caps) if return_end_caps else None
        assessment = assess_household(
            dataset,
            household,
            acceptance_targets=acceptance_targets,
            device_requirements=device_requirements,
            as_of=as_of,
            hard_start=hard_start,
            end_cap=end_cap,
        )

        slot_intervals = [
            Interval(date.fromisoformat(s["start"]), date.fromisoformat(s["end"]))
            for s in plan.get("asset_slots", [])
            if s.get("home") == household.home and not s.get("released")
        ]
        commitment = None
        if slot_intervals:
            commitment = Interval(
                max(i.start for i in slot_intervals),
                min(i.end for i in slot_intervals),
            )

        confirmed_committed = assessment.committed_days(commitment)
        upper_committed = assessment.committed_upper_bound_days(commitment)
        total_confirmed += assessment.confirmed_days
        total_upper_bound += assessment.possible_upper_bound_days
        total_committed_confirmed += confirmed_committed
        total_committed_upper += upper_committed

        blockers = assessment.blockers()
        event_flags = {
            "acceptance_scope_or_validity_change": any(
                "wrong_scope" in item or "accepted" in item for item in assessment.corrections
            ),
            "first_visit_condition_absent_or_failed": assessment.missing_first_visit
            or not assessment.window_ok,
            "asset_returned_during_commitment": any(
                item["returned_during_commitment"] for item in asset_reviews
            ),
        }
        status = "confirmed" if assessment.is_confirmed else "evidence_gap_or_correction"
        if blockers:
            exceptions.extend(f"{household.home}: {message}" for message in blockers)

        original = original_households.get(household.home, {})
        bundle_ids = [b.id for b in home_bundles]
        household_reviews.append(
            {
                "home": household.home,
                "bundles": bundle_ids,
                # Compatibility for single-bundle reports/tests.
                "bundle": "+".join(bundle_ids),
                "status": status,
                "confirmed_days_in_approval_period": assessment.confirmed_days,
                "possible_upper_bound_days_in_approval_period": assessment.possible_upper_bound_days,
                "committed_confirmed_days": confirmed_committed,
                "committed_possible_upper_bound_days": upper_committed,
                "commitment_interval": (
                    {"start": commitment.start.isoformat(), "end": commitment.end.isoformat()}
                    if commitment
                    else None
                ),
                "confirmed_range": _range(assessment.confirmed_range),
                "upper_bound_range": _range(assessment.upper_bound_range),
                "used_evidence": assessment.used_evidence,
                "corrections": assessment.corrections,
                "event_flags": event_flags,
                "asset_reviews": asset_reviews,
                "blockers": blockers,
                "original_forecast": {
                    "conservative_days": original.get("conservative_days"),
                    "median_days": original.get("median_days"),
                    "optimistic_days": original.get("optimistic_days"),
                    "hard_ceiling_days": original.get("hard_ceiling_days"),
                },
                "variance_committed_vs_original_median": (
                    confirmed_committed - original["median_days"]
                    if isinstance(original.get("median_days"), int)
                    else None
                ),
            }
        )

    minimum_days = int(plan["minimum_days"])
    floor_met = total_committed_confirmed >= minimum_days

    contract_cost = int(plan["cost_cents"])
    budget = int(plan["budget_cents"])
    paid = paid_cents
    unpaid = None if paid is None else max(0, contract_cost - paid)

    financial_action = {
        "committed_cost_cents": contract_cost,
        "budget_cents": budget,
        "budget_remaining_after_issue_cents": int(plan.get("budget_remaining_cents", budget - contract_cost)),
        "paid_cents": paid,
        "unpaid_balance_cents": unpaid,
        "cash_recovery_determinable": paid is not None,
        "recommendation": (
            "履约证据完整；按合同与支付节点结算"
            if floor_met and not exceptions
            else "冻结尚未支付的合同余款并发起凭证补正/验收复核；已付金额能否追回需支付与合同条款，不能仅凭核算表认定"
        ),
        "maximum_new_budget_if_full_commitment_decommitted_cents": contract_cost,
    }

    waitlist = _waitlist_review(
        dataset,
        plan,
        as_of,
        exclude_failed_bundles_from_waitlist,
        base_occupancies,
        other_new_commitment_cents,
    )

    return {
        "plan_id": plan["plan_id"],
        "settled_as_of": as_of.isoformat(),
        "district": plan["district"],
        "minimum_days": minimum_days,
        "totals": {
            "confirmed_days": total_confirmed,
            "possible_upper_bound_days": total_upper_bound,
            "committed_confirmed_days": total_committed_confirmed,
            "committed_possible_upper_bound_days": total_committed_upper,
        },
        "floor_met_by_confirmed_days": floor_met,
        "exceptions": exceptions,
        "households": household_reviews,
        "financial_action": financial_action,
        "waitlist_review": waitlist,
        "accounting_rule": (
            "原预测保留在 plan.original_forecast；冲正凭证不追溯计为启用，"
            "空窗不计入 confirmed_days，只进入 possible_upper_bound。"
        ),
    }


def _range(value: Interval | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"start": value.start.isoformat(), "end": value.end.isoformat(), "days": value.days}


def _waitlist_review(
    dataset: Dataset,
    plan: dict[str, Any],
    as_of: date,
    exclude_failed: bool,
    base_occupancies: list[Occupancy] | None = None,
    other_new_commitment_cents: int = 0,
) -> dict[str, Any]:
    selected = set(plan["selected_bundles"])
    excluded = selected if exclude_failed else set()
    district = plan["district"]

    # At settlement date, quote validity and current asset/window records are
    # rechecked.  Do not pass the old plan's assets as available; source signed
    # decisions still protect DEC1.
    options = enumerate_plan_options(
        dataset,
        district=district,
        budget_cents=max(0, int(plan["budget_cents"]) - other_new_commitment_cents),
        minimum_days=0,
        as_of=as_of,
        base_occupancies=base_occupancies,
        excluded_bundle_ids=excluded,
    )
    selected = set(plan["selected_bundles"])
    alternatives = []
    for option in options.options:
        new_bundles = [b for b in option["bundles"] if b not in selected]
        if not new_bundles:
            continue
        alternatives.append(
            {
                "new_bundles": new_bundles,
                "bundles": option["bundles"],
                "cost_cents": option["cost_cents"],
                "conservative_days": option["conservative_days"],
                "budget_feasible": option["budget_feasible"],
                "resource_feasible": option["resource_feasible"],
                "quote_valid": option["quote_valid"],
                "validity_problems": option["validity_problems"],
            }
        )
    feasible_transfers = [
        a
        for a in alternatives
        if a["budget_feasible"] and a["resource_feasible"] and a["quote_valid"]
    ]
    return {
        "candidate_count": len(alternatives),
        "feasible_transfer_count": len(feasible_transfers),
        "feasible_transfers": feasible_transfers,
        "all_candidates": alternatives,
        "conclusion": (
            "有可按当前预算、资产和有效报价转入的备选"
            if feasible_transfers
            else "没有可直接转入的备选：可能缺少同区候选、报价已失效、资产冲突或预算不足"
        ),
    }
