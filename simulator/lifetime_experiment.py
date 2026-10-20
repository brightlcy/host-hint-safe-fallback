from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .baseline_ftl import BaselineFTL
from .config import SSDConfig
from .lifetime_ftl import (
    DeviceOnlyLifetimeFTL,
    OracleLifetimeFTL,
    PersistenceFallbackFTL,
    StaticHostGuidedFTL,
    TrustFallbackFTL,
    run_page_write_events,
)
from .lifetime_workload import (
    LifetimeClass,
    PageWriteEvent,
    SyntheticWorkloadConfig,
    annotate_exact_lifetimes,
    calibration_median_boundary,
    generate_classified_synthetic_prefix,
    generate_synthetic_lpn_sequence,
    inject_nested_random_hint_errors,
)


PRIMARY_ERROR_RATES = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
SYMMETRY_ERROR_RATES = (0.60, 0.80, 1.00)


@dataclass(frozen=True)
class CalibrationResult:
    lifetime_boundary_writes: int
    failure_error_rate: Optional[float]
    failure_association_threshold: Optional[float]
    calibration_points: List[Dict[str, float]]
    rows: List[Dict[str, Any]]


def derive_hint_seed(base_hint_seed: int, workload_seed: int) -> int:
    """Derive a deterministic, collision-free seed for one workload stream."""

    def zigzag(value: int) -> int:
        return value * 2 if value >= 0 else -value * 2 - 1

    left = zigzag(base_hint_seed)
    right = zigzag(workload_seed)
    total = left + right
    return total * (total + 1) // 2 + right


def select_stable_failure_boundary(
    calibration_points: Sequence[Dict[str, float]],
) -> tuple[Optional[float], Optional[float]]:
    """Select the first non-reversing empirical crossover from tested rates.

    The association threshold is calibrated from this workload's observed
    Static-Host versus Device-only crossover. It is not a universal physical
    threshold, and this function deliberately performs no interpolation.
    """

    stable_index = next(
        (
            index
            for index, point in enumerate(calibration_points)
            if point["median_delta_waf"] >= 0.0
            and all(
                later["median_delta_waf"] >= 0.0
                for later in calibration_points[index:]
            )
        ),
        None,
    )
    if stable_index is None:
        return None, None
    stable_point = calibration_points[stable_index]
    return stable_point["error_rate"], stable_point["median_absolute_phi"]


def absolute_hint_phi(events: Iterable[PageWriteEvent]) -> float:
    n11 = n10 = n01 = n00 = 0
    for event in events:
        if event.true_class is LifetimeClass.UNKNOWN or event.host_hint is LifetimeClass.UNKNOWN:
            continue
        true_short = event.true_class is LifetimeClass.SHORT
        hint_short = event.host_hint is LifetimeClass.SHORT
        if true_short and hint_short:
            n11 += 1
        elif true_short and not hint_short:
            n10 += 1
        elif not true_short and hint_short:
            n01 += 1
        else:
            n00 += 1
    denominator = math.sqrt(
        (n11 + n01) * (n11 + n10) * (n00 + n01) * (n00 + n10)
    )
    if denominator == 0.0:
        return 0.0
    return abs((n11 * n00 - n01 * n10) / denominator)


def wear_imbalance_cv(ftl: BaselineFTL) -> float:
    erases = [info.erase_count for info in ftl.metadata.block_info]
    if not erases:
        return 0.0
    mean = sum(erases) / len(erases)
    if mean == 0.0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in erases) / len(erases)
    return math.sqrt(variance) / mean


def _base_ssd_config(logical_pages: int) -> SSDConfig:
    return SSDConfig.from_logical_pages(
        logical_pages=logical_pages,
        page_size=4096,
        chips=4,
        pages_per_block=64,
        overprovision_ratio=0.20,
        gc_threshold_ratio=0.05,
        reserved_gc_blocks=2,
        capacity_basis="controlled-synthetic-working-set",
    )


