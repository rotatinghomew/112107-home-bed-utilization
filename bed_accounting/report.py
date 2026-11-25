"""Human-readable audit and committee report helpers."""

from __future__ import annotations

from datetime import date
from typing import Any

from .evidence import DeviceRequirement, assess_household
from .plans import _hard_start, build_bundles
from .models import Dataset


def yuan(cents: int | None) -> str:
    if cents is None:
        return "凭证不足/未知"
    return f"¥{cents / 100:,.0f}" if cents % 100 == 0 else f"¥{cents / 100:,.2f}"


def audit_current_evidence(dataset: Dataset, as_of: date | None = None) -> list[dict[str, Any]]:
    """Audit every household against all bundles quoting for that home."""

    bundles = build_bundles(dataset)
    results: list[dict[str, Any]] = []
    for household in dataset.households:
        home_bundles = [b for b in bundles if b.home == household.home]
        for bundle in home_bundles:
            requirements = [
                DeviceRequirement(
                    req.requirement_id,
                    [a.asset for a in dataset.assets if a.kind == req.kind],
                )
                for req in bundle.asset_requirements
            ]
            hard_start = _hard_start(bundle, dataset, {}, as_of)
            assessment = assess_household(
                dataset,
                household,
                acceptance_targets=bundle.quote_ids,
                device_requirements=requirements,
                as_of=as_of,
                hard_start=hard_start,
            )
            results.append(
                {
                    "home": household.home,
                    "district": household.district,
                    "bundle": bundle.id,
                    "confirmed_days": assessment.confirmed_days,
                    "possible_upper_bound_days": assessment.possible_upper_bound_days,
                    "confirmed_range": _range(assessment.confirmed_range),
                    "upper_bound_range": _range(assessment.upper_bound_range),
                    "blockers": assessment.blockers(),
                    "used_evidence": assessment.used_evidence,
                    "corrections": assessment.corrections,
                }
            )
    return results


def _range(value) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"start": value.start.isoformat(), "end": value.end.isoformat(), "days": value.days}


def render_options_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# {result['district']}区投放方案测算（截至 {result.get('as_of') or '全部记录'}）",
        "",
        f"- 预算：{yuan(result['budget_cents'])}（{result['budget_cents']:,} 分）",
        f"- 最低使用天数底线：{result['minimum_days']} 天",
        f"- 已锁定方案包：{', '.join(result['locked_bundle_ids']) or '无'}",
        "",
        "## 可行的预算—天数取舍（Pareto 前沿）",
        "",
        "| 方案包 | 报价(分) | 预算余(分) | 保守天数 | 中位天数 | 乐观天数 | 凭证硬上界 | 达底线 | 可审批 |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|:---:|",
    ]
    for o in result["pareto"]:
        lines.append(
            "| {bundles} | {cost} | {remaining} | {cons} | {median} | {opt} | {hard} | {floor} | {appr} |".format(
                bundles="+".join(o["bundles"]),
                cost=o["cost_cents"],
                remaining=o["budget_remaining_cents"],
                cons=_na(o["conservative_days"]),
                median=_na(o["median_days"]),
                opt=_na(o["optimistic_days"]),
                hard=o["hard_ceiling_days"],
                floor="是" if o["meets_minimum"] else "否",
                appr="是" if o["approvable"] else "否",
            )
        )

    rec = result.get("recommended")
    lines.extend(["", "## 建议方案", ""])
    if not rec:
        lines.append("没有同时满足预算、资产无冲突、报价有效和保守天数底线的可审批方案。")
    else:
        lines.append(
            f"建议选择 `{'+'.join(rec['bundles'])}`：报价 {yuan(rec['cost_cents'])}，"
            f"预算余 {yuan(rec['budget_remaining_cents'])}，保守/中位/乐观天数为 "
            f"{rec['conservative_days']}/{rec['median_days']}/{rec['optimistic_days']}。"
        )
    lines.extend(["", "## 分户依据", ""])
    for o in result["options"]:
        if rec and o["bundles"] != rec["bundles"]:
            continue
        for home, detail in o["households"].items():
            f = detail["forecast"]
            lines.append(
                f"- **{home} / {detail['bundle']}**：资产分配 `{detail['assigned_assets']}`；"
                f"可比样本 {f['sample_size']}（启用 {f['activation_samples']}、"
                f"已确定未启用 {f['confirmed_inactive']}、等待/删失 {f['waiting']}）；"
                f"保守 {detail['conservative_days']} 天、中位 {detail['median_days']} 天、"
                f"乐观 {detail['optimistic_days']} 天，硬上界 {detail['hard_ceiling_days']} 天。"
            )
    return "\n".join(lines) + "\n"


def render_settlement_markdown(s: dict[str, Any]) -> str:
    lines = [
        f"# 方案 {s['plan_id']} 履约回看（{s['settled_as_of']}）",
        "",
        f"地区：{s['district']}；底线：{s['minimum_days']} 天。",
        f"已确认承诺天数：**{s['totals']['committed_confirmed_days']}**；"
        f"缺凭证情形的可能上界：{s['totals']['committed_possible_upper_bound_days']} 天。",
        f"底线是否由已确认天数达到：{'是' if s['floor_met_by_confirmed_days'] else '否'}。",
        "",
        "## 分户核对",
        "",
        "| 户 | 方案包 | 已确认天数 | 可能上界 | 原中位预测 | 差异 | 阻塞项 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for h in s["households"]:
        lines.append(
            "| {home} | {bundle} | {confirmed} | {upper} | {orig} | {variance} | {blockers} |".format(
                home=h["home"],
                bundle=h["bundle"],
                confirmed=h["committed_confirmed_days"],
                upper=h["committed_possible_upper_bound_days"],
                orig=h["original_forecast"].get("median_days"),
                variance=h["variance_committed_vs_original_median"],
                blockers="；".join(h["blockers"]) or "无",
            )
        )
    fa = s["financial_action"]
    lines.extend(
        [
            "",
            "## 余款与备选",
            "",
            f"- 签发承诺额：{yuan(fa['committed_cost_cents'])}；签发后预算余：{yuan(fa['budget_remaining_after_issue_cents'])}。",
            f"- 已支付金额：{yuan(fa['paid_cents'])}；未付余额：{yuan(fa['unpaid_balance_cents'])}。",
            f"- 处理建议：{fa['recommendation']}。",
            f"- 备选转入：{s['waitlist_review']['conclusion']}。",
            "",
            "## 核算口径",
            "",
            s["accounting_rule"],
        ]
    )
    return "\n".join(lines) + "\n"


def _na(value: Any) -> str:
    return "支撑不足" if value is None else str(value)
