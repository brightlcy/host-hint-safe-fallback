"""Persistence-aware association detector used only by Safe-Fallback v2.

This module deliberately does not alter the v1 rolling Fisher detector.  A
completed evidence block contains exactly ``block_size`` usable resolved
lifetime observations and no observation can appear in more than one block.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .lifetime_tracker import LifetimeClassResolution
from .lifetime_workload import LifetimeClass


GOOD = "GOOD"
BAD = "BAD"
INVALID = "INVALID"


@dataclass(frozen=True)
class PersistenceAssociationBlock:
    """One completed, non-overlapping association evidence block."""

    block_index: int
    first_detector_observation_ordinal: int
    last_detector_observation_ordinal: int
    block_end_host_write_index: int
    usable_sample_count: int
    ss_count: int
    sl_count: int
    ls_count: int
    ll_count: int
    true_short_count: int
    true_long_count: int
    hint_short_count: int
    hint_long_count: int
    absolute_phi: Optional[float]
    block_state: str
    invalid_reason: Optional[str]
    bad_streak_before: int
    bad_streak_after: int
    triggered_on_this_block: bool


@dataclass(frozen=True)
class PersistenceAssociationDetectorSnapshot:
    detector_version: str
    block_size: int
    persistence_k: int
    min_marginal_support: int
    failure_association_threshold: Optional[float]
    detector_observation_count: int
    completed_block_count: int
    good_block_count: int
    bad_block_count: int
    invalid_block_count: int
    partial_block_sample_count: int
    bad_streak: int
    triggered: bool
    trigger_block_index: Optional[int]
    trigger_detector_observation_ordinal: Optional[int]
    trigger_host_write_index: Optional[int]
    trigger_bad_streak: Optional[int]
    trigger_block_absolute_phi: Optional[float]


class PersistenceAssociationDetector:
    """Require persistent low raw-|phi| association evidence before fallback.

    Blocks are evidence units, not independent statistical samples.  Unknown
    hints do not contribute observations.  Invalid blocks reset the BAD streak
    so they cannot connect BAD evidence on either side.
    """

    detector_version = "persistence-association-v2"

    def __init__(
        self,
        *,
        failure_association_threshold: Optional[float],
        block_size: int,
        persistence_k: int,
        min_marginal_support: int = 5,
    ) -> None:
        if failure_association_threshold is not None and not (
            0.0 <= failure_association_threshold <= 1.0
        ):
            raise ValueError("failure_association_threshold must be in [0, 1]")
        if block_size < 1:
            raise ValueError("block_size must be >= 1")
        if persistence_k < 1:
            raise ValueError("persistence_k must be >= 1")
        if min_marginal_support < 1:
            raise ValueError("min_marginal_support must be >= 1")
        if min_marginal_support * 2 > block_size:
            raise ValueError(
                "min_marginal_support requires both marginal classes to fit in block_size"
            )

        self.failure_association_threshold = failure_association_threshold
        self.block_size = block_size
        self.persistence_k = persistence_k
        self.min_marginal_support = min_marginal_support
        self._pending_pairs: List[Tuple[int, int]] = []
        self._pending_first_ordinal: Optional[int] = None
        self._blocks: List[PersistenceAssociationBlock] = []
        self.detector_observation_count = 0
        self.bad_streak = 0
        self.triggered = False
        self.trigger_block_index: Optional[int] = None
        self.trigger_detector_observation_ordinal: Optional[int] = None
        self.trigger_host_write_index: Optional[int] = None
        self.trigger_bad_streak: Optional[int] = None
        self.trigger_block_absolute_phi: Optional[float] = None

    @property
    def completed_blocks(self) -> Tuple[PersistenceAssociationBlock, ...]:
        return tuple(self._blocks)

    @staticmethod
    def _counts(pairs: List[Tuple[int, int]]) -> tuple[int, int, int, int]:
        ss = sl = ls = ll = 0
        for true_short, hint_short in pairs:
            if true_short and hint_short:
                ss += 1
            elif true_short:
                sl += 1
            elif hint_short:
                ls += 1
            else:
                ll += 1
        return ss, sl, ls, ll

    @staticmethod
    def _absolute_phi(
        ss: int, sl: int, ls: int, ll: int
    ) -> tuple[Optional[float], float]:
        denominator_squared = (ss + ls) * (ss + sl) * (ll + ls) * (ll + sl)
        if denominator_squared == 0:
            return None, 0.0
        denominator = math.sqrt(denominator_squared)
        return abs((ss * ll - sl * ls) / denominator), denominator

    def observe_resolution(self, resolution: LifetimeClassResolution) -> bool:
        """Consume one usable resolution and return sticky trigger state."""

        if (
            resolution.lifetime_class not in {LifetimeClass.SHORT, LifetimeClass.LONG}
            or resolution.host_hint not in {LifetimeClass.SHORT, LifetimeClass.LONG}
        ):
            return self.triggered

        self.detector_observation_count += 1
        observation_ordinal = self.detector_observation_count
        if self._pending_first_ordinal is None:
            self._pending_first_ordinal = observation_ordinal
        self._pending_pairs.append(
            (
                1 if resolution.lifetime_class is LifetimeClass.SHORT else 0,
                1 if resolution.host_hint is LifetimeClass.SHORT else 0,
            )
        )
        if len(self._pending_pairs) == self.block_size:
            self._complete_block(
                last_detector_observation_ordinal=observation_ordinal,
                host_write_index=resolution.resolved_sequence,
            )
        return self.triggered

    def _complete_block(
        self,
        *,
        last_detector_observation_ordinal: int,
        host_write_index: int,
    ) -> None:
        if len(self._pending_pairs) != self.block_size:
            raise AssertionError("only a full persistence block may be completed")
        if self._pending_first_ordinal is None:
            raise AssertionError("completed persistence block has no first observation")

        ss, sl, ls, ll = self._counts(self._pending_pairs)
        true_short = ss + sl
        true_long = ls + ll
        hint_short = ss + ls
        hint_long = sl + ll
        absolute_phi, denominator = self._absolute_phi(ss, sl, ls, ll)

        invalid_reasons: List[str] = []
        if denominator == 0.0:
            invalid_reasons.append("phi_denominator_zero")
        for label, count in (
            ("true_short_support", true_short),
            ("true_long_support", true_long),
            ("hint_short_support", hint_short),
            ("hint_long_support", hint_long),
        ):
            if count < self.min_marginal_support:
                invalid_reasons.append(f"{label}_below_minimum")

        bad_streak_before = self.bad_streak
        if invalid_reasons:
            state = INVALID
            self.bad_streak = 0
        elif (
            self.failure_association_threshold is not None
            and absolute_phi is not None
            and absolute_phi < self.failure_association_threshold
        ):
            state = BAD
            self.bad_streak += 1
        else:
            state = GOOD
            self.bad_streak = 0

        block_index = len(self._blocks) + 1
        triggered_on_this_block = False
        if (
            not self.triggered
            and state == BAD
            and self.bad_streak >= self.persistence_k
        ):
            self.triggered = True
            self.trigger_block_index = block_index
            self.trigger_detector_observation_ordinal = (
                last_detector_observation_ordinal
            )
            self.trigger_host_write_index = host_write_index
            self.trigger_bad_streak = self.bad_streak
            self.trigger_block_absolute_phi = absolute_phi
            triggered_on_this_block = True

        self._blocks.append(
            PersistenceAssociationBlock(
                block_index=block_index,
                first_detector_observation_ordinal=self._pending_first_ordinal,
                last_detector_observation_ordinal=last_detector_observation_ordinal,
                block_end_host_write_index=host_write_index,
                usable_sample_count=self.block_size,
                ss_count=ss,
                sl_count=sl,
                ls_count=ls,
                ll_count=ll,
                true_short_count=true_short,
                true_long_count=true_long,
                hint_short_count=hint_short,
                hint_long_count=hint_long,
                absolute_phi=absolute_phi,
                block_state=state,
                invalid_reason=";".join(invalid_reasons) or None,
                bad_streak_before=bad_streak_before,
                bad_streak_after=self.bad_streak,
                triggered_on_this_block=triggered_on_this_block,
            )
        )
        self._pending_pairs = []
        self._pending_first_ordinal = None

    def snapshot(self) -> PersistenceAssociationDetectorSnapshot:
        states = [block.block_state for block in self._blocks]
        return PersistenceAssociationDetectorSnapshot(
            detector_version=self.detector_version,
            block_size=self.block_size,
            persistence_k=self.persistence_k,
            min_marginal_support=self.min_marginal_support,
            failure_association_threshold=self.failure_association_threshold,
            detector_observation_count=self.detector_observation_count,
            completed_block_count=len(self._blocks),
            good_block_count=states.count(GOOD),
            bad_block_count=states.count(BAD),
            invalid_block_count=states.count(INVALID),
            partial_block_sample_count=len(self._pending_pairs),
            bad_streak=self.bad_streak,
            triggered=self.triggered,
            trigger_block_index=self.trigger_block_index,
            trigger_detector_observation_ordinal=(
                self.trigger_detector_observation_ordinal
            ),
            trigger_host_write_index=self.trigger_host_write_index,
            trigger_bad_streak=self.trigger_bad_streak,
            trigger_block_absolute_phi=self.trigger_block_absolute_phi,
        )
