import tempfile
import unittest
from datetime import date
from pathlib import Path

from bed_accounting.analogues import summarise
from bed_accounting.assets import Occupancy, earliest_available
from bed_accounting.commitments import CommitmentStore, make_plan_record
from bed_accounting.dates import Interval
from bed_accounting.evidence import DeviceRequirement, assess_household
from bed_accounting.io import load_dataset
from bed_accounting.plans import build_bundles, enumerate_plan_options, price_quote_selection
from bed_accounting.settlement import settle_plan

ROOT = Path(__file__).resolve().parents[1]


class AccountingRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "data")

    def test_half_open_intervals(self):
        interval = Interval(date(2026, 6, 30), date(2026, 9, 1))
        self.assertEqual(interval.days, 63)
        self.assertTrue(interval.contains(date(2026, 8, 31)))
        self.assertFalse(interval.contains(date(2026, 9, 1)))

    def test_comparable_analogues_separate_waiting_from_failure(self):
        household = self.dataset.household("H2")
        summary = summarise(self.dataset.analogues, household, 6, date(2026, 5, 20))
        self.assertTrue(summary.supported)
        self.assertEqual(summary.sample_size, 18)
        self.assertEqual(summary.activation_samples, 13)
        self.assertEqual(summary.confirmed_inactive, 2)
        self.assertEqual(summary.waiting, 3)
        self.assertEqual(summary.optimistic_offset, 23)
        self.assertEqual(summary.median_offset, 29)
        self.assertEqual(summary.conservative_offset, 37)

    def test_point_in_time_estimate_does_not_use_future_records(self):
        household = self.dataset.household("H2")
        early = summarise(self.dataset.analogues, household, 6, date(2025, 5, 26))
        self.assertEqual(early.sample_size, 1)
        self.assertEqual(early.waiting, 1)
        self.assertFalse(early.supported)

    def test_evidence_correction_is_temporal(self):
        household = self.dataset.household("H2")
        targets = ["Q2", "Q3"]
        devices = [DeviceRequirement("transfer", ["X1"])]

        before_correction = assess_household(
            self.dataset,
            household,
            acceptance_targets=targets,
            device_requirements=devices,
            as_of=date(2026, 7, 1),
            hard_start=date(2026, 6, 12),
        )
        self.assertEqual(before_correction.confirmed_days, 63)

        after_correction = assess_household(
            self.dataset,
            household,
            acceptance_targets=targets,
            device_requirements=devices,
            as_of=date(2026, 7, 2),
            hard_start=date(2026, 6, 12),
        )
        self.assertEqual(after_correction.confirmed_days, 0)
        self.assertEqual(after_correction.possible_upper_bound_days, 63)
        self.assertTrue(any("Q3" in x for x in after_correction.missing_acceptances))

    def test_returned_asset_needs_receipt_plus_cooldown(self):
        x1 = next(a for a in self.dataset.assets if a.asset == "X1")
        x2 = next(a for a in self.dataset.assets if a.asset == "X2")
        self.assertEqual(earliest_available(x1), date(2026, 6, 12))
        self.assertIsNone(earliest_available(x2))

    def test_bundle_joint_price_is_required_for_budget(self):
        bundles = {b.id: b for b in build_bundles(self.dataset)}
        b2 = bundles["B2"]
        self.assertEqual(b2.line_total, 830_000)
        self.assertEqual(b2.bundle_price, 710_000)
        self.assertEqual(b2.whole_bundle_saving, 120_000)
        # Signed H3 costs 530k.  At line prices B2 would make 1.36m > 1.3m.
        self.assertGreater(530_000 + b2.line_total, 1_300_000)
        self.assertLessEqual(530_000 + b2.bundle_price, 1_300_000)
        self.assertEqual(price_quote_selection(self.dataset, ["Q2"])["total_cents"], 410_000)
        self.assertEqual(price_quote_selection(self.dataset, ["Q2", "Q3"])["total_cents"], 710_000)

    def test_west_recommends_locked_h3_plus_b2(self):
        result = enumerate_plan_options(
            self.dataset,
            district="西",
            as_of=date(2026, 5, 20),
        )
        self.assertEqual(result.locked_bundle_ids, ["B3"])
        self.assertIsNotNone(result.recommended)
        self.assertEqual(result.recommended["bundles"], ["B3", "B2"])
        self.assertEqual(result.recommended["cost_cents"], 1_240_000)
        self.assertEqual(result.recommended["conservative_days"], 96)

    def test_one_fewer_transfer_device_only_hits_optional_h2(self):
        result = enumerate_plan_options(
            self.dataset,
            district="西",
            as_of=date(2026, 5, 20),
            unavailable_asset_ids={"X1"},
        )
        self.assertEqual(result.recommended["bundles"], ["B3"])
        self.assertEqual(result.recommended["conservative_days"], 41)
        b2_options = [o for o in result.options if "B2" in o["bundles"]]
        self.assertTrue(all(not o["resource_feasible"] for o in b2_options))

    def test_one_week_delay_puts_protected_h3_below_floor(self):
        result = enumerate_plan_options(
            self.dataset,
            district="西",
            as_of=date(2026, 5, 20),
            service_delay_days=7,
        )
        h3_only = next(o for o in result.options if o["bundles"] == ["B3"])
        self.assertEqual(h3_only["conservative_days"], 34)
        self.assertFalse(h3_only["protected_commitments_meet_minimum"])
        self.assertEqual(h3_only["protected_floor_breaches"][0]["home"], "H3")

        combined = next(o for o in result.options if set(o["bundles"]) == {"B3", "B2"})
        self.assertEqual(combined["conservative_days"], 82)
        self.assertTrue(combined["district_meets_minimum"])
        self.assertFalse(combined["approvable"])
        self.assertIsNone(result.recommended)

    def test_issue_preserves_forecast_and_settlement_does_not_count_gap(self):
        planning = enumerate_plan_options(
            self.dataset, district="西", as_of=date(2026, 5, 20)
        )
        option = planning.recommended
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "commitments.json"
            store = CommitmentStore(store_path)
            record = make_plan_record(
                plan_id="T1",
                district="西",
                issue_date=date(2026, 5, 20),
                budget_cents=1_300_000,
                minimum_days=38,
                option=option,
                dataset=self.dataset,
            )
            store.issue(record)
            self.assertTrue(store_path.exists())

            reloaded = CommitmentStore(store_path).get("T1")
            settlement = settle_plan(
                self.dataset,
                reloaded,
                as_of=date(2026, 11, 25),
                paid_cents=600_000,
            )
            self.assertEqual(settlement["totals"]["committed_confirmed_days"], 0)
            self.assertEqual(settlement["totals"]["committed_possible_upper_bound_days"], 130)
            h2 = next(h for h in settlement["households"] if h["home"] == "H2")
            self.assertEqual(h2["original_forecast"]["median_days"], 63)
            self.assertEqual(h2["possible_upper_bound_days_in_approval_period"], 63)
            self.assertEqual(settlement["financial_action"]["unpaid_balance_cents"], 640_000)
            self.assertEqual(settlement["waitlist_review"]["feasible_transfer_count"], 0)

    def test_returned_device_caps_enabled_interval(self):
        household = self.dataset.household("H2")
        assessment = assess_household(
            self.dataset,
            household,
            acceptance_targets=["Q2", "Q3"],
            device_requirements=[DeviceRequirement("transfer", ["X1"])],
            as_of=date(2026, 7, 1),
            hard_start=date(2026, 6, 12),
            end_cap=date(2026, 8, 1),
        )
        self.assertEqual(assessment.confirmed_days, 32)
        self.assertEqual(assessment.confirmed_range.end, date(2026, 8, 1))

    def test_asset_occupancy_blocks_same_asset_overlap(self):
        from bed_accounting.assets import find_assignments

        period = Interval(date(2026, 6, 12), date(2026, 9, 1))
        occupancy = Occupancy("X1", "other", period, "test")
        assignment = find_assignments(
            [("r1", period, "transfer")],
            [a for a in self.dataset.assets if a.kind == "transfer"],
            [occupancy],
        )
        # X1 is blocked; X2 has no observed return/new-household availability.
        self.assertIsNone(assignment)


if __name__ == "__main__":
    unittest.main()
