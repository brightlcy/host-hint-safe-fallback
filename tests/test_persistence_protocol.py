import tempfile
import unittest
from pathlib import Path

from simulator.persistence_protocol import (
    DEVELOPMENT_SEEDS,
    FINAL_HELD_OUT_SEEDS,
    FrozenV2Selection,
    PersistenceCandidate,
    classify_static_device_delta,
    core_measurement_error_rates,
    development_profile,
    final_profile,
    load_frozen_v2_selection,
    save_frozen_v2_selection,
    seed_cluster_summaries,
    select_persistence_candidate,
    timely_detection,
    v2_condition_outcome,
)


class PersistenceProtocolTest(unittest.TestCase):
    def outcome(
        self,
        *,
        seed: int,
        candidate: PersistenceCandidate,
        static_waf: float = 1.2,
        device_waf: float = 1.0,
        v2_waf: float = 1.1,
        triggered: bool = True,
        trigger_write: int | None = 10,
        total_host_writes: int = 20,
    ):
        return v2_condition_outcome(
            workload_seed=seed,
            error_rate=0.4,
            candidate=candidate,
            static_waf=static_waf,
            device_waf=device_waf,
            v2_waf=v2_waf,
            fallback_triggered=triggered,
            trigger_host_write_index=trigger_write,
            total_host_writes=total_host_writes,
        )

    def test_exact_pairwise_label_and_timely_boundary(self) -> None:
        self.assertEqual(classify_static_device_delta(1.1, 1.0), "harmful")
        self.assertEqual(classify_static_device_delta(0.9, 1.0), "beneficial")
        self.assertEqual(classify_static_device_delta(1.0, 1.0), "neutral")
        self.assertTrue(timely_detection(25_000, 50_000))
        self.assertFalse(timely_detection(25_001, 50_000))

    def test_never_trigger_candidate_is_not_eligible(self) -> None:
        candidate = PersistenceCandidate(128, 2)
        rows = [
            self.outcome(
                seed=seed,
                candidate=candidate,
                v2_waf=1.2,
                triggered=False,
                trigger_write=None,
            )
            for seed in DEVELOPMENT_SEEDS
        ]
        result = select_persistence_candidate(rows, candidates=(candidate,))
        self.assertEqual(result["selection_status"], "NO_GO")
        self.assertIsNone(result["selected_candidate"])

    def test_selection_uses_lexicographic_rules_and_tie_breakers(self) -> None:
        first = PersistenceCandidate(128, 2)
        second = PersistenceCandidate(256, 2)
        rows = []
        for seed in DEVELOPMENT_SEEDS:
            # Both candidates pass the harmful eligibility gate identically.
            rows.append(self.outcome(seed=seed, candidate=first))
            rows.append(self.outcome(seed=seed, candidate=second))
            # Both have beneficial evidence; first has a zero false-fallback
            # rate rather than looking safe merely due to missing evidence.
            rows.append(
                self.outcome(
                    seed=seed,
                    candidate=first,
                    static_waf=0.9,
                    device_waf=1.0,
                    v2_waf=0.9,
                    triggered=False,
                    trigger_write=None,
                )
            )
            # A beneficial false fallback disqualifies second at priority 1.
            rows.append(
                self.outcome(
                    seed=seed,
                    candidate=second,
                    static_waf=0.9,
                    device_waf=1.0,
                    v2_waf=0.9,
                    triggered=True,
                    trigger_write=10,
                )
            )
        result = select_persistence_candidate(rows, candidates=(first, second))
        self.assertEqual(result["selection_status"], "SELECTED")
        self.assertEqual(
            result["selected_candidate"], {"block_size": 128, "persistence_k": 2}
        )

        # With all outcome metrics equal, product B*K, then B, then K decide.
        tied = PersistenceCandidate(128, 3)
        tie_rows = [
            self.outcome(seed=seed, candidate=candidate)
            for candidate in (first, tied)
            for seed in DEVELOPMENT_SEEDS
        ]
        tie_result = select_persistence_candidate(
            tie_rows, candidates=(first, tied)
        )
        self.assertEqual(
            tie_result["selected_candidate"], {"block_size": 128, "persistence_k": 2}
        )

    def test_beneficial_false_fallback_rates_are_equal_weighted_by_seed(self) -> None:
        candidate = PersistenceCandidate(128, 2)
        rows = [self.outcome(seed=seed, candidate=candidate) for seed in DEVELOPMENT_SEEDS]
        rows.extend(
            self.outcome(
                seed=201,
                candidate=candidate,
                static_waf=0.9,
                device_waf=1.0,
                v2_waf=0.9,
                triggered=index < 2,
                trigger_write=10 if index < 2 else None,
            )
            for index in range(8)
        )
        rows.extend(
            self.outcome(
                seed=202,
                candidate=candidate,
                static_waf=0.9,
                device_waf=1.0,
                v2_waf=0.9,
                triggered=index == 0,
                trigger_write=10 if index == 0 else None,
            )
            for index in range(4)
        )
        summaries = seed_cluster_summaries(rows, candidate=candidate)
        self.assertEqual(summaries[0]["beneficial_condition_count"], 8)
        self.assertEqual(summaries[0]["beneficial_false_fallback_count"], 2)
        self.assertEqual(summaries[0]["beneficial_false_fallback_rate"], 0.25)
        self.assertEqual(summaries[1]["beneficial_condition_count"], 4)
        self.assertEqual(summaries[1]["beneficial_false_fallback_count"], 1)
        self.assertEqual(summaries[1]["beneficial_false_fallback_rate"], 0.25)
        self.assertEqual(summaries[2]["beneficial_condition_count"], 0)
        self.assertIsNone(summaries[2]["beneficial_false_fallback_rate"])
        result = select_persistence_candidate(rows, candidates=(candidate,))
        metrics = result["candidate_ranking_metrics"]["B128_K2"]
        self.assertEqual(metrics["mean_seed_beneficial_false_fallback_rate"], 0.25)
        self.assertEqual(metrics["beneficial_evidence_seed_count"], 2)
        self.assertEqual(metrics["beneficial_false_fallback_count"], 3)

    def test_many_beneficial_rows_in_one_seed_do_not_outweigh_another_seed(self) -> None:
        candidate = PersistenceCandidate(128, 2)
        rows = [self.outcome(seed=seed, candidate=candidate) for seed in DEVELOPMENT_SEEDS]
        rows.extend(
            self.outcome(
                seed=201,
                candidate=candidate,
                static_waf=0.9,
                device_waf=1.0,
                v2_waf=0.9,
                triggered=True,
                trigger_write=10,
            )
            for _ in range(100)
        )
        rows.append(
            self.outcome(
                seed=202,
                candidate=candidate,
                static_waf=0.9,
                device_waf=1.0,
                v2_waf=0.9,
                triggered=False,
                trigger_write=None,
            )
        )
        result = select_persistence_candidate(rows, candidates=(candidate,))
        metrics = result["candidate_ranking_metrics"]["B128_K2"]
        # mean(1.0, 0.0), not pooled 100 / 101.
        self.assertEqual(metrics["mean_seed_beneficial_false_fallback_rate"], 0.5)
        self.assertEqual(metrics["beneficial_evidence_seed_count"], 2)

    def test_no_beneficial_evidence_is_unevaluable_not_zero_false_fallback(self) -> None:
        candidate = PersistenceCandidate(128, 2)
        rows = [self.outcome(seed=seed, candidate=candidate) for seed in DEVELOPMENT_SEEDS]
        result = select_persistence_candidate(rows, candidates=(candidate,))
        metrics = result["candidate_ranking_metrics"]["B128_K2"]
        self.assertEqual(metrics["beneficial_evidence_seed_count"], 0)
        self.assertEqual(metrics["beneficial_false_fallback_count"], 0)
        self.assertEqual(
            metrics["mean_seed_beneficial_false_fallback_rate"], float("inf")
        )

    def test_many_harmful_rows_in_one_seed_do_not_outweigh_seed_median_rate(self) -> None:
        first = PersistenceCandidate(128, 2)
        second = PersistenceCandidate(256, 2)
        rows = []
        # First has poor pooled rate (64/104) but a seed-level median rate of
        # 1.0.  The 100-row seed is only one cluster in ranking.
        for index in range(100):
            rows.append(
                self.outcome(
                    seed=201,
                    candidate=first,
                    triggered=index < 60,
                    trigger_write=10 if index < 60 else None,
                )
            )
        for seed in DEVELOPMENT_SEEDS[1:]:
            rows.append(self.outcome(seed=seed, candidate=first))

        # Second has a much better pooled row rate but the same seed-level
        # median timely rate. The fixed B*K tie-breaker must choose first.
        for _ in range(100):
            rows.append(self.outcome(seed=201, candidate=second))
        rows.extend(
            [
                self.outcome(seed=202, candidate=second),
                self.outcome(
                    seed=202,
                    candidate=second,
                    triggered=False,
                    trigger_write=None,
                ),
            ]
        )
        for seed in DEVELOPMENT_SEEDS[2:]:
            rows.append(self.outcome(seed=seed, candidate=second))

        result = select_persistence_candidate(rows, candidates=(first, second))
        metrics = result["candidate_ranking_metrics"]
        self.assertEqual(
            metrics["B128_K2"]["harmful_median_timely_detection_rate"], 1.0
        )
        self.assertEqual(
            metrics["B256_K2"]["harmful_median_timely_detection_rate"], 1.0
        )
        self.assertEqual(
            result["selected_candidate"], {"block_size": 128, "persistence_k": 2}
        )

    def test_recovered_harm_uses_median_of_seed_medians(self) -> None:
        candidate = PersistenceCandidate(128, 2)
        rows = []
        # Raw pooled median would be 2.0 because seed 201 supplies 100 rows.
        # The frozen equal-weight seed aggregate is median(2.0, .1, .1, .1, .1).
        for _ in range(100):
            rows.append(
                self.outcome(seed=201, candidate=candidate, v2_waf=-0.8)
            )
        for seed in DEVELOPMENT_SEEDS[1:]:
            rows.append(self.outcome(seed=seed, candidate=candidate, v2_waf=1.1))
        summaries = seed_cluster_summaries(rows, candidate=candidate)
        self.assertEqual(summaries[0]["harmful_median_recovered_harm"], 2.0)
        for summary in summaries[1:]:
            self.assertAlmostEqual(
                summary["harmful_median_recovered_harm"], 0.1
            )
        result = select_persistence_candidate(rows, candidates=(candidate,))
        self.assertAlmostEqual(
            result["candidate_ranking_metrics"]["B128_K2"][
                "harmful_median_recovered_harm"
            ],
            0.1,
        )

    def test_detection_delay_uses_median_of_seed_medians_and_inf_for_no_trigger(self) -> None:
        candidate = PersistenceCandidate(128, 2)
        rows = []
        # Seed 201 has many fast rows; it remains a single 0.1 seed median.
        for _ in range(100):
            rows.append(
                self.outcome(
                    seed=201,
                    candidate=candidate,
                    trigger_write=10,
                    total_host_writes=100,
                )
            )
        for seed in DEVELOPMENT_SEEDS[1:]:
            rows.append(
                self.outcome(
                    seed=seed,
                    candidate=candidate,
                    trigger_write=40,
                    total_host_writes=100,
                )
            )
        summaries = seed_cluster_summaries(rows, candidate=candidate)
        self.assertEqual(
            [summary["harmful_median_detection_delay"] for summary in summaries],
            [0.1, 0.4, 0.4, 0.4, 0.4],
        )
        result = select_persistence_candidate(rows, candidates=(candidate,))
        self.assertAlmostEqual(
            result["candidate_ranking_metrics"]["B128_K2"][
                "harmful_median_detection_delay"
            ],
            0.4,
        )

        missed_rows = [
            self.outcome(
                seed=seed,
                candidate=candidate,
                triggered=False,
                trigger_write=None,
            )
            for seed in DEVELOPMENT_SEEDS
        ]
        missed_summaries = seed_cluster_summaries(missed_rows, candidate=candidate)
        self.assertTrue(
            all(
                summary["harmful_median_detection_delay"] == float("inf")
                for summary in missed_summaries
            )
        )

    def test_final_profile_requires_frozen_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            with self.assertRaises(FileNotFoundError):
                final_profile(path)

            selection = FrozenV2Selection(candidate=PersistenceCandidate(256, 3))
            save_frozen_v2_selection(selection, path)
            loaded = load_frozen_v2_selection(path)
            self.assertEqual(loaded, selection)
            self.assertEqual(development_profile().seeds, DEVELOPMENT_SEEDS)
            self.assertEqual(
                development_profile().measurement_error_rates,
                core_measurement_error_rates(),
            )
            self.assertEqual(final_profile(path).seeds, FINAL_HELD_OUT_SEEDS)


if __name__ == "__main__":
    unittest.main()