def _row_from_ftl(
    *,
    ftl: BaselineFTL,
    policy: str,
    seed: int,
    error_rate: Optional[float],
    boundary_writes: int,
    failure_error_rate: Optional[float],
    failure_association_threshold: Optional[float],
    hint_phi: Optional[float],
) -> Dict[str, Any]:
    summary = ftl.get_summary()
    row: Dict[str, Any] = {
        "workload_seed": seed,
        "policy": policy,
        "error_rate": error_rate,
        "lifetime_boundary_writes": boundary_writes,
        "failure_error_rate": failure_error_rate,
        "failure_association_threshold": failure_association_threshold,
        "offline_hint_absolute_phi": hint_phi,
        "host_write_pages": summary["host_write_pages"],
        "nand_write_pages": summary["nand_write_pages"],
        "waf": summary["write_amplification_factor"],
        "valid_page_migrations": summary["valid_page_migrations"],
        "gc_count": summary["gc_count"],
        "erase_count": summary["erase_count"],
        "wear_imbalance_cv": wear_imbalance_cv(ftl),
        "fallback_triggered": False,
        "fallback_trigger_sequence": None,
        "fallback_trigger_write_index": None,
        "fallback_trigger_sample_count": None,
        "detector_sample_count": None,
        "online_hint_absolute_phi": None,
        "hint_fisher_abs_upper_bound": None,
        "short_host_write_pages": None,
        "long_host_write_pages": None,
        "short_gc_migration_pages": None,
        "long_gc_migration_pages": None,
        "placement_fallback_pages": None,
        "host_guided_write_pages": None,
        "device_fallback_write_pages": None,
        "tracker_resolution_count": None,
        "tracker_completion_count": None,
        "deployable_waf_regret": None,
    }
    for field in (
        "short_host_write_pages",
        "long_host_write_pages",
        "short_gc_migration_pages",
        "long_gc_migration_pages",
        "placement_fallback_pages",
    ):
        if field in summary:
            row[field] = summary[field]
    tracker = getattr(ftl, "lifetime_tracker", None)
    if tracker is not None:
        row["tracker_resolution_count"] = tracker.resolution_count
        row["tracker_completion_count"] = tracker.completion_count
    if isinstance(ftl, StaticHostGuidedFTL):
        row["host_guided_write_pages"] = summary["host_write_pages"]
    if isinstance(ftl, TrustFallbackFTL):
        detector = ftl.detector.snapshot()
        row.update(
            {
                "fallback_triggered": detector.triggered,
                "fallback_trigger_sequence": detector.trigger_sequence,
                "fallback_trigger_write_index": detector.trigger_sequence,
                "fallback_trigger_sample_count": detector.trigger_sample_count,
                "detector_sample_count": detector.sample_count,
                "online_hint_absolute_phi": detector.absolute_phi,
                "hint_fisher_abs_upper_bound": detector.fisher_abs_upper_bound,
                "host_guided_write_pages": ftl.host_guided_write_pages,
                "device_fallback_write_pages": ftl.device_fallback_write_pages,
            }
        )
    return row


def persistence_v2_run_row(
    *,
    ftl: PersistenceFallbackFTL,
    seed: int,
    error_rate: float,
    boundary_writes: int,
    failure_association_threshold: float,
) -> Dict[str, Any]:
    """Create the run-level artifact row required by Safe-Fallback v2."""

    summary = ftl.get_summary()
    return {
        "workload_seed": seed,
        "policy": "persistence_fallback_v2",
        "error_rate": error_rate,
        "detector_version": summary["detector_version"],
        "block_size": summary["block_size"],
        "persistence_k": summary["persistence_k"],
        "min_marginal_support": summary["min_marginal_support"],
        "failure_association_threshold": failure_association_threshold,
        "lifetime_boundary_writes": boundary_writes,
        "completed_block_count": summary["completed_block_count"],
        "good_block_count": summary["good_block_count"],
        "bad_block_count": summary["bad_block_count"],
        "invalid_block_count": summary["invalid_block_count"],
        "partial_block_sample_count": summary["partial_block_sample_count"],
        "fallback_triggered": summary["fallback_triggered"],
        "trigger_block_index": summary["trigger_block_index"],
        "trigger_detector_observation_ordinal": summary[
            "trigger_detector_observation_ordinal"
        ],
        "trigger_host_write_index": summary["trigger_host_write_index"],
        "host_write_pages": summary["host_write_pages"],
        "nand_write_pages": summary["nand_write_pages"],
        "waf": summary["write_amplification_factor"],
        "valid_page_migrations": summary["valid_page_migrations"],
        "gc_count": summary["gc_count"],
        "erase_count": summary["erase_count"],
        "wear_imbalance_cv": wear_imbalance_cv(ftl),
        "host_guided_write_pages": summary["host_guided_write_pages"],
        "device_fallback_write_pages": summary["device_fallback_write_pages"],
    }


