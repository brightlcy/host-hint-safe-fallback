from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from .lifetime_workload import LifetimeClass


@dataclass
class _ActiveVersion:
    lpn: int
    birth_sequence: int
    host_hint: LifetimeClass
    resolved_class: Optional[LifetimeClass] = None


@dataclass(frozen=True)
class LifetimeClassResolution:
    """A version whose SHORT/LONG class has become knowable online."""

    lpn: int
    birth_sequence: int
    resolved_sequence: int
    lifetime_class: LifetimeClass
    host_hint: LifetimeClass
    exact_lifetime_writes: Optional[int]
    resolution_reason: str

    @property
    def hint_mismatch(self) -> Optional[bool]:
        if self.host_hint not in {LifetimeClass.SHORT, LifetimeClass.LONG}:
            return None
        return self.host_hint is not self.lifetime_class


@dataclass(frozen=True)
class LifetimeCompletion:
    lpn: int
    birth_sequence: int
    death_sequence: int
    exact_lifetime_writes: int
    lifetime_class: LifetimeClass


class OnlineLifetimeTracker:
    """Resolve binary lifetime classes using only host writes observed so far.

    A version born at write sequence b is SHORT when the next overwrite occurs
    within `boundary_writes`, otherwise LONG. LONG becomes knowable before the
    eventual overwrite as soon as the version survives past the boundary.
    """

    def __init__(self, *, boundary_writes: int) -> None:
        if boundary_writes < 1:
            raise ValueError("boundary_writes must be >= 1")
        self.boundary_writes = boundary_writes
        self.sequence = 0
        self._active: Dict[int, _ActiveVersion] = {}
        self._long_deadlines: list[tuple[int, int, int]] = []
        self._last_resolved_by_lpn: Dict[int, LifetimeClass] = {}
        self.resolution_count = 0
        self.completion_count = 0

    def _resolve_due_long(self) -> List[LifetimeClassResolution]:
        resolutions: List[LifetimeClassResolution] = []
        while self._long_deadlines and self._long_deadlines[0][0] <= self.sequence:
            _, birth_sequence, lpn = heapq.heappop(self._long_deadlines)
            active = self._active.get(lpn)
            if active is None or active.birth_sequence != birth_sequence:
                continue
            if active.resolved_class is not None:
                continue
            active.resolved_class = LifetimeClass.LONG
            self._last_resolved_by_lpn[lpn] = LifetimeClass.LONG
            self.resolution_count += 1
            resolutions.append(
                LifetimeClassResolution(
                    lpn=lpn,
                    birth_sequence=birth_sequence,
                    resolved_sequence=self.sequence,
                    lifetime_class=LifetimeClass.LONG,
                    host_hint=active.host_hint,
                    exact_lifetime_writes=None,
                    resolution_reason="survived_boundary",
                )
            )
        return resolutions

    def observe_write(
        self, lpn: int, *, host_hint: LifetimeClass = LifetimeClass.UNKNOWN
    ) -> tuple[List[LifetimeClassResolution], Optional[LifetimeCompletion]]:
        if lpn < 0:
            raise ValueError(f"LPN must be >= 0: {lpn}")
        if not isinstance(host_hint, LifetimeClass):
            raise TypeError("host_hint must be a LifetimeClass")

        self.sequence += 1
        resolutions = self._resolve_due_long()
        completion: Optional[LifetimeCompletion] = None

        previous = self._active.get(lpn)
        if previous is not None:
            lifetime = self.sequence - previous.birth_sequence
            true_class = (
                LifetimeClass.SHORT
                if lifetime <= self.boundary_writes
                else LifetimeClass.LONG
            )
            if previous.resolved_class is None:
                previous.resolved_class = true_class
                self._last_resolved_by_lpn[lpn] = true_class
                self.resolution_count += 1
                resolutions.append(
                    LifetimeClassResolution(
                        lpn=lpn,
                        birth_sequence=previous.birth_sequence,
                        resolved_sequence=self.sequence,
                        lifetime_class=true_class,
                        host_hint=previous.host_hint,
                        exact_lifetime_writes=lifetime,
                        resolution_reason="overwrite",
                    )
                )
            elif previous.resolved_class is not true_class:
                raise AssertionError("online lifetime resolution contradicted completion")

            self.completion_count += 1
            completion = LifetimeCompletion(
                lpn=lpn,
                birth_sequence=previous.birth_sequence,
                death_sequence=self.sequence,
                exact_lifetime_writes=lifetime,
                lifetime_class=true_class,
            )

        current = _ActiveVersion(
            lpn=lpn,
            birth_sequence=self.sequence,
            host_hint=host_hint,
        )
        self._active[lpn] = current
        deadline = self.sequence + self.boundary_writes + 1
        heapq.heappush(self._long_deadlines, (deadline, self.sequence, lpn))
        return resolutions, completion

    def predict_next_class(self, lpn: int) -> LifetimeClass:
        """Simple device-only baseline: repeat the LPN's last resolved class.

        New/unseen LPNs default to LONG. This deliberately avoids an additional
        predictor parameter and makes first-write behavior explicit.
        """

        if lpn < 0:
            raise ValueError(f"LPN must be >= 0: {lpn}")
        return self._last_resolved_by_lpn.get(lpn, LifetimeClass.LONG)

    @property
    def active_version_count(self) -> int:
        return len(self._active)


