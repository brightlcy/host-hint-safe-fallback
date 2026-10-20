from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Iterable, List, Optional, Sequence


class LifetimeClass(str, Enum):
    SHORT = "short"
    LONG = "long"
    UNKNOWN = "unknown"

    def flipped(self) -> "LifetimeClass":
        if self is LifetimeClass.SHORT:
            return LifetimeClass.LONG
        if self is LifetimeClass.LONG:
            return LifetimeClass.SHORT
        return LifetimeClass.UNKNOWN


@dataclass(frozen=True)
class PageWriteEvent:
    """One host page-write version used by lifetime-placement experiments."""

    write_sequence: int
    timestamp: float
    lpn: int
    exact_lifetime_writes: Optional[int]
    residual_horizon_writes: int
    true_class: LifetimeClass = LifetimeClass.UNKNOWN
    host_hint: LifetimeClass = LifetimeClass.UNKNOWN


@dataclass(frozen=True)
class SyntheticWorkloadConfig:
    total_writes: int = 50_000
    logical_pages: int = 2_048
    short_pool_fraction: float = 0.20
    short_write_probability: float = 0.80
    phase_change_at: Optional[int] = None
    seed: int = 1
    timestamp_step_s: float = 0.0001

    def __post_init__(self) -> None:
        if self.total_writes <= 0:
            raise ValueError("total_writes must be > 0")
        if self.logical_pages < 2:
            raise ValueError("logical_pages must be >= 2")
        if not 0.0 < self.short_pool_fraction < 1.0:
            raise ValueError("short_pool_fraction must be between 0 and 1")
        if not 0.0 < self.short_write_probability < 1.0:
            raise ValueError("short_write_probability must be between 0 and 1")
        if self.phase_change_at is not None and not (
            1 <= self.phase_change_at < self.total_writes
        ):
            raise ValueError("phase_change_at must be inside the write sequence")
        if self.timestamp_step_s <= 0:
            raise ValueError("timestamp_step_s must be > 0")


def generate_synthetic_lpn_sequence(config: SyntheticWorkloadConfig) -> List[int]:
    """Generate a reproducible two-population page-write workload.

    Before an optional phase change, a small LPN pool receives most writes and a
    large pool receives the rest. After the phase change, the probabilities are
    swapped. Pool membership itself stays fixed, so phase change changes which
    data are short/long lived without changing the logical address space.
    """

    rng = random.Random(config.seed)
    short_pool_size = max(1, round(config.logical_pages * config.short_pool_fraction))
    short_pool_size = min(short_pool_size, config.logical_pages - 1)
    short_pool = list(range(short_pool_size))
    long_pool = list(range(short_pool_size, config.logical_pages))

    sequence: List[int] = []
    for zero_index in range(config.total_writes):
        write_sequence = zero_index + 1
        short_probability = config.short_write_probability
        if config.phase_change_at is not None and write_sequence > config.phase_change_at:
            short_probability = 1.0 - short_probability

        pool = short_pool if rng.random() < short_probability else long_pool
        sequence.append(pool[rng.randrange(len(pool))])
    return sequence


def annotate_exact_lifetimes(
    lpns: Sequence[int], *, timestamp_step_s: float = 0.0001
) -> List[PageWriteEvent]:
    """Attach exact next-overwrite distance where it is observable offline."""

    if timestamp_step_s <= 0:
        raise ValueError("timestamp_step_s must be > 0")
    next_sequence_by_lpn: dict[int, int] = {}
    reversed_rows: List[PageWriteEvent] = []
    end_sequence = len(lpns)

    for write_sequence in range(end_sequence, 0, -1):
        lpn = int(lpns[write_sequence - 1])
        if lpn < 0:
            raise ValueError(f"LPN must be >= 0: {lpn}")
        next_sequence = next_sequence_by_lpn.get(lpn)
        exact_lifetime = (
            None if next_sequence is None else next_sequence - write_sequence
        )
        reversed_rows.append(
            PageWriteEvent(
                write_sequence=write_sequence,
                timestamp=(write_sequence - 1) * timestamp_step_s,
                lpn=lpn,
                exact_lifetime_writes=exact_lifetime,
                residual_horizon_writes=end_sequence - write_sequence,
            )
        )
        next_sequence_by_lpn[lpn] = write_sequence

    reversed_rows.reverse()
    return reversed_rows


def calibration_median_boundary(events: Iterable[PageWriteEvent]) -> int:
    """Choose a non-performance-tuned SHORT/LONG boundary from calibration data.

    Only completed lifetimes are used. The boundary is the median host-write
    distance, rounded down to an integer because lifetimes are integer events.
    """

    observed = [
        event.exact_lifetime_writes
        for event in events
        if event.exact_lifetime_writes is not None
    ]
    if len(observed) < 30:
        raise ValueError(
            "at least 30 completed lifetimes are required for calibration: "
            f"found={len(observed)}"
        )
    if len(set(observed)) < 2:
        raise ValueError("calibration lifetimes have no usable variation")
    return max(1, int(median(observed)))


