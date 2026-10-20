"""Execute only the frozen Safe-Fallback v2 development matrix.

This runner deliberately has no final stage.  It consumes the frozen protocol
helpers but never writes a final-selection configuration: human review is
required before any held-out execution is authorized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
from .baseline_ftl import BaselineFTL
from .persistence_protocol import (
    DEVELOPMENT_SEEDS,
    FROZEN_FAILURE_ASSOCIATION_THRESHOLD,
    FROZEN_LIFETIME_BOUNDARY_WRITES,
    FROZEN_MIN_MARGINAL_SUPPORT,
    V2_BLOCK_SIZES,
    V2_PERSISTENCE_VALUES,
    PersistenceCandidate,
    _candidate_selection_metrics,
    candidate_is_eligible,
    core_measurement_error_rates,
    development_profile,
    select_persistence_candidate,
    v2_condition_outcome,
)


FROZEN_PROTOCOL_TAG = "safe-fallback-v2-protocol-frozen-20260820"
OUTPUT_SCHEMA_VERSION = "safe-fallback-v2-development-runner-v1"
DEFAULT_HINT_SEED = 42
CORE_TOTAL_WRITES = 50_000
CORE_LOGICAL_PAGES = 2_048
V1_DETECTOR_WINDOW = 128
V1_DETECTOR_MIN_SAMPLES = 30


def candidate_grid() -> tuple[PersistenceCandidate, ...]:
    return tuple(
        PersistenceCandidate(block_size, persistence_k)
        for block_size in V2_BLOCK_SIZES
        for persistence_k in V2_PERSISTENCE_VALUES
    )


@dataclass(frozen=True)
class DevelopmentRunConfig:
    """Immutable execution inputs; test_mode only permits smaller test work."""

    stage: str = "development"
    seeds: tuple[int, ...] = DEVELOPMENT_SEEDS
    error_rates: tuple[float, ...] = core_measurement_error_rates()
    candidates: tuple[PersistenceCandidate, ...] = candidate_grid()
    total_writes: int = CORE_TOTAL_WRITES
    logical_pages: int = CORE_LOGICAL_PAGES
    hint_seed: int = DEFAULT_HINT_SEED
    lifetime_boundary_writes: int = FROZEN_LIFETIME_BOUNDARY_WRITES
    failure_association_threshold: float = FROZEN_FAILURE_ASSOCIATION_THRESHOLD
    min_marginal_support: int = FROZEN_MIN_MARGINAL_SUPPORT
    test_mode: bool = False

    def validate(self) -> None:
        profile = development_profile()
        if self.stage != "development":
            raise ValueError("only the explicit development stage is supported")
        if tuple(self.seeds) != profile.seeds:
            raise ValueError("development runner requires exactly the frozen development seeds")
        if self.lifetime_boundary_writes != FROZEN_LIFETIME_BOUNDARY_WRITES:
            raise ValueError("development runner cannot change frozen lifetime boundary")
        if self.failure_association_threshold != FROZEN_FAILURE_ASSOCIATION_THRESHOLD:
            raise ValueError("development runner cannot change frozen association threshold")
        if self.min_marginal_support != FROZEN_MIN_MARGINAL_SUPPORT:
            raise ValueError("development runner cannot change frozen marginal support")
        if tuple(self.candidates) != candidate_grid():
            raise ValueError("development runner requires the frozen B/K candidate grid")
        if not self.test_mode:
            if tuple(self.error_rates) != profile.measurement_error_rates:
                raise ValueError("development runner requires the frozen measurement grid")
            if self.total_writes != CORE_TOTAL_WRITES:
                raise ValueError("development runner requires the core 50k write count")
            if self.logical_pages != CORE_LOGICAL_PAGES:
                raise ValueError("development runner requires the core logical-page count")
        elif not self.error_rates:
            raise ValueError("test development runs require at least one error rate")


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
        raise RuntimeError("development experiment requires git provenance") from exc


def _git_provenance(*, production: bool) -> dict[str, Any]:
    """Collect provenance and enforce the production-only clean-tree gate."""

    head = _git_required_value(
        ("rev-parse", "HEAD"),
        failure_message="development experiment requires a git HEAD commit",
    )
    frozen_protocol_commit = _git_required_value(
        ("rev-parse", "--verify", f"{FROZEN_PROTOCOL_TAG}^{{commit}}"),
        failure_message=(
            "development experiment requires frozen protocol tag "
            f"{FROZEN_PROTOCOL_TAG}"
        ),
    )
    worktree_clean = _git_worktree_clean()
    if production and not worktree_clean:
        raise ValueError("development experiment requires a clean git worktree")
    return {
        "git_commit_hash": head,
        "git_worktree_clean": worktree_clean,
        "frozen_protocol_tag": FROZEN_PROTOCOL_TAG,
        "frozen_protocol_commit": frozen_protocol_commit,
    }


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


def _baseline_row(
    ftl: BaselineFTL,
    *,
    policy: str,
    seed: int,
    error_rate: float | None,
    config: DevelopmentRunConfig,
    hint_identifier: str | None,
) -> dict[str, Any]:
    row = _row_from_ftl(
        ftl=ftl,
        policy=policy,
        seed=seed,
        error_rate=error_rate,
        boundary_writes=config.lifetime_boundary_writes,
        failure_error_rate=None,
        failure_association_threshold=config.failure_association_threshold,
        hint_phi=None,
    )
    row["hint_realization_id"] = hint_identifier
    return row


def run_development(
    output_dir: Path,
    *,
    config: DevelopmentRunConfig | None = None,
) -> dict[str, Any]:
    """Run the frozen development matrix and write auditable artifacts.

    ``config`` exists for small deterministic unit tests.  The CLI always uses
    the production frozen configuration, and no call creates a final config.
    """

    config = config or DevelopmentRunConfig()
    config.validate()
    git_provenance = _git_provenance(production=not config.test_mode)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ssd = _base_ssd_config(config.logical_pages)
    run_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, Any]] = []
    hint_realizations: dict[tuple[int, float], dict[str, Any]] = {}

    for seed in config.seeds:
        classified = generate_classified_synthetic_prefix(
            SyntheticWorkloadConfig(
                total_writes=config.total_writes,
                logical_pages=config.logical_pages,
                seed=seed,
            ),
            boundary_writes=config.lifetime_boundary_writes,
        )

        # Error-independent baselines are intentionally run once per workload.
        baseline = BaselineFTL(ssd)
        device_only = DeviceOnlyLifetimeFTL(
            ssd, boundary_writes=config.lifetime_boundary_writes
        )
        run_page_write_events(baseline, classified)
        run_page_write_events(device_only, classified)
        run_rows.extend(
            (
                _baseline_row(
                    baseline,
                    policy="baseline_zero_information",
                    seed=seed,
                    error_rate=None,
                    config=config,
                    hint_identifier=None,
                ),
                _baseline_row(
                    device_only,
                    policy="device_only_lifetime",
                    seed=seed,
                    error_rate=None,
                    config=config,
                    hint_identifier=None,
                ),
            )
        )
        device_waf = float(device_only.get_summary()["write_amplification_factor"])

        hint_seed = derive_hint_seed(config.hint_seed, seed)
        for error_rate in config.error_rates:
            events = inject_nested_random_hint_errors(
                classified, error_rate=error_rate, seed=hint_seed
            )
            hint_identifier = _event_identifier(events)
            # Preserve only provenance metadata.  The events object is shared
            # within this condition and becomes collectible after this loop
            # iteration, rather than retaining every 50k-event realization.
            hint_realizations[(seed, error_rate)] = {
                "hint_realization_id": hint_identifier,
                "hint_seed": hint_seed,
            }
            hint_phi = absolute_hint_phi(events)

            static = StaticHostGuidedFTL(
                ssd, boundary_writes=config.lifetime_boundary_writes
            )
            v1 = TrustFallbackFTL(
                ssd,
                boundary_writes=config.lifetime_boundary_writes,
                failure_association_threshold=config.failure_association_threshold,
                detector_window=V1_DETECTOR_WINDOW,
                detector_min_samples=V1_DETECTOR_MIN_SAMPLES,
            )
            for policy, ftl in (("static_host_guided", static), ("safe_fallback", v1)):
                run_page_write_events(ftl, events)
                row = _baseline_row(
                    ftl,
                    policy=policy,
                    seed=seed,
                    error_rate=error_rate,
                    config=config,
                    hint_identifier=hint_identifier,
                )
                row["offline_hint_absolute_phi"] = hint_phi
                run_rows.append(row)
            static_waf = float(static.get_summary()["write_amplification_factor"])

            for candidate in config.candidates:
                # Each candidate condition owns a fresh FTL and detector state.
                v2 = PersistenceFallbackFTL(
                    ssd,
                    boundary_writes=config.lifetime_boundary_writes,
                    failure_association_threshold=config.failure_association_threshold,
                    block_size=candidate.block_size,
                    persistence_k=candidate.persistence_k,
                    min_marginal_support=config.min_marginal_support,
                )
                run_page_write_events(v2, events)
                v2_row = persistence_v2_run_row(
                    ftl=v2,
                    seed=seed,
                    error_rate=error_rate,
                    boundary_writes=config.lifetime_boundary_writes,
                    failure_association_threshold=config.failure_association_threshold,
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
                outcome = v2_condition_outcome(
                    workload_seed=seed,
                    error_rate=error_rate,
                    candidate=candidate,
                    static_waf=static_waf,
                    device_waf=device_waf,
                    v2_waf=float(v2.get_summary()["write_amplification_factor"]),
                    fallback_triggered=bool(v2.get_summary()["fallback_triggered"]),
                    trigger_host_write_index=v2.get_summary()["trigger_host_write_index"],
                    total_host_writes=config.total_writes,
                )
                outcome["hint_realization_id"] = hint_identifier
                outcome_rows.append(outcome)

    selection = select_persistence_candidate(
        outcome_rows, candidates=config.candidates, expected_seeds=config.seeds
    )
    candidate_eligibility = {
        key: candidate_is_eligible(summaries)
        for key, summaries in selection["candidate_seed_summaries"].items()
    }
    # The frozen selector returns ranking metrics when at least one candidate
    # qualifies.  For an all-NO_GO development run, expose the same frozen
    # metric helper for review without recreating the ranking rule here.
    if "candidate_ranking_metrics" not in selection:
        selection["candidate_ranking_metrics"] = {
            key: {
                "mean_seed_beneficial_false_fallback_rate": _candidate_selection_metrics(
                    summaries
                )[0],
                "harmful_median_timely_detection_rate": _candidate_selection_metrics(
                    summaries
                )[1],
                "harmful_median_recovered_harm": _candidate_selection_metrics(
                    summaries
                )[2],
                "harmful_median_detection_delay": _candidate_selection_metrics(
                    summaries
                )[3],
            }
            for key, summaries in selection["candidate_seed_summaries"].items()
        }
    seed_summaries = [
        summary
        for summaries in selection["candidate_seed_summaries"].values()
        for summary in summaries
    ]
    save_experiment_rows(run_rows, output_dir / "development_run_results.csv")
    if block_rows:
        save_experiment_rows(block_rows, output_dir / "development_block_results.csv")
    else:
        # A header-only artifact remains explicit when a tiny test has no blocks.
        (output_dir / "development_block_results.csv").write_text("\n", encoding="utf-8")
    save_experiment_rows(outcome_rows, output_dir / "development_condition_outcomes.csv")
    save_experiment_rows(seed_summaries, output_dir / "development_seed_summaries.csv")

    candidate_summary = {
        **selection,
        "candidate_eligibility": candidate_eligibility,
        "frozen_constants": {
            "lifetime_boundary_writes": config.lifetime_boundary_writes,
            "failure_association_threshold": config.failure_association_threshold,
            "min_marginal_support": config.min_marginal_support,
        },
        "candidate_grid": [
            {"block_size": item.block_size, "persistence_k": item.persistence_k}
            for item in config.candidates
        ],
        "development_seeds": list(config.seeds),
        "error_rates": list(config.error_rates),
        "total_writes": config.total_writes,
        "logical_pages": config.logical_pages,
    }
    _write_json(output_dir / "development_candidate_summary.json", candidate_summary)
    manifest = {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "stage": config.stage,
        "git_commit_hash": git_provenance["git_commit_hash"],
        "git_branch": _git_value(("branch", "--show-current")),
        "git_worktree_clean": git_provenance["git_worktree_clean"],
        "frozen_protocol_tag": git_provenance["frozen_protocol_tag"],
        "frozen_protocol_commit": git_provenance["frozen_protocol_commit"],
        "workload_seeds": list(config.seeds),
        "hint_seed_base": config.hint_seed,
        "hint_seed_derivation_scheme": "derive_hint_seed(base_hint_seed, workload_seed)",
        "hint_realizations": [
            {
                "workload_seed": seed,
                "error_rate": error_rate,
                "hint_seed": realization["hint_seed"],
                "hint_realization_id": realization["hint_realization_id"],
            }
            for (seed, error_rate), realization in sorted(hint_realizations.items())
        ],
        "ssd_config": ssd.to_dict(),
        "lifetime_boundary_writes": config.lifetime_boundary_writes,
        "failure_association_threshold": config.failure_association_threshold,
        "min_marginal_support": config.min_marginal_support,
        "candidate_grid": candidate_summary["candidate_grid"],
        "development_seeds": list(config.seeds),
        "error_rates": list(config.error_rates),
        "total_writes": config.total_writes,
        "logical_pages": config.logical_pages,
        "baseline_cache_scheme": {
            "baseline_zero_information": "once per workload seed",
            "device_only_lifetime": "once per workload seed",
            "static_host_guided": "once per (workload seed, error rate)",
            "safe_fallback_v1": "once per (workload seed, error rate)",
            "persistence_fallback_v2": "fresh instance per (seed, error rate, B, K)",
        },
    }
    _write_json(output_dir / "development_manifest.json", manifest)
    return candidate_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run only the frozen Safe-Fallback v2 development matrix."
    )
    parser.add_argument("--stage", required=True, choices=("development",))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results/safe_fallback_v2_development")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_development(args.output_dir, config=DevelopmentRunConfig(stage=args.stage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
