from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .lifetime_experiment import (
    PRIMARY_ERROR_RATES,
    SYMMETRY_ERROR_RATES,
    CalibrationResult,
    calibrate_synthetic_failure_boundary,
    run_synthetic_measurement,
    save_calibration_audit_rows,
    save_experiment_rows,
)


def _parse_int_list(value: str) -> tuple[int, ...]:
    items = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("expected at least one integer")
    return items


def _parse_float_list(value: str) -> tuple[float, ...]:
    items = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("expected at least one float")
    if any(item < 0.0 or item > 1.0 for item in items):
        raise argparse.ArgumentTypeError("error rates must be in [0, 1]")
    return items


def _profile_defaults(profile: str) -> dict[str, object]:
    if profile == "quick":
        return {
            "calibration_seeds": (1, 2),
            "measurement_seeds": (101, 102),
            "total_writes": 3_000,
            "logical_pages": 256,
            "calibration_error_rates": (0.0, 0.2, 0.4, 0.5),
            "measurement_error_rates": (0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0),
            "detector_window": 128,
        }
    if profile == "core":
        return {
            "calibration_seeds": (1, 2, 3),
            "measurement_seeds": (101, 102, 103, 104, 105),
            "total_writes": 50_000,
            "logical_pages": 2_048,
            "calibration_error_rates": PRIMARY_ERROR_RATES,
            "measurement_error_rates": PRIMARY_ERROR_RATES + SYMMETRY_ERROR_RATES,
            "detector_window": 128,
        }
    raise ValueError(f"unknown profile: {profile}")


def _write_calibration_summary(
    calibration: CalibrationResult,
    *,
    path: Path,
    calibration_seeds: Sequence[int],
    total_writes: int,
    logical_pages: int,
    hint_seed: int,
    calibration_error_rates: Sequence[float],
) -> None:
    payload = {
        "lifetime_boundary_writes": calibration.lifetime_boundary_writes,
        "failure_error_rate": calibration.failure_error_rate,
        "failure_association_threshold": calibration.failure_association_threshold,
        "calibration_seeds": list(calibration_seeds),
        "total_writes": total_writes,
        "logical_pages": logical_pages,
        "hint_seed": hint_seed,
        "calibration_error_rates": list(calibration_error_rates),
        "calibration_points": calibration.calibration_points,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_calibration(path: Path) -> CalibrationResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return CalibrationResult(
        lifetime_boundary_writes=int(payload["lifetime_boundary_writes"]),
        failure_error_rate=(
            None if payload.get("failure_error_rate") is None else float(payload["failure_error_rate"])
        ),
        failure_association_threshold=(
            None
            if payload.get("failure_association_threshold") is None
            else float(payload["failure_association_threshold"])
        ),
        calibration_points=list(payload.get("calibration_points", [])),
        rows=[],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the synthetic Host-Hint failure-boundary / safe-fallback experiment."
    )
    parser.add_argument("--stage", choices=("calibration", "measurement", "full"), default="full")
    parser.add_argument("--profile", choices=("quick", "core"), default="core")
    parser.add_argument("--output-dir", type=Path, default=Path("results/safe_fallback_core"))
    parser.add_argument("--calibration-summary", type=Path, default=None)
    parser.add_argument("--calibration-seeds", type=_parse_int_list, default=None)
    parser.add_argument("--measurement-seeds", type=_parse_int_list, default=None)
    parser.add_argument("--total-writes", type=int, default=None)
    parser.add_argument("--logical-pages", type=int, default=None)
    parser.add_argument("--hint-seed", type=int, default=42)
    parser.add_argument("--calibration-error-rates", type=_parse_float_list, default=None)
    parser.add_argument("--measurement-error-rates", type=_parse_float_list, default=None)
    parser.add_argument("--detector-window", type=int, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    defaults = _profile_defaults(args.profile)

    calibration_seeds = args.calibration_seeds or defaults["calibration_seeds"]
    measurement_seeds = args.measurement_seeds or defaults["measurement_seeds"]
    total_writes = args.total_writes or defaults["total_writes"]
    logical_pages = args.logical_pages or defaults["logical_pages"]
    calibration_error_rates = args.calibration_error_rates or defaults["calibration_error_rates"]
    measurement_error_rates = args.measurement_error_rates or defaults["measurement_error_rates"]
    detector_window = args.detector_window or defaults["detector_window"]

    if total_writes < 1 or logical_pages < 1:
        raise SystemExit("total-writes and logical-pages must be positive")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.calibration_summary or output_dir / "calibration_summary.json"

    calibration: CalibrationResult
    if args.stage in {"calibration", "full"}:
        print(
            "[calibration]",
            f"profile={args.profile}",
            f"seeds={list(calibration_seeds)}",
            f"writes={total_writes}",
            f"logical_pages={logical_pages}",
            flush=True,
        )
        calibration = calibrate_synthetic_failure_boundary(
            calibration_seeds=calibration_seeds,
            total_writes=total_writes,
            logical_pages=logical_pages,
            hint_seed=args.hint_seed,
            error_rates=calibration_error_rates,
        )
        save_experiment_rows(calibration.rows, output_dir / "calibration_results.csv")
        save_calibration_audit_rows(
            calibration, output_dir / "calibration_boundary_audit.csv"
        )
        _write_calibration_summary(
            calibration,
            path=summary_path,
            calibration_seeds=calibration_seeds,
            total_writes=total_writes,
            logical_pages=logical_pages,
            hint_seed=args.hint_seed,
            calibration_error_rates=calibration_error_rates,
        )
        print(
            "[calibration done]",
            f"lifetime_boundary={calibration.lifetime_boundary_writes}",
            f"failure_error_rate={calibration.failure_error_rate}",
            f"failure_association_threshold={calibration.failure_association_threshold}",
            f"summary={summary_path}",
            flush=True,
        )
        if args.stage == "calibration":
            return 0
    else:
        if not summary_path.exists():
            raise SystemExit(f"calibration summary not found: {summary_path}")
        calibration = _load_calibration(summary_path)
        print(
            "[measurement] loaded calibration",
            f"boundary={calibration.lifetime_boundary_writes}",
            f"association_threshold={calibration.failure_association_threshold}",
            flush=True,
        )

    print(
        "[measurement]",
        f"seeds={list(measurement_seeds)}",
        f"error_rates={list(measurement_error_rates)}",
        f"detector_window={detector_window}",
        flush=True,
    )
    rows = run_synthetic_measurement(
        calibration,
        measurement_seeds=measurement_seeds,
        total_writes=total_writes,
        logical_pages=logical_pages,
        hint_seed=args.hint_seed,
        error_rates=measurement_error_rates,
        detector_window=detector_window,
    )
    measurement_path = save_experiment_rows(rows, output_dir / "measurement_results.csv")
    run_summary = {
        "profile": args.profile,
        "stage": args.stage,
        "calibration_summary": str(summary_path),
        "measurement_results": str(measurement_path),
        "measurement_seeds": list(measurement_seeds),
        "measurement_error_rates": list(measurement_error_rates),
        "detector_window": detector_window,
        "row_count": len(rows),
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(run_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[measurement done] rows={len(rows)} results={measurement_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