@dataclass(frozen=True)
class MismatchDetectorSnapshot:
    sample_count: int
    mismatch_rate: float
    wilson_lower_bound: float
    triggered: bool
    trigger_sequence: Optional[int]


class RollingMismatchDetector:
    """One-way conservative detector for Host Hint failure-boundary crossing."""

    def __init__(
        self,
        *,
        failure_boundary: Optional[float],
        window_size: int = 128,
        min_samples: int = 30,
        z_score: float = 1.959963984540054,
    ) -> None:
        if failure_boundary is not None and not 0.0 <= failure_boundary <= 1.0:
            raise ValueError("failure_boundary must be between 0 and 1")
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        if min_samples < 1 or min_samples > window_size:
            raise ValueError("min_samples must be in [1, window_size]")
        if z_score <= 0:
            raise ValueError("z_score must be > 0")
        self.failure_boundary = failure_boundary
        self.window_size = window_size
        self.min_samples = min_samples
        self.z_score = z_score
        self._outcomes: Deque[int] = deque(maxlen=window_size)
        self.triggered = False
        self.trigger_sequence: Optional[int] = None

    def observe_resolution(self, resolution: LifetimeClassResolution) -> bool:
        mismatch = resolution.hint_mismatch
        if mismatch is None:
            return self.triggered
        self._outcomes.append(1 if mismatch else 0)
        if (
            not self.triggered
            and self.failure_boundary is not None
            and len(self._outcomes) >= self.min_samples
            and self.wilson_lower_bound() > self.failure_boundary
        ):
            self.triggered = True
            self.trigger_sequence = resolution.resolved_sequence
        return self.triggered

    def mismatch_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(self._outcomes) / len(self._outcomes)

    def wilson_lower_bound(self) -> float:
        n = len(self._outcomes)
        if n == 0:
            return 0.0
        phat = self.mismatch_rate()
        z = self.z_score
        z2 = z * z
        denominator = 1.0 + z2 / n
        center = phat + z2 / (2.0 * n)
        margin = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n)
        return max(0.0, (center - margin) / denominator)

    def snapshot(self) -> MismatchDetectorSnapshot:
        return MismatchDetectorSnapshot(
            sample_count=len(self._outcomes),
            mismatch_rate=self.mismatch_rate(),
            wilson_lower_bound=self.wilson_lower_bound(),
            triggered=self.triggered,
            trigger_sequence=self.trigger_sequence,
        )

@dataclass(frozen=True)
class AssociationDetectorSnapshot:
    """Detector state at one observation point.

    ``sample_count`` is the current rolling-window count at snapshot time.
    ``trigger_sample_count`` is frozen at the first fallback trigger and stays
    ``None`` when no trigger has occurred.
    """

    sample_count: int
    phi_correlation: float
    absolute_phi: float
    fisher_abs_upper_bound: float
    triggered: bool
    trigger_sequence: Optional[int]
    trigger_sample_count: Optional[int]