def persistence_v2_block_rows(
    *,
    ftl: PersistenceFallbackFTL,
    workload_seed: int,
    error_rate: float,
) -> List[Dict[str, Any]]:
    """Return one artifact row per completed v2 evidence block only."""

    detector = ftl.detector
    rows: List[Dict[str, Any]] = []
    for block in detector.completed_blocks:
        rows.append(
            {
                "detector_version": detector.detector_version,
                "workload_seed": workload_seed,
                "error_rate": error_rate,
                "block_size": detector.block_size,
                "persistence_k": detector.persistence_k,
                "block_index": block.block_index,
                "first_detector_observation_ordinal": (
                    block.first_detector_observation_ordinal
                ),
                "last_detector_observation_ordinal": (
                    block.last_detector_observation_ordinal
                ),
                "block_end_host_write_index": block.block_end_host_write_index,
                "usable_sample_count": block.usable_sample_count,
                "ss_count": block.ss_count,
                "sl_count": block.sl_count,
                "ls_count": block.ls_count,
                "ll_count": block.ll_count,
                "true_short_count": block.true_short_count,
                "true_long_count": block.true_long_count,
                "hint_short_count": block.hint_short_count,
                "hint_long_count": block.hint_long_count,
                "absolute_phi": block.absolute_phi,
                "block_state": block.block_state,
                "invalid_reason": block.invalid_reason,
                "bad_streak_before": block.bad_streak_before,
                "bad_streak_after": block.bad_streak_after,
                "triggered_on_this_block": block.triggered_on_this_block,
            }
        )
    return rows


