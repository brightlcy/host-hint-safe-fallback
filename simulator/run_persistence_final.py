"""Execute the one frozen Safe-Fallback v2 final holdout matrix.

This module deliberately has no candidate grid or selection path.  Production
execution is enabled only by the reviewed frozen selection artifact and the
corresponding git provenance tags.  Its test-only configuration uses synthetic
non-holdout seeds so unit tests can never execute final seeds 301--305.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .baseline_ftl import BaselineFTL
from .lifetime_experiment import (
    _base_ssd_config,
    _row_from_ftl,
    absolute_hint_phi,
    derive_hint_seed,
    persistence_v2_block_rows,
    persistence_v2_run_row,
    run_page_write_events,
    save_experiment_rows,
)
from .lifetime_ftl import (
    DeviceOnlyLifetimeFTL,
    PersistenceFallbackFTL,
    StaticHostGuidedFTL,
    TrustFallbackFTL,
)
from .lifetime_workload import (
    PageWriteEvent,
    SyntheticWorkloadConfig,
    generate_classified_synthetic_prefix,
    inject_nested_random_hint_errors,
)
from .persistence_protocol import (
    FINAL_HELD_OUT_SEEDS,
    FROZEN_FAILURE_ASSOCIATION_THRESHOLD,
    FROZEN_LIFETIME_BOUNDARY_WRITES,
    FROZEN_MIN_MARGINAL_SUPPORT,
    FrozenV2Selection,
    classify_static_device_delta,
    core_measurement_error_rates,
    final_profile,
    load_frozen_v2_selection,
    timely_detection,
)


FROZEN_PROTOCOL_TAG = "safe-fallback-v2-protocol-frozen-20260820"
FROZEN_SELECTION_TAG = "safe-fallback-v2-selection-frozen-20260820"
REQUIRED_BLOCK_SIZE = 512
REQUIRED_PERSISTENCE_K = 2
OUTPUT_SCHEMA_VERSION = "safe-fallback-v2-final-runner-v1"
DEFAULT_HINT_SEED = 42
CORE_TOTAL_WRITES = 50_000
CORE_LOGICAL_PAGES = 2_048
V1_DETECTOR_WINDOW = 128
V1_DETECTOR_MIN_SAMPLES = 30
# These are intentionally disjoint from both development and final holdouts.
TEST_ONLY_SEEDS = (901, 902, 903, 904, 905)


@dataclass(frozen=True)
class FinalRunConfig:
    """Immutable final inputs. ``test_mode`` cannot use actual final seeds."""

    selection_file: Path | None = None
    stage: str = "final"
    seeds: tuple[int, ...] = FINAL_HELD_OUT_SEEDS
    error_rates: tuple[float, ...] = core_measurement_error_rates()
    total_writes: int = CORE_TOTAL_WRITES
    logical_pages: int = CORE_LOGICAL_PAGES
    hint_seed: int = DEFAULT_HINT_SEED
    test_mode: bool = False

    def validate(self) -> FrozenV2Selection:
        if self.stage != "final":
            raise ValueError("only the explicit final stage is supported")
        if self.selection_file is None:
            raise ValueError("final runner requires a frozen selection file")
        # final_profile intentionally routes through the frozen selection helper.
        profile = final_profile(Path(self.selection_file))
        selection = load_frozen_v2_selection(Path(self.selection_file))
        if (selection.candidate.block_size, selection.candidate.persistence_k) != (
            REQUIRED_BLOCK_SIZE,
            REQUIRED_PERSISTENCE_K,
        ):
            raise ValueError("final runner requires frozen selection B512/K2")
        if profile.lifetime_boundary_writes != selection.lifetime_boundary_writes:
            raise ValueError("frozen selection conflicts with final lifetime boundary")
        if (
            profile.failure_association_threshold
            != selection.failure_association_threshold
        ):
            raise ValueError("frozen selection conflicts with final association threshold")
        if profile.min_marginal_support != selection.min_marginal_support:
            raise ValueError("frozen selection conflicts with final marginal support")
        if any(seed in FINAL_HELD_OUT_SEEDS for seed in self.seeds) and self.test_mode:
            raise ValueError("test mode must never execute final held-out seeds")
        if any(seed in selection.development_seeds for seed in self.seeds):
            raise ValueError("final runner must not execute development seeds")
        expected_seeds = TEST_ONLY_SEEDS if self.test_mode else FINAL_HELD_OUT_SEEDS
        if tuple(self.seeds) != expected_seeds:
            raise ValueError("final runner requires exactly its authorized seed split")
        if not self.test_mode:
            if tuple(self.error_rates) != profile.measurement_error_rates:
                raise ValueError("final runner requires the frozen measurement grid")
            if self.total_writes != CORE_TOTAL_WRITES:
                raise ValueError("final runner requires the core 50k write count")
            if self.logical_pages != CORE_LOGICAL_PAGES:
                raise ValueError("final runner requires the core logical-page count")
        elif not self.error_rates:
            raise ValueError("test final runs require at least one error rate")
        return selection


def _event_identifier(events: Iterable[PageWriteEvent]) -> str:
    digest = hashlib.sha256()
    for event in events:
        digest.update(
            (
                f"{event.write_sequence}|{event.lpn}|{event.true_class.value}|"
                f"{event.host_hint.value}\n"
            ).encode("ascii")
        )
    return f"sha256:{digest.hexdigest()}"


def _git_value(args: Sequence[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _git_required_value(args: Sequence[str], *, failure_message: str) -> str:
    value = _git_value(args)
    if not value or value == "unavailable":
        raise RuntimeError(failure_message)
    return value


def _git_worktree_clean() -> bool:
    try:
        return subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
        ) == ""
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("final experiment requires git provenance") from exc


def _git_provenance(*, production: bool) -> dict[str, Any]:
    """Collect final provenance and enforce production guards before work."""

    head = _git_required_value(
        ("rev-parse", "HEAD"), failure_message="final experiment requires a git HEAD commit"
    )
    protocol_commit = _git_required_value(
        ("rev-parse", "--verify", f"{FROZEN_PROTOCOL_TAG}^{{commit}}"),
        failure_message=f"final experiment requires frozen protocol tag {FROZEN_PROTOCOL_TAG}",
    )
    selection_commit = _git_required_value(
        ("rev-parse", "--verify", f"{FROZEN_SELECTION_TAG}^{{commit}}"),
        failure_message=(
            "final experiment requires frozen selection tag " f"{FROZEN_SELECTION_TAG}"
        ),
    )
    worktree_clean = _git_worktree_clean()
    if production and not worktree_clean:
        raise ValueError("final experiment requires a clean git worktree")
    return {
        "git_commit_hash": head,
        "git_branch": _git_value(("branch", "--show-current")),
        "git_worktree_clean": worktree_clean,
        "frozen_protocol_tag": FROZEN_PROTOCOL_TAG,
        "frozen_protocol_commit": protocol_commit,
        "frozen_selection_tag": FROZEN_SELECTION_TAG,
        "frozen_selection_commit": selection_commit,
    }


def _selection_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _run_row(
    ftl: BaselineFTL,
    *,
    policy: str,
    seed: int,
    error_rate: float | None,
    selection: FrozenV2Selection,
    hint_identifier: str | None,
) -> dict[str, Any]:
    row = _row_from_ftl(
        ftl=ftl,
        policy=policy,
        seed=seed,
        error_rate=error_rate,
        boundary_writes=selection.lifetime_boundary_writes,
        failure_error_rate=None,
        failure_association_threshold=selection.failure_association_threshold,
        hint_phi=None,
    )
    row["hint_realization_id"] = hint_identifier
    return row


def _condition_outcome(
    *,
    seed: int,
    error_rate: float,
    static: Mapping[str, Any],
    device: Mapping[str, Any],
    v1: Mapping[str, Any],
    v2: Mapping[str, Any],
    hint_identifier: str,
    total_writes: int,
) -> dict[str, Any]:
    static_waf = float(static["waf"])
    device_waf = float(device["waf"])
    v1_waf = float(v1["waf"])
    v2_waf = float(v2["waf"])
    ground_truth = classify_static_device_delta(static_waf, device_waf)
    v1_triggered = bool(v1["fallback_triggered"])
    v2_triggered = bool(v2["fallback_triggered"])
    v1_trigger = v1["fallback_trigger_write_index"]
    v2_trigger = v2["trigger_host_write_index"]
    return {
        "workload_seed": seed,
        "error_rate": error_rate,
        "static_waf": static_waf,
        "device_waf": device_waf,
        "v1_waf": v1_waf,
        "v2_waf": v2_waf,
        "delta_static_device": static_waf - device_waf,
        "ground_truth": ground_truth,
        "v1_fallback_triggered": v1_triggered,
        "v2_fallback_triggered": v2_triggered,
        "v1_trigger_host_write_index": v1_trigger,
        "v2_trigger_host_write_index": v2_trigger,
        "v1_false_fallback": ground_truth == "beneficial" and v1_triggered,
        "v2_false_fallback": ground_truth == "beneficial" and v2_triggered,
        "v1_timely_detection": ground_truth == "harmful"
        and timely_detection(v1_trigger, total_writes),
        "v2_timely_detection": ground_truth == "harmful"
        and timely_detection(v2_trigger, total_writes),
        "v1_recovered_harm": static_waf - v1_waf,
        "v2_recovered_harm": static_waf - v2_waf,
        "hint_realization_id": hint_identifier,
    }


def summarize_final_outcomes(
    rows: Iterable[Mapping[str, Any]], *, expected_seeds: Sequence[int]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the pre-data final gates using one equally weighted seed at a time."""

    by_seed: dict[int, list[Mapping[str, Any]]] = {int(seed): [] for seed in expected_seeds}
    for row in rows:
        seed = int(row["workload_seed"])
        if seed in by_seed:
            by_seed[seed].append(row)

    summaries: list[dict[str, Any]] = []
    for seed in expected_seeds:
        seed_rows = by_seed[int(seed)]
        harmful = [row for row in seed_rows if row["ground_truth"] == "harmful"]
        beneficial = [row for row in seed_rows if row["ground_truth"] == "beneficial"]
        summaries.append(
            {
                "workload_seed": int(seed),
                "harmful_condition_count": len(harmful),
                "harmful_timely_detection_rate": (
                    sum(bool(row["v2_timely_detection"]) for row in harmful) / len(harmful)
                    if harmful
                    else None
                ),
                "harmful_median_recovered_harm": (
                    median(float(row["v2_recovered_harm"]) for row in harmful)
                    if harmful
                    else None
                ),
                "beneficial_condition_count": len(beneficial),
                "v1_beneficial_false_fallback_rate": (
                    sum(bool(row["v1_false_fallback"]) for row in beneficial) / len(beneficial)
                    if beneficial
                    else None
                ),
                "v2_beneficial_false_fallback_rate": (
                    sum(bool(row["v2_false_fallback"]) for row in beneficial) / len(beneficial)
                    if beneficial
                    else None
                ),
            }
        )

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
    v1_rates = [
        float(summary["v1_beneficial_false_fallback_rate"])
        for summary in summaries
        if summary["v1_beneficial_false_fallback_rate"] is not None
    ]
    v2_rates = [
        float(summary["v2_beneficial_false_fallback_rate"])
        for summary in summaries
        if summary["v2_beneficial_false_fallback_rate"] is not None
    ]
    v1_mean = sum(v1_rates) / len(v1_rates) if v1_rates else None
    v2_mean = sum(v2_rates) / len(v2_rates) if v2_rates else None
    safety_status = (
        "UNEVALUABLE"
        if not v1_rates
        else ("PASS" if v2_mean < v1_mean else "FAIL")
    )
    gate1 = "PASS" if timely_passes >= 4 else "FAIL"
    gate2 = "PASS" if recovered_passes >= 4 else "FAIL"
    verdict = (
        "FINAL_SUPPORT"
        if gate1 == "PASS" and gate2 == "PASS" and safety_status == "PASS"
        else (
            "PARTIAL_SUPPORT"
            if gate1 == "PASS" and gate2 == "PASS"
            else "NO_SUPPORT"
        )
    )
    return summaries, {
        "verdict": verdict,
        "gate1_harmful_timely_detection": {
            "result": gate1,
            "required_seed_passes": 4,
            "passing_seed_count": timely_passes,
        },
        "gate2_harmful_recovery": {
            "result": gate2,
            "required_seed_passes": 4,
            "passing_seed_count": recovered_passes,
        },
        "safety_improvement": {
            "result": safety_status,
            "evidence_seed_count": len(v1_rates),
            "v1_mean_seed_level_beneficial_false_fallback_rate": v1_mean,
            "v2_mean_seed_level_beneficial_false_fallback_rate": v2_mean,
        },
    }