class RollingHintAssociationDetector:
    """Detect loss of lifetime-separation power, independent of label polarity.

    SHORT/LONG group names are semantically arbitrary when the two placement
    groups have symmetric resources. A perfectly inverted hint therefore still
    separates lifetimes well. We monitor |phi| between Host Hint group and the
    online-resolved lifetime class instead of raw semantic mismatch rate. The
    Fisher interval is an approximate confidence bound for binary phi.
    """

    @staticmethod
    def minimum_fisher_abs_upper_bound(
        *, window_size: int, z_score: float = 1.959963984540054
    ) -> float:
        """Return the smallest approximate upper bound attainable at phi=0.

        With both binary variables varying and a full window, the Fisher
        approximation reduces to ``tanh(z / sqrt(n - 3))`` at zero
        association. A detector that requires a strictly smaller value than
        its threshold can never trigger when that threshold is at or below
        this bound.
        """

        if window_size < 4:
            raise ValueError("window_size must be >= 4 for Fisher interval")
        if z_score <= 0:
            raise ValueError("z_score must be > 0")
        return math.tanh(z_score / math.sqrt(window_size - 3))

    def __init__(
        self,
        *,
        failure_association_threshold: Optional[float],
        window_size: int = 128,
        min_samples: int = 30,
        z_score: float = 1.959963984540054,
    ) -> None:
        if failure_association_threshold is not None and not (
            0.0 <= failure_association_threshold <= 1.0
        ):
            raise ValueError("failure_association_threshold must be in [0, 1]")
        if window_size < 4:
            raise ValueError("window_size must be >= 4 for Fisher interval")
        if min_samples < 4 or min_samples > window_size:
            raise ValueError("min_samples must be in [4, window_size]")
        if z_score <= 0:
            raise ValueError("z_score must be > 0")
        if failure_association_threshold is not None:
            minimum_upper_bound = self.minimum_fisher_abs_upper_bound(
                window_size=window_size,
                z_score=z_score,
            )
            if failure_association_threshold <= minimum_upper_bound:
                raise ValueError(
                    "failure_association_threshold is not detectable with "
                    f"window_size={window_size}: threshold="
                    f"{failure_association_threshold:.12g}, minimum "
                    f"Fisher absolute upper bound={minimum_upper_bound:.12g}"
                )
        self.failure_association_threshold = failure_association_threshold
        self.window_size = window_size
        self.min_samples = min_samples
        self.z_score = z_score
        self._pairs: Deque[tuple[int, int]] = deque(maxlen=window_size)
        self.triggered = False
        self.trigger_sequence: Optional[int] = None
        self.trigger_sample_count: Optional[int] = None

    def observe_resolution(self, resolution: LifetimeClassResolution) -> bool:
        if resolution.host_hint not in {LifetimeClass.SHORT, LifetimeClass.LONG}:
            return self.triggered
        true_bit = 1 if resolution.lifetime_class is LifetimeClass.SHORT else 0
        hint_bit = 1 if resolution.host_hint is LifetimeClass.SHORT else 0
        self._pairs.append((true_bit, hint_bit))
        if (
            not self.triggered
            and self.failure_association_threshold is not None
            and len(self._pairs) >= self.min_samples
            and self.fisher_abs_upper_bound() < self.failure_association_threshold
        ):
            self.triggered = True
            self.trigger_sequence = resolution.resolved_sequence
            self.trigger_sample_count = len(self._pairs)
        return self.triggered

    def phi_correlation(self) -> float:
        if not self._pairs:
            return 0.0
        n11 = n10 = n01 = n00 = 0
        for true_bit, hint_bit in self._pairs:
            if true_bit == 1 and hint_bit == 1:
                n11 += 1
            elif true_bit == 1 and hint_bit == 0:
                n10 += 1
            elif true_bit == 0 and hint_bit == 1:
                n01 += 1
            else:
                n00 += 1
        denominator = math.sqrt(
            (n11 + n01) * (n11 + n10) * (n00 + n01) * (n00 + n10)
        )
        if denominator == 0.0:
            return 0.0
        return (n11 * n00 - n01 * n10) / denominator

    def fisher_abs_upper_bound(self) -> float:
        """Approximate 95% upper confidence bound for |phi|.

        When either binary variable has no variation, association is not
        identifiable from the current window; return 1.0 so the detector fails
        closed instead of falling back without evidence.
        """

        n = len(self._pairs)
        if n < 4:
            return 1.0
        true_values = {pair[0] for pair in self._pairs}
        hint_values = {pair[1] for pair in self._pairs}
        if len(true_values) < 2 or len(hint_values) < 2:
            return 1.0
        r = max(-0.999999, min(0.999999, self.phi_correlation()))
        fisher_z = math.atanh(r)
        margin = self.z_score / math.sqrt(n - 3)
        lower = math.tanh(fisher_z - margin)
        upper = math.tanh(fisher_z + margin)
        return min(1.0, max(abs(lower), abs(upper)))

    def snapshot(self) -> AssociationDetectorSnapshot:
        phi = self.phi_correlation()
        return AssociationDetectorSnapshot(
            sample_count=len(self._pairs),
            phi_correlation=phi,
            absolute_phi=abs(phi),
            fisher_abs_upper_bound=self.fisher_abs_upper_bound(),
            triggered=self.triggered,
            trigger_sequence=self.trigger_sequence,
            trigger_sample_count=self.trigger_sample_count,
        )