def calibrate_synthetic_failure_boundary(
    *,
    calibration_seeds: Sequence[int] = (1, 2, 3),
    total_writes: int = 50_000,
    logical_pages: int = 2_048,
    hint_seed: int = 42,
    error_rates: Sequence[float] = PRIMARY_ERROR_RATES,
) -> CalibrationResult:
    if not calibration_seeds:
        raise ValueError("calibration_seeds must not be empty")
    if not error_rates:
        raise ValueError("error_rates must not be empty")
    if tuple(sorted(set(error_rates))) != tuple(error_rates):
        raise ValueError("error_rates must be sorted and unique")
    if any(error_rate not in PRIMARY_ERROR_RATES for error_rate in error_rates):
        raise ValueError("calibration error_rates must be tested PRIMARY_ERROR_RATES")

    raw_by_seed: Dict[int, List[PageWriteEvent]] = {}
    pooled: List[PageWriteEvent] = []
    for seed in calibration_seeds:
        sequence = generate_synthetic_lpn_sequence(
            SyntheticWorkloadConfig(
                total_writes=total_writes,
                logical_pages=logical_pages,
                seed=seed,
            )
        )
        raw = annotate_exact_lifetimes(sequence)
        raw_by_seed[seed] = raw
        pooled.extend(raw)
    boundary = calibration_median_boundary(pooled)
    ssd = _base_ssd_config(logical_pages)

    rows: List[Dict[str, Any]] = []
    device_waf_by_seed: Dict[int, float] = {}
    classified_by_seed: Dict[int, List[PageWriteEvent]] = {}
    for seed, raw in raw_by_seed.items():
        classified = generate_classified_synthetic_prefix(
            SyntheticWorkloadConfig(
                total_writes=total_writes,
                logical_pages=logical_pages,
                seed=seed,
            ),
            boundary_writes=boundary,
        )
        classified_by_seed[seed] = classified
        ftl = DeviceOnlyLifetimeFTL(ssd, boundary_writes=boundary)
        run_page_write_events(ftl, classified)
        device_waf_by_seed[seed] = float(ftl.get_summary()["write_amplification_factor"])
        rows.append(
            _row_from_ftl(
                ftl=ftl,
                policy="device_only_lifetime",
                seed=seed,
                error_rate=0.0,
                boundary_writes=boundary,
                failure_error_rate=None,
                failure_association_threshold=None,
                hint_phi=0.0,
            )
        )

    calibration_points: List[Dict[str, float]] = []
    for error_rate in error_rates:
        paired_differences: List[float] = []
        phi_values: List[float] = []
        for seed in calibration_seeds:
            events = inject_nested_random_hint_errors(
                classified_by_seed[seed],
                error_rate=error_rate,
                seed=derive_hint_seed(hint_seed, seed),
            )
            phi = absolute_hint_phi(events)
            phi_values.append(phi)
            ftl = StaticHostGuidedFTL(ssd, boundary_writes=boundary)
            run_page_write_events(ftl, events)
            row = _row_from_ftl(
                ftl=ftl,
                policy="static_host_guided",
                seed=seed,
                error_rate=error_rate,
                boundary_writes=boundary,
                failure_error_rate=None,
                failure_association_threshold=None,
                hint_phi=phi,
            )
            rows.append(row)
            paired_differences.append(row["waf"] - device_waf_by_seed[seed])

        median_delta = float(median(paired_differences))
        median_phi = float(median(phi_values))
        calibration_points.append(
            {
                "error_rate": float(error_rate),
                "median_delta_waf": median_delta,
                "median_absolute_phi": median_phi,
            }
        )
        for row in rows:
            if row["policy"] == "static_host_guided" and row["error_rate"] == error_rate:
                row["median_delta_waf"] = median_delta
                row["median_absolute_phi"] = median_phi

    failure_error_rate, failure_association_threshold = select_stable_failure_boundary(
        calibration_points
    )

    return CalibrationResult(
        lifetime_boundary_writes=boundary,
        failure_error_rate=failure_error_rate,
        failure_association_threshold=failure_association_threshold,
        calibration_points=calibration_points,
        rows=rows,
    )


