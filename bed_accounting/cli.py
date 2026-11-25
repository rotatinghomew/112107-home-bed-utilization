from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from .commitments import DEFAULT_STORE, CommitmentStore, make_plan_record
from .io import DEFAULT_DATA_DIR, load_dataset
from .report import audit_current_evidence, render_options_markdown, render_settlement_markdown
from .settlement import settle_plan
from .plans import enumerate_plan_options


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _print_json(value: Any, output: str | Path | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bed-accounting", description="家庭养老床位投放核算与履约回看")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    sub = parser.add_subparsers(dest="command", required=True)

    opt = sub.add_parser("options", help="枚举预算、资产和天数约束下的方案")
    opt.add_argument("--district", required=True)
    opt.add_argument("--season")
    opt.add_argument("--budget-cents", type=int)
    opt.add_argument("--minimum-days", type=int)
    opt.add_argument("--as-of", type=_date)
    opt.add_argument("--delay-days", type=int, default=0)
    opt.add_argument("--remove-asset", action="append", default=[])
    opt.add_argument("--store", default=str(DEFAULT_STORE))
    opt.add_argument("--format", choices=["json", "markdown"], default="markdown")
    opt.add_argument("--output")

    audit = sub.add_parser("audit", help="按当前凭证核对实际启用天数")
    audit.add_argument("--as-of", type=_date)
    audit.add_argument("--format", choices=["json", "markdown"], default="json")

    issue = sub.add_parser("issue", help="签发并持久化一套方案")
    issue.add_argument("plan_id")
    issue.add_argument("--district", required=True)
    issue.add_argument("--season")
    issue.add_argument("--issue-date", type=_date, required=True)
    issue.add_argument("--budget-cents", type=int)
    issue.add_argument("--minimum-days", type=int)
    issue.add_argument("--select", default="recommended", help="recommended、minimal 或逗号分隔的可选方案包")
    issue.add_argument("--store", default=str(DEFAULT_STORE))
    issue.add_argument("--overwrite", action="store_true")
    issue.add_argument("--force", action="store_true", help="即使未达保守底线也签发（不推荐）")

    settle = sub.add_parser("settle", help="对照签发预测做结算复盘")
    settle.add_argument("plan_id")
    settle.add_argument("--as-of", type=_date, required=True)
    settle.add_argument("--paid-cents", type=int)
    settle.add_argument("--store", default=str(DEFAULT_STORE))
    settle.add_argument("--format", choices=["json", "markdown"], default="markdown")
    settle.add_argument("--output")

    analogues = sub.add_parser("analogues", help="输出可比历史家庭分类")
    analogues.add_argument("--district", required=True)
    analogues.add_argument("--home-kind", required=True)
    analogues.add_argument("--access", required=True)
    analogues.add_argument("--min-analogues", type=int, default=6)
    analogues.add_argument("--as-of", type=_date)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset = load_dataset(args.data_dir)

    if args.command == "audit":
        result = audit_current_evidence(dataset, args.as_of)
        if args.format == "json":
            _print_json(result)
        else:
            for row in result:
                print(f"{row['home']} {row['bundle']}: confirmed={row['confirmed_days']}, upper={row['possible_upper_bound_days']}")
                for problem in row["blockers"]:
                    print(f"  - {problem}")
        return 0

    if args.command == "options":
        store = CommitmentStore(args.store)
        base_budget = args.budget_cents
        if base_budget is None:
            base_budget = dataset.budget(args.district, args.season).budget
        prior_commitments = store.new_commitment_cost(district=args.district)
        result = enumerate_plan_options(
            dataset,
            district=args.district,
            budget_cents=base_budget - prior_commitments,
            minimum_days=args.minimum_days,
            season=args.season,
            as_of=args.as_of,
            base_occupancies=store.occupancies(),
            unavailable_asset_ids=set(args.remove_asset),
            service_delay_days=args.delay_days,
        ).as_dict()
        result["base_budget_cents"] = base_budget
        result["prior_new_commitment_cents"] = prior_commitments
        if args.format == "json":
            _print_json(result, args.output)
        else:
            md = render_options_markdown(result)
            if args.output:
                Path(args.output).write_text(md, encoding="utf-8")
            print(md)
        return 0

    if args.command == "issue":
        store = CommitmentStore(args.store)
        occupancies = store.occupancies()
        exclude_plan = args.plan_id if args.overwrite and store.has(args.plan_id) else None
        if exclude_plan:
            occupancies = [o for o in occupancies if o.source != args.plan_id]
        base_budget = args.budget_cents
        if base_budget is None:
            base_budget = dataset.budget(args.district, args.season).budget
        prior_commitments = store.new_commitment_cost(
            district=args.district,
            exclude_plan_id=exclude_plan,
        )
        result = enumerate_plan_options(
            dataset,
            district=args.district,
            budget_cents=base_budget - prior_commitments,
            minimum_days=args.minimum_days,
            season=args.season,
            as_of=args.issue_date,
            base_occupancies=occupancies,
        )
        option = _choose_option(result, args.select)
        if option is None:
            raise SystemExit(f"no option matches --select {args.select}")
        if not option["approvable"] and not args.force:
            raise SystemExit("匹配方案不可审批（预算/资产/报价有效期/天数底线）；如仍要留痕请加 --force")
        budget = base_budget
        minimum = result.minimum_days
        record = make_plan_record(
            plan_id=args.plan_id,
            district=args.district,
            issue_date=args.issue_date,
            budget_cents=budget,
            minimum_days=minimum,
            option=option,
            dataset=dataset,
            season=args.season or (result.budget_row.season if result.budget_row else None),
        )
        store.issue(record, overwrite=args.overwrite)
        _print_json({"issued": args.plan_id, "path": str(Path(args.store)), "bundles": option["bundles"]})
        return 0

    if args.command == "settle":
        store = CommitmentStore(args.store)
        plan = store.get(args.plan_id)
        other_occupancies = [o for o in store.occupancies() if o.source != args.plan_id]
        other_commitment_cost = store.new_commitment_cost(
            district=plan.get("district"),
            exclude_plan_id=args.plan_id,
        )
        result = settle_plan(
            dataset,
            plan,
            as_of=args.as_of,
            paid_cents=args.paid_cents,
            base_occupancies=other_occupancies,
            other_new_commitment_cents=other_commitment_cost,
        )
        default_json = Path(args.store).parent / "settlements" / f"{args.plan_id}-{args.as_of}.json"
        if args.format == "json":
            _print_json(result, args.output or default_json)
        else:
            json_path = Path(args.output).with_suffix(".json") if args.output else default_json
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            md = render_settlement_markdown(result)
            md_path = Path(args.output) if args.output else default_json.with_suffix(".md")
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md, encoding="utf-8")
            print(md)
            print(f"[written] {json_path}")
            print(f"[written] {md_path}")
        return 0

    if args.command == "analogues":
        from .analogues import comparable_cases, summarise, classify_cases

        class _H:
            district = args.district
            kind = args.home_kind
            access = args.access

        summary = summarise(dataset.analogues, _H(), args.min_analogues, args.as_of)  # type: ignore[arg-type]
        cases = comparable_cases(dataset.analogues, _H(), args.as_of)  # type: ignore[arg-type]
        activated, inactive, waiting = classify_cases(cases)
        _print_json(
            {
                "summary": summary.__dict__,
                "activated": [c.home for c in activated],
                "confirmed_inactive": [c.home for c in inactive],
                "waiting": [c.home for c in waiting],
            }
        )
        return 0

    return 1


def _choose_option(result, selector: str) -> dict[str, Any] | None:
    if selector == "recommended":
        return result.recommended
    if selector == "minimal":
        # Locked packages only is the lowest-cost Pareto baseline.
        target_optional: tuple[str, ...] = ()
    else:
        target_optional = tuple(sorted(x.strip() for x in selector.split(",") if x.strip()))
    for option in result.options:
        if tuple(sorted(option["optional_bundles"])) == tuple(sorted(target_optional)):
            return option
    return None


if __name__ == "__main__":
    raise SystemExit(main())