def run_final(output_dir: Path, *, config: FinalRunConfig) -> dict[str, Any]:
    """Run exactly one selected v2 candidate per paired final condition."""

    selection = config.validate()
    provenance = _git_provenance(production=not config.test_mode)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selection_path = Path(config.selection_file)
    ssd = _base_ssd_config(config.logical_pages)
    run_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, Any]] = []
    hint_realizations: list[dict[str, Any]] = []

    for seed in config.seeds:
        classified = generate_classified_synthetic_prefix(
            SyntheticWorkloadConfig(
                total_writes=config.total_writes,
                logical_pages=config.logical_pages,
                seed=seed,
            ),
            boundary_writes=selection.lifetime_boundary_writes,
        )
        baseline = BaselineFTL(ssd)
        device = DeviceOnlyLifetimeFTL(
            ssd, boundary_writes=selection.lifetime_boundary_writes
        )
        run_page_write_events(baseline, classified)
        run_page_write_events(device, classified)
        baseline_row = _run_row(
            baseline, policy="baseline_zero_information", seed=seed, error_rate=None,
            selection=selection, hint_identifier=None,
        )
        device_row = _run_row(
            device, policy="device_only_lifetime", seed=seed, error_rate=None,
            selection=selection, hint_identifier=None,
        )
        run_rows.extend((baseline_row, device_row))

        hint_seed = derive_hint_seed(config.hint_seed, seed)
        for error_rate in config.error_rates:
            events = inject_nested_random_hint_errors(
                classified, error_rate=error_rate, seed=hint_seed
            )
            hint_identifier = _event_identifier(events)
            hint_realizations.append(
                {
                    "workload_seed": seed,
                    "error_rate": error_rate,
                    "hint_seed": hint_seed,
                    "hint_realization_id": hint_identifier,
                }
            )
            hint_phi = absolute_hint_phi(events)
            static = StaticHostGuidedFTL(
                ssd, boundary_writes=selection.lifetime_boundary_writes
            )
            v1 = TrustFallbackFTL(
                ssd,
                boundary_writes=selection.lifetime_boundary_writes,
                failure_association_threshold=selection.failure_association_threshold,
                detector_window=V1_DETECTOR_WINDOW,
                detector_min_samples=V1_DETECTOR_MIN_SAMPLES,
            )
            v2 = PersistenceFallbackFTL(
                ssd,
                boundary_writes=selection.lifetime_boundary_writes,
                failure_association_threshold=selection.failure_association_threshold,
                block_size=selection.candidate.block_size,
                persistence_k=selection.candidate.persistence_k,
                min_marginal_support=selection.min_marginal_support,
            )
            for policy, ftl in (
                ("static_host_guided", static),
                ("safe_fallback_v1", v1),
            ):
                run_page_write_events(ftl, events)
                row = _run_row(
                    ftl, policy=policy, seed=seed, error_rate=error_rate,
                    selection=selection, hint_identifier=hint_identifier,
                )
                row["offline_hint_absolute_phi"] = hint_phi
                run_rows.append(row)
            run_page_write_events(v2, events)
            v2_row = persistence_v2_run_row(
                ftl=v2, seed=seed, error_rate=error_rate,
                boundary_writes=selection.lifetime_boundary_writes,
                failure_association_threshold=selection.failure_association_threshold,
            )
            v2_row.update(
                {
                    "hint_realization_id": hint_identifier,
                    "hint_seed": hint_seed,
                    "offline_hint_absolute_phi": hint_phi,
                }
            )
            run_rows.append(v2_row)
            for block_row in persistence_v2_block_rows(
                ftl=v2, workload_seed=seed, error_rate=error_rate
            ):
                block_row["hint_realization_id"] = hint_identifier
                block_rows.append(block_row)
            outcome_rows.append(
                _condition_outcome(
                    seed=seed, error_rate=error_rate, static=run_rows[-3],
                    device=device_row, v1=run_rows[-2], v2=v2_row,
                    hint_identifier=hint_identifier, total_writes=config.total_writes,
                )
            )

    seed_summaries, gate_summary = summarize_final_outcomes(
        outcome_rows, expected_seeds=config.seeds
    )
    save_experiment_rows(run_rows, output_dir / "final_run_results.csv")
    if block_rows:
        save_experiment_rows(block_rows, output_dir / "final_block_results.csv")
    else:
        (output_dir / "final_block_results.csv").write_text("\n", encoding="utf-8")
    save_experiment_rows(outcome_rows, output_dir / "final_condition_outcomes.csv")
    save_experiment_rows(seed_summaries, output_dir / "final_seed_summaries.csv")
    summary = {
        **gate_summary,
        "selected_block_size": selection.candidate.block_size,
        "selected_persistence_k": selection.candidate.persistence_k,
        "frozen_constants": {
            "lifetime_boundary_writes": selection.lifetime_boundary_writes,
            "failure_association_threshold": selection.failure_association_threshold,
            "min_marginal_support": selection.min_marginal_support,
        },
        "final_seeds": list(config.seeds),
        "error_rates": list(config.error_rates),
        "harmful_timely_detection_seed_summaries": [
            {
                "workload_seed": row["workload_seed"],
                "harmful_condition_count": row["harmful_condition_count"],
                "harmful_timely_detection_rate": row["harmful_timely_detection_rate"],
            }
            for row in seed_summaries
        ],
        "harmful_recovered_harm_seed_summaries": [
            {
                "workload_seed": row["workload_seed"],
                "harmful_condition_count": row["harmful_condition_count"],
                "harmful_median_recovered_harm": row["harmful_median_recovered_harm"],
            }
            for row in seed_summaries
        ],
        "secondary_tradeoff_fields": [
            "waf", "nand_write_pages", "valid_page_migrations", "gc_count",
            "wear_imbalance_cv",
        ],
    }
    _write_json(output_dir / "final_summary.json", summary)
    manifest = {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "stage": config.stage,
        **provenance,
        "frozen_selection_file": str(selection_path),
        "frozen_selection_sha256": _selection_sha256(selection_path),
        "selected_block_size": selection.candidate.block_size,
        "selected_persistence_k": selection.candidate.persistence_k,
        "workload_seeds": list(config.seeds),
        "error_rates": list(config.error_rates),
        "total_writes": config.total_writes,
        "logical_pages": config.logical_pages,
        "hint_seed_base": config.hint_seed,
        "hint_seed_derivation_scheme": "derive_hint_seed(base_hint_seed, workload_seed)",
        "hint_realizations": hint_realizations,
        "ssd_config": ssd.to_dict(),
        "baseline_cache_scheme": {
            "baseline_zero_information": "once per workload seed",
            "device_only_lifetime": "once per workload seed",
            "static_host_guided": "once per (workload seed, error rate)",
            "safe_fallback_v1": "once per (workload seed, error rate)",
            "persistence_fallback_v2": "once per (workload seed, error rate)",
        },
    }
    _write_json(output_dir / "final_manifest.json", manifest)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen Safe-Fallback v2 final holdout matrix."
    )
    parser.add_argument("--stage", required=True, choices=("final",))
    parser.add_argument("--selection-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_final(
        args.output_dir,
        config=FinalRunConfig(selection_file=args.selection_file, stage=args.stage),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