def classify_lifetimes(
    events: Iterable[PageWriteEvent], *, boundary_writes: int
) -> List[PageWriteEvent]:
    """Classify finite-horizon versions without pretending censored values are exact.

    A censored version can still be known LONG when the remaining observed
    horizon already exceeds the SHORT/LONG boundary. Otherwise its class remains
    UNKNOWN.
    """

    if boundary_writes < 1:
        raise ValueError("boundary_writes must be >= 1")

    classified: List[PageWriteEvent] = []
    for event in events:
        lifetime = event.exact_lifetime_writes
        if lifetime is not None:
            true_class = (
                LifetimeClass.SHORT
                if lifetime <= boundary_writes
                else LifetimeClass.LONG
            )
        elif event.residual_horizon_writes >= boundary_writes:
            true_class = LifetimeClass.LONG
        else:
            true_class = LifetimeClass.UNKNOWN

        classified.append(
            PageWriteEvent(
                write_sequence=event.write_sequence,
                timestamp=event.timestamp,
                lpn=event.lpn,
                exact_lifetime_writes=event.exact_lifetime_writes,
                residual_horizon_writes=event.residual_horizon_writes,
                true_class=true_class,
                host_hint=event.host_hint,
            )
        )
    return classified


def generate_classified_synthetic_prefix(
    config: SyntheticWorkloadConfig, *, boundary_writes: int
) -> List[PageWriteEvent]:
    """Label a replayable synthetic prefix using a label-only guard suffix.

    The suffix has exactly ``boundary_writes`` synthetic writes. It is used
    only to establish that every final prefix version either overwrites within
    the boundary or survives it. Only the original prefix is returned, so the
    suffix is never replayed to an FTL or included in measurement counters.
    """

    if boundary_writes < 1:
        raise ValueError("boundary_writes must be >= 1")
    guarded_sequence = generate_synthetic_lpn_sequence(
        SyntheticWorkloadConfig(
            total_writes=config.total_writes + boundary_writes,
            logical_pages=config.logical_pages,
            short_pool_fraction=config.short_pool_fraction,
            short_write_probability=config.short_write_probability,
            phase_change_at=config.phase_change_at,
            seed=config.seed,
            timestamp_step_s=config.timestamp_step_s,
        )
    )
    classified = classify_lifetimes(
        annotate_exact_lifetimes(
            guarded_sequence, timestamp_step_s=config.timestamp_step_s
        ),
        boundary_writes=boundary_writes,
    )[: config.total_writes]
    if any(event.true_class is LifetimeClass.UNKNOWN for event in classified):
        raise AssertionError("guard suffix must fully classify the replay prefix")
    return classified


def inject_nested_random_hint_errors(
    events: Sequence[PageWriteEvent],
    *,
    error_rate: float,
    seed: int,
) -> List[PageWriteEvent]:
    """Create an exact, reproducible binary Host Hint error level.

    The same seed induces one permutation of hintable write versions. Increasing
    error_rate flips a prefix of that permutation, so error sets are nested across
    a sweep (e.g. every 10% error is also present at 20%). UNKNOWN classes remain
    UNKNOWN and are excluded from the injected-error denominator.
    """

    if not 0.0 <= error_rate <= 1.0:
        raise ValueError("error_rate must be between 0 and 1")

    hintable_positions = [
        index
        for index, event in enumerate(events)
        if event.true_class in {LifetimeClass.SHORT, LifetimeClass.LONG}
    ]
    rng = random.Random(seed)
    rng.shuffle(hintable_positions)
    flip_count = round(error_rate * len(hintable_positions))
    flipped_positions = set(hintable_positions[:flip_count])

    output: List[PageWriteEvent] = []
    for index, event in enumerate(events):
        if event.true_class is LifetimeClass.UNKNOWN:
            hint = LifetimeClass.UNKNOWN
        elif index in flipped_positions:
            hint = event.true_class.flipped()
        else:
            hint = event.true_class
        output.append(
            PageWriteEvent(
                write_sequence=event.write_sequence,
                timestamp=event.timestamp,
                lpn=event.lpn,
                exact_lifetime_writes=event.exact_lifetime_writes,
                residual_horizon_writes=event.residual_horizon_writes,
                true_class=event.true_class,
                host_hint=hint,
            )
        )
    return output


def save_page_write_events(events: Sequence[PageWriteEvent], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "write_sequence",
                "timestamp",
                "lpn",
                "exact_lifetime_writes",
                "residual_horizon_writes",
                "true_class",
                "host_hint",
            ],
        )
        writer.writeheader()
        for event in events:
            writer.writerow(
                {
                    "write_sequence": event.write_sequence,
                    "timestamp": f"{event.timestamp:.9f}",
                    "lpn": event.lpn,
                    "exact_lifetime_writes": ""
                    if event.exact_lifetime_writes is None
                    else event.exact_lifetime_writes,
                    "residual_horizon_writes": event.residual_horizon_writes,
                    "true_class": event.true_class.value,
                    "host_hint": event.host_hint.value,
                }
            )
    return output_path
