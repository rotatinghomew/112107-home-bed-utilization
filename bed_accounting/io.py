"""CSV loading.  The project uses only the Python standard library."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .dates import Interval, parse_date
from .models import (
    AnalogueCase,
    Asset,
    Budget,
    Dataset,
    Decision,
    Evidence,
    Household,
    QuoteLine,
    VisitWindow,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"


def _int(value: str | None, default: int | None = None) -> int | None:
    value = (value or "").strip()
    if not value:
        return default
    return int(value)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, strict=True))


def load_dataset(data_dir: str | Path = DEFAULT_DATA_DIR) -> Dataset:
    data_dir = Path(data_dir)

    households: list[Household] = []
    for row in _read_csv(data_dir / "households.csv"):
        households.append(
            Household(
                home=row["home"],
                district=row["district"],
                kind=row["kind"],
                access=row["access"],
                period=_require_interval(row["from"], row["until"], "household"),
                assessed_at=parse_date(row["assessed_at"]),
            )
        )

    quotes: list[QuoteLine] = []
    for row in _read_csv(data_dir / "quotes.csv"):
        quotes.append(
            QuoteLine(
                quote=row["quote"],
                bundle=row["bundle"],
                home=row["home"],
                work=row.get("work", ""),
                asset_kind=row.get("asset_kind", ""),
                line_price=int(row["line_price"]),
                bundle_price=int(row["bundle_price"]),
                work_period=_require_interval(row["start"], row["end"], "quote"),
                valid_until=parse_date(row["valid_until"]),
            )
        )

    assets: list[Asset] = []
    for row in _read_csv(data_dir / "assets.csv"):
        assets.append(
            Asset(
                asset=row["asset"],
                kind=row["kind"],
                held_for=row.get("held_for", ""),
                reserved_until=parse_date(row["reserved_until"]),
                received_back=parse_date(row["received_back"]),
                cooldown_days=int(row["cooldown_days"] or 0),
                recorded_at=parse_date(row["recorded_at"]),
            )
        )

    evidence: list[Evidence] = []
    for row in _read_csv(data_dir / "delivery.csv"):
        evidence.append(
            Evidence(
                evidence=row["evidence"],
                home=row["home"],
                kind=row["kind"],
                related=row["related"],
                effective=parse_date(row["effective"]),
                recorded_at=parse_date(row["recorded_at"]),
                replaces=row.get("replaces", ""),
            )
        )

    decisions: list[Decision] = []
    for row in _read_csv(data_dir / "decisions.csv"):
        decisions.append(
            Decision(
                document=row["document"],
                home=row["home"],
                quote=row["quote"],
                asset=row["asset"],
                status=row["status"],
                period=_require_interval(row["from"], row["until"], "decision"),
                amount=int(row["amount"]),
            )
        )

    budgets: list[Budget] = []
    for row in _read_csv(data_dir / "budgets.csv"):
        budgets.append(
            Budget(
                season=row["season"],
                district=row["district"],
                budget=int(row["budget"]),
                minimum_days=int(row["minimum_days"]),
                min_analogues=int(row["min_analogues"]),
            )
        )

    windows: list[VisitWindow] = []
    for row in _read_csv(data_dir / "visit_windows.csv"):
        start = parse_date(row["open_from"])
        end = parse_date(row["open_until"])
        assert start is not None and end is not None
        windows.append(
            VisitWindow(
                team=row["team"],
                district=row["district"],
                open_period=Interval(start, end),
                recorded_at=parse_date(row["recorded_at"]),
                revision=int(row["revision"]),
            )
        )

    analogues: list[AnalogueCase] = []
    for row in _read_csv(data_dir / "analogue_cases.csv"):
        analogues.append(
            AnalogueCase(
                home=row["home"],
                district=row["district"],
                kind=row["kind"],
                access=row["access"],
                approved_period=_require_interval(row["approved_from"], row["approved_until"], "analogue"),
                verified=parse_date(row["verified"]),
                device_ready=parse_date(row["device_ready"]),
                first_visit=parse_date(row["first_visit"]),
                observation_end=parse_date(row["observation_end"]),
                recorded_at=parse_date(row["recorded_at"]),
            )
        )

    return Dataset(
        households=households,
        quotes=quotes,
        assets=assets,
        evidence=evidence,
        decisions=decisions,
        budgets=budgets,
        visit_windows=windows,
        analogues=analogues,
    )


def _require_interval(start: str, end: str, what: str):
    from .dates import Interval

    s = parse_date(start)
    e = parse_date(end)
    if s is None or e is None:
        raise ValueError(f"{what} is missing an interval endpoint")
    return Interval(s, e)


def load_contract(path: str | Path = ROOT / "data-contract.json") -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
