"""Frozen protocol helpers for Safe-Fallback persistence detector v2.

The functions here prepare analysis and execution inputs; they never launch an
experiment.  Error-rate rows are grouped by workload seed and are explicitly
not represented as independent statistical samples.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


FROZEN_LIFETIME_BOUNDARY_WRITES = 451
FROZEN_FAILURE_ASSOCIATION_THRESHOLD = 0.29980003993028376
FROZEN_MIN_MARGINAL_SUPPORT = 5
V2_BLOCK_SIZES = (128, 256, 512)
V2_PERSISTENCE_VALUES = (2, 3)
DEVELOPMENT_SEEDS = (201, 202, 203, 204, 205)
FINAL_HELD_OUT_SEEDS = (301, 302, 303, 304, 305)
TIMELY_DETECTION_FRACTION = 0.5


def core_measurement_error_rates() -> tuple[float, ...]:
    """Read the established v1 core measurement grid without duplicating it."""

    from .lifetime_experiment import PRIMARY_ERROR_RATES, SYMMETRY_ERROR_RATES

    return PRIMARY_ERROR_RATES + SYMMETRY_ERROR_RATES


@dataclass(frozen=True, order=True)
class PersistenceCandidate:
    block_size: int
    persistence_k: int

    def __post_init__(self) -> None:
        if self.block_size not in V2_BLOCK_SIZES:
            raise ValueError(f"block_size must be one of {V2_BLOCK_SIZES}")
        if self.persistence_k not in V2_PERSISTENCE_VALUES:
            raise ValueError(
                f"persistence_k must be one of {V2_PERSISTENCE_VALUES}"
            )


@dataclass(frozen=True)
class V2ExperimentProfile:
    stage: str
    seeds: tuple[int, ...]
    lifetime_boundary_writes: int = FROZEN_LIFETIME_BOUNDARY_WRITES
    failure_association_threshold: float = FROZEN_FAILURE_ASSOCIATION_THRESHOLD
    min_marginal_support: int = FROZEN_MIN_MARGINAL_SUPPORT
    measurement_error_rates: tuple[float, ...] = field(
        default_factory=core_measurement_error_rates
    )


@dataclass(frozen=True)
class FrozenV2Selection:
    """The single selected config that authorizes a future final run."""

    candidate: PersistenceCandidate
    lifetime_boundary_writes: int = FROZEN_LIFETIME_BOUNDARY_WRITES
    failure_association_threshold: float = FROZEN_FAILURE_ASSOCIATION_THRESHOLD
    min_marginal_support: int = FROZEN_MIN_MARGINAL_SUPPORT
    development_seeds: tuple[int, ...] = DEVELOPMENT_SEEDS


def development_profile() -> V2ExperimentProfile:
    return V2ExperimentProfile(stage="development", seeds=DEVELOPMENT_SEEDS)


def final_profile(selection_path: Path) -> V2ExperimentProfile:
    """Load a frozen selected config before exposing final held-out inputs."""

    load_frozen_v2_selection(selection_path)
    return V2ExperimentProfile(stage="final", seeds=FINAL_HELD_OUT_SEEDS)


def save_frozen_v2_selection(selection: FrozenV2Selection, path: Path) -> None:
    if selection.lifetime_boundary_writes != FROZEN_LIFETIME_BOUNDARY_WRITES:
        raise ValueError("final selection cannot change frozen lifetime boundary")
    if (
        selection.failure_association_threshold
        != FROZEN_FAILURE_ASSOCIATION_THRESHOLD
    ):
        raise ValueError("final selection cannot change frozen association threshold")
    if selection.min_marginal_support != FROZEN_MIN_MARGINAL_SUPPORT:
        raise ValueError("final selection cannot change frozen marginal support")
    if tuple(selection.development_seeds) != DEVELOPMENT_SEEDS:
        raise ValueError("final selection must retain the frozen development split")
    payload = asdict(selection)
    payload["candidate"] = asdict(selection.candidate)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_frozen_v2_selection(path: Path) -> FrozenV2Selection:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            "final v2 execution requires a frozen development selection config"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidate = PersistenceCandidate(**payload["candidate"])
    selection = FrozenV2Selection(
        candidate=candidate,
        lifetime_boundary_writes=int(payload["lifetime_boundary_writes"]),
        failure_association_threshold=float(payload["failure_association_threshold"]),
        min_marginal_support=int(payload["min_marginal_support"]),
        development_seeds=tuple(int(seed) for seed in payload["development_seeds"]),
    )
    # Reuse the write-time checks to reject altered configs as well.
    if selection.lifetime_boundary_writes != FROZEN_LIFETIME_BOUNDARY_WRITES:
        raise ValueError("frozen selection changes lifetime boundary")
    if selection.failure_association_threshold != FROZEN_FAILURE_ASSOCIATION_THRESHOLD:
        raise ValueError("frozen selection changes association threshold")
    if selection.min_marginal_support != FROZEN_MIN_MARGINAL_SUPPORT:
        raise ValueError("frozen selection changes marginal support")
    if selection.development_seeds != DEVELOPMENT_SEEDS:
        raise ValueError("frozen selection changes development seeds")
    return selection


def classify_static_device_delta(static_waf: float, device_waf: float) -> str:
    """Classify a paired condition; exact numeric equality is neutral."""

    delta = static_waf - device_waf
    if delta > 0.0:
        return "harmful"
    if delta < 0.0:
        return "beneficial"
    return "neutral"


def timely_detection(
    trigger_host_write_index: Optional[int], total_host_writes: int
) -> bool:
    if total_host_writes < 1:
        raise ValueError("total_host_writes must be positive")
    return (
        trigger_host_write_index is not None
        and trigger_host_write_index <= TIMELY_DETECTION_FRACTION * total_host_writes
    )


def v2_condition_outcome(
    *,
    workload_seed: int,
    error_rate: float,
    candidate: PersistenceCandidate,
    static_waf: float,
    device_waf: float,
    v2_waf: float,
    fallback_triggered: bool,
    trigger_host_write_index: Optional[int],
    total_host_writes: int,
) -> Dict[str, Any]:
    """Build one deterministic, seed-clusterable v2 analysis row."""

    label = classify_static_device_delta(static_waf, device_waf)
    return {
        "workload_seed": workload_seed,
        "error_rate": error_rate,
        "block_size": candidate.block_size,
        "persistence_k": candidate.persistence_k,
        "ground_truth": label,
        "delta_static_device": static_waf - device_waf,
        "false_fallback": label == "beneficial" and fallback_triggered,
        "timely_detection": label == "harmful"
        and timely_detection(trigger_host_write_index, total_host_writes),
        "detection_delay": (
            None
            if trigger_host_write_index is None
            else trigger_host_write_index / total_host_writes
        ),
        "recovered_harm": static_waf - v2_waf,
        "fallback_triggered": fallback_triggered,
    }


def seed_cluster_summaries(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidate: PersistenceCandidate,
    expected_seeds: Sequence[int] = DEVELOPMENT_SEEDS,
) -> List[Dict[str, Any]]:
    """Summarize candidates by seed, without treating error rates as iid rows.

    Each seed contributes one cluster summary to ranking.  False-fallback
    counts remain auditable artifacts, while ranking uses the arithmetic mean
    of per-seed false-fallback rates over seeds with beneficial evidence.
    """

    rows_by_seed: Dict[int, List[Mapping[str, Any]]] = {seed: [] for seed in expected_seeds}
    for row in rows:
        if (
            int(row["block_size"]) == candidate.block_size
            and int(row["persistence_k"]) == candidate.persistence_k
            and int(row["workload_seed"]) in rows_by_seed
        ):
            rows_by_seed[int(row["workload_seed"])].append(row)

    summaries: List[Dict[str, Any]] = []
    for seed in expected_seeds:
        seed_rows = rows_by_seed[seed]
        harmful = [row for row in seed_rows if row["ground_truth"] == "harmful"]
        beneficial = [row for row in seed_rows if row["ground_truth"] == "beneficial"]
        timely = sum(bool(row["timely_detection"]) for row in harmful)
        recovered = [float(row["recovered_harm"]) for row in harmful]
        timely_delays = [
            float(row["detection_delay"])
            for row in harmful
            if bool(row["timely_detection"])
            and row["detection_delay"] is not None
        ]
        summaries.append(
            {
                "workload_seed": seed,
                "block_size": candidate.block_size,
                "persistence_k": candidate.persistence_k,
                "harmful_condition_count": len(harmful),
                "beneficial_condition_count": len(beneficial),
                "harmful_timely_detection_rate": (
                    timely / len(harmful) if harmful else None
                ),
                "harmful_median_recovered_harm": median(recovered) if recovered else None,
                # A harmful seed with no timely trigger is worst-case delay.
                # A seed with no harmful condition remains absent from harmful
                # aggregates rather than being assigned a numeric value.
                "harmful_median_detection_delay": (
                    median(timely_delays)
                    if timely_delays
                    else (math.inf if harmful else None)
                ),
                "beneficial_false_fallback_count": sum(
                    bool(row["false_fallback"]) for row in beneficial
                ),
                "beneficial_false_fallback_rate": (
                    sum(bool(row["false_fallback"]) for row in beneficial)
                    / len(beneficial)
                    if beneficial
                    else None
                ),
            }
        )
    return summaries


def candidate_is_eligible(seed_summaries: Iterable[Mapping[str, Any]]) -> bool:
    summaries = list(seed_summaries)
    timely_passes = sum(
        summary["harmful_timely_detection_rate"] is not None
        and float(summary["harmful_timely_detection_rate"]) >= 0.75
        for summary in summaries
    )
    recovered_passes = sum(
        summary["harmful_median_recovered_harm"] is not None
        and float(summary["harmful_median_recovered_harm"]) > 0.0
        for summary in summaries
    )
    return timely_passes >= 4 and recovered_passes >= 4


def _candidate_selection_metrics(
    seed_summaries: Iterable[Mapping[str, Any]],
) -> tuple[float, float, float, float]:
    """Aggregate one metric per seed cluster; never pool harmful rows."""

    summaries = list(seed_summaries)
    false_fallback_rates = [
        float(summary["beneficial_false_fallback_rate"])
        for summary in summaries
        if summary["beneficial_false_fallback_rate"] is not None
    ]
    timely_rates = [
        float(summary["harmful_timely_detection_rate"])
        for summary in summaries
        if summary["harmful_timely_detection_rate"] is not None
    ]
    recovered_harms = [
        float(summary["harmful_median_recovered_harm"])
        for summary in summaries
        if summary["harmful_median_recovered_harm"] is not None
    ]
    detection_delays = [
        float(summary["harmful_median_detection_delay"])
        for summary in summaries
        if summary["harmful_condition_count"] > 0
    ]
    # No beneficial evidence is unevaluable, never evidence of a zero rate.
    mean_false_fallback_rate = (
        sum(false_fallback_rates) / len(false_fallback_rates)
        if false_fallback_rates
        else math.inf
    )
    timely_rate = float(median(timely_rates)) if timely_rates else 0.0
    recovered_harm = float(median(recovered_harms)) if recovered_harms else -math.inf
    median_delay = float(median(detection_delays)) if detection_delays else math.inf
    return mean_false_fallback_rate, timely_rate, recovered_harm, median_delay


def select_persistence_candidate(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidates: Sequence[PersistenceCandidate] = tuple(
        PersistenceCandidate(block_size, persistence_k)
        for block_size in V2_BLOCK_SIZES
        for persistence_k in V2_PERSISTENCE_VALUES
    ),
    expected_seeds: Sequence[int] = DEVELOPMENT_SEEDS,
) -> Dict[str, Any]:
    """Apply the frozen eligibility gates and lexicographic selection rule."""

    materialized_rows = list(rows)
    eligible: List[PersistenceCandidate] = []
    summaries: Dict[PersistenceCandidate, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        candidate_summaries = seed_cluster_summaries(
            materialized_rows, candidate=candidate, expected_seeds=expected_seeds
        )
        summaries[candidate] = candidate_summaries
        if candidate_is_eligible(candidate_summaries):
            eligible.append(candidate)

    if not eligible:
        return {
            "selection_status": "NO_GO",
            "selected_candidate": None,
            "candidate_seed_summaries": {
                f"B{candidate.block_size}_K{candidate.persistence_k}": summaries[candidate]
                for candidate in candidates
            },
        }

    def ranking_key(candidate: PersistenceCandidate) -> tuple[float, ...]:
        false_fallback_rate, timely_rate, recovered_harm, median_delay = (
            _candidate_selection_metrics(summaries[candidate])
        )
        return (
            false_fallback_rate,
            -timely_rate,
            -recovered_harm,
            median_delay,
            candidate.block_size * candidate.persistence_k,
            candidate.block_size,
            candidate.persistence_k,
        )

    selected = min(eligible, key=ranking_key)
    return {
        "selection_status": "SELECTED",
        "selected_candidate": {
            "block_size": selected.block_size,
            "persistence_k": selected.persistence_k,
        },
        "ranking_key": ranking_key(selected),
        "candidate_ranking_metrics": {
            f"B{candidate.block_size}_K{candidate.persistence_k}": {
                "mean_seed_beneficial_false_fallback_rate": _candidate_selection_metrics(
                    summaries[candidate]
                )[0],
                "beneficial_evidence_seed_count": sum(
                    summary["beneficial_condition_count"] > 0
                    for summary in summaries[candidate]
                ),
                "beneficial_false_fallback_count": sum(
                    int(summary["beneficial_false_fallback_count"])
                    for summary in summaries[candidate]
                ),
                "harmful_median_timely_detection_rate": _candidate_selection_metrics(
                    summaries[candidate]
                )[1],
                "harmful_median_recovered_harm": _candidate_selection_metrics(
                    summaries[candidate]
                )[2],
                "harmful_median_detection_delay": _candidate_selection_metrics(
                    summaries[candidate]
                )[3],
            }
            for candidate in candidates
        },
        "candidate_seed_summaries": {
            f"B{candidate.block_size}_K{candidate.persistence_k}": summaries[candidate]
            for candidate in candidates
        },
    }