def run_synthetic_measurement(
    calibration: CalibrationResult,
    *,
    measurement_seeds: Sequence[int] = (101, 102, 103, 104, 105),
    total_writes: int = 50_000,
    logical_pages: int = 2_048,
    hint_seed: int = 42,
    error_rates: Sequence[float] = PRIMARY_ERROR_RATES + SYMMETRY_ERROR_RATES,
    detector_window: int = 128,
) -> List[Dict[str, Any]]:
    ssd = _base_ssd_config(logical_pages)
    rows: List[Dict[str, Any]] = []

    for seed in measurement_seeds:
        classified = generate_classified_synthetic_prefix(
            SyntheticWorkloadConfig(
                total_writes=total_writes,
                logical_pages=logical_pages,
                seed=seed,
            ),
            boundary_writes=calibration.lifetime_boundary_writes,
        )
        baseline = BaselineFTL(ssd)
        device_only = DeviceOnlyLifetimeFTL(
            ssd, boundary_writes=calibration.lifetime_boundary_writes
        )
        oracle = OracleLifetimeFTL(ssd)
        run_page_write_events(baseline, classified)
        run_page_write_events(device_only, classified)
        run_page_write_events(oracle, classified)
        rows.extend(
            (
                _row_from_ftl(
                    ftl=baseline,
                    policy="baseline_zero_information",
                    seed=seed,
                    error_rate=None,
                    boundary_writes=calibration.lifetime_boundary_writes,
                    failure_error_rate=calibration.failure_error_rate,
                    failure_association_threshold=calibration.failure_association_threshold,
                    hint_phi=None,
                ),
                _row_from_ftl(
                    ftl=device_only,
                    policy="device_only_lifetime",
                    seed=seed,
                    error_rate=None,
                    boundary_writes=calibration.lifetime_boundary_writes,
                    failure_error_rate=calibration.failure_error_rate,
                    failure_association_threshold=calibration.failure_association_threshold,
                    hint_phi=None,
                ),
                _row_from_ftl(
                    ftl=oracle,
                    policy="oracle_observed_lifetime",
                    seed=seed,
                    error_rate=None,
                    boundary_writes=calibration.lifetime_boundary_writes,
                    failure_error_rate=calibration.failure_error_rate,
                    failure_association_threshold=calibration.failure_association_threshold,
                    hint_phi=None,
                ),
            )
        )
        device_waf = float(device_only.get_summary()["write_amplification_factor"])

        for error_rate in error_rates:
            events = inject_nested_random_hint_errors(
                classified,
                error_rate=error_rate,
                seed=derive_hint_seed(hint_seed, seed),
            )
            phi = absolute_hint_phi(events)

            policies: list[tuple[str, BaselineFTL]] = [
                (
                    "static_host_guided",
                    StaticHostGuidedFTL(
                        ssd, boundary_writes=calibration.lifetime_boundary_writes
                    ),
                )
            ]
            if calibration.failure_association_threshold is not None:
                policies.append(
                    (
                        "safe_fallback",
                        TrustFallbackFTL(
                            ssd,
                            boundary_writes=calibration.lifetime_boundary_writes,
                            failure_association_threshold=calibration.failure_association_threshold,
                            detector_window=detector_window,
                            detector_min_samples=30,
                        ),
                    )
                )

            condition_rows: List[Dict[str, Any]] = []
            for policy_name, ftl in policies:
                run_page_write_events(ftl, events)
                condition_rows.append(
                    _row_from_ftl(
                        ftl=ftl,
                        policy=policy_name,
                        seed=seed,
                        error_rate=error_rate,
                        boundary_writes=calibration.lifetime_boundary_writes,
                        failure_error_rate=calibration.failure_error_rate,
                        failure_association_threshold=calibration.failure_association_threshold,
                        hint_phi=phi,
                    )
                )
            static_waf = next(
                row["waf"]
                for row in condition_rows
                if row["policy"] == "static_host_guided"
            )
            best = min(static_waf, device_waf)
            for row in condition_rows:
                row["deployable_waf_regret"] = (row["waf"] - best) / best
            rows.extend(condition_rows)
    return rows


def save_calibration_audit_rows(
    calibration: CalibrationResult, path: Path
) -> Path:
    return save_experiment_rows(calibration.calibration_points, path)


def save_experiment_rows(rows: Sequence[Dict[str, Any]], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("rows must not be empty")
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def run_full_synthetic_experiment(output_dir: Path) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    calibration = calibrate_synthetic_failure_boundary()
    measurement_rows = run_synthetic_measurement(calibration)
    calibration_path = save_experiment_rows(
        calibration.rows, output_dir / "calibration_results.csv"
    )
    calibration_audit_path = save_calibration_audit_rows(
        calibration, output_dir / "calibration_boundary_audit.csv"
    )
    measurement_path = save_experiment_rows(
        measurement_rows, output_dir / "measurement_results.csv"
    )
    summary = {
        "lifetime_boundary_writes": calibration.lifetime_boundary_writes,
        "failure_error_rate": calibration.failure_error_rate,
        "failure_association_threshold": calibration.failure_association_threshold,
        "calibration_points": calibration.calibration_points,
        "calibration_seeds": [1, 2, 3],
        "measurement_seeds": [101, 102, 103, 104, 105],
        "primary_error_rates": list(PRIMARY_ERROR_RATES),
        "symmetry_error_rates": list(SYMMETRY_ERROR_RATES),
        "calibration_results": calibration_path.name,
        "calibration_boundary_audit": calibration_audit_path.name,
        "measurement_results": measurement_path.name,
    }
    summary_path = output_dir / "experiment_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary
