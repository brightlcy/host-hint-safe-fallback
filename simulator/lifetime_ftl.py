from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .address import ppn_to_block
from .baseline_ftl import (
    BaselineFTL,
    BaselineFTLError,
    FTLCounterSnapshot,
    GCLogEntry,
    InsufficientGCWorkspaceError,
)
from .config import SSDConfig
from .lifetime_tracker import OnlineLifetimeTracker, RollingHintAssociationDetector
from .persistence_detector import PersistenceAssociationDetector
from .lifetime_workload import LifetimeClass, PageWriteEvent
from .metadata import FTLMetadata, NoFreePageError


@dataclass(frozen=True)
class LifetimeCounterSnapshot(FTLCounterSnapshot):
    short_host_write_pages: int
    long_host_write_pages: int
    short_gc_migration_pages: int
    long_gc_migration_pages: int
    placement_fallback_pages: int


class SegregatedLifetimeFTL(BaselineFTL):
    """Shared two-frontier placement engine for binary lifetime policies.

    SHORT/LONG active blocks draw from one shared free-block pool. GC migration
    preserves the class assigned to the current data version; migration itself is
    never treated as a new lifetime-learning event.
    """

    def __init__(
        self,
        config: SSDConfig,
        *,
        metadata: Optional[FTLMetadata] = None,
        debug_check: bool = False,
    ) -> None:
        super().__init__(config, metadata=metadata, debug_check=debug_check)
        self.active_blocks: Dict[LifetimeClass, Optional[int]] = {
            LifetimeClass.SHORT: None,
            LifetimeClass.LONG: None,
        }
        self.block_lifetime_class: Dict[int, str] = {}
        self.current_class_by_lpn: Dict[int, LifetimeClass] = {}
        self.short_host_write_pages = 0
        self.long_host_write_pages = 0
        self.short_gc_migration_pages = 0
        self.long_gc_migration_pages = 0
        self.placement_fallback_pages = 0

    @staticmethod
    def _placement_class(value: LifetimeClass) -> LifetimeClass:
        if value is LifetimeClass.SHORT:
            return LifetimeClass.SHORT
        return LifetimeClass.LONG

    def _usable_active_block(self, lifetime_class: LifetimeClass) -> Optional[int]:
        block_id = self.active_blocks[lifetime_class]
        if block_id is None:
            return None
        if self.metadata.get_block_info(block_id).free_count > 0:
            return block_id
        self.active_blocks[lifetime_class] = None
        return None

    def _claim_free_block(self, lifetime_class: LifetimeClass) -> Optional[int]:
        active = {block for block in self.active_blocks.values() if block is not None}
        for block_id in self.metadata.get_fully_free_blocks():
            if block_id in active:
                continue
            self.active_blocks[lifetime_class] = block_id
            self.block_lifetime_class[block_id] = lifetime_class.value
            return block_id
        return None

    def _allocate_by_class(self, lifetime_class: LifetimeClass) -> int:
        lifetime_class = self._placement_class(lifetime_class)
        block_id = self._usable_active_block(lifetime_class)
        if block_id is None:
            block_id = self._claim_free_block(lifetime_class)
        if block_id is not None:
            ppn = self.metadata.allocate_free_page_in_block(block_id)
            if self.metadata.get_block_info(block_id).free_count == 0:
                self.active_blocks[lifetime_class] = None
            return ppn

        ppn = self.metadata.allocate_free_page()
        fallback_block = ppn_to_block(ppn, self.config)
        prior = self.block_lifetime_class.get(fallback_block)
        if prior is None:
            self.block_lifetime_class[fallback_block] = lifetime_class.value
            self.active_blocks[lifetime_class] = fallback_block
        elif prior != lifetime_class.value:
            self.block_lifetime_class[fallback_block] = "mixed"
        self.placement_fallback_pages += 1
        return ppn

    def run_gc(self) -> GCLogEntry:
        candidate = self._select_victim()
        victim_block = candidate.block_id
        valid_pages = self.metadata.get_valid_pages_in_block(victim_block)

        destination_ppns: List[int] = []
        destination_classes: List[LifetimeClass] = []
        try:
            for _, lpn in valid_pages:
                lifetime_class = self.current_class_by_lpn.get(lpn)
                if lifetime_class is None:
                    raise BaselineFTLError(
                        f"missing placement class for live LPN {lpn} during GC"
                    )
                destination_classes.append(lifetime_class)
                destination_ppns.append(self._allocate_by_class(lifetime_class))
        except NoFreePageError as exc:
            for ppn in destination_ppns:
                self.metadata.release_reserved_page(ppn)
            raise InsufficientGCWorkspaceError(
                "free-page pool changed while reserving lifetime GC destinations"
            ) from exc

        free_pages_before = self.metadata.free_page_count + len(destination_ppns)
        migrated_pages = 0
        for (old_ppn, lpn), new_ppn, lifetime_class in zip(
            valid_pages, destination_ppns, destination_classes
        ):
            if self.metadata.mapping_table.get(lpn) != old_ppn:
                raise BaselineFTLError(
                    "mapping changed during lifetime-aware GC migration: "
                    f"LPN {lpn}, expected PPN {old_ppn}"
                )
            self.metadata.invalidate_old_page(old_ppn)
            self.metadata.register_valid_page(lpn, new_ppn)
            migrated_pages += 1
            self.valid_page_migrations += 1
            self.nand_write_pages += 1
            if lifetime_class is LifetimeClass.SHORT:
                self.short_gc_migration_pages += 1
            else:
                self.long_gc_migration_pages += 1

        self.metadata.erase_block(victim_block)
        self.block_lifetime_class.pop(victim_block, None)
        for lifetime_class, active_block in tuple(self.active_blocks.items()):
            if active_block == victim_block:
                self.active_blocks[lifetime_class] = None
        self.gc_count += 1

        entry = GCLogEntry(
            gc_id=self.gc_count,
            victim_block=victim_block,
            valid_pages_before=candidate.valid_pages,
            invalid_pages_before=candidate.invalid_pages,
            free_pages_before=free_pages_before,
            migrated_pages=migrated_pages,
            free_pages_after=self.metadata.free_page_count,
            victim_erase_count=self.metadata.get_block_info(victim_block).erase_count,
        )
        self.gc_log.append(entry)
        if self.debug_check:
            self.assert_consistent()
        return entry

    def _write_lpn_to_class(self, lpn: int, lifetime_class: LifetimeClass) -> int:
        self._validate_lpn(lpn)
        lifetime_class = self._placement_class(lifetime_class)

        if self.metadata.free_page_count == 0:
            self.run_gc()
        old_ppn = self.metadata.mapping_table.get(lpn)
        if (
            old_ppn is None
            and self.config.logical_pages is not None
            and len(self.metadata.mapping_table) >= self.config.logical_pages
        ):
            raise ValueError(
                "write would exceed configured logical mapping capacity: "
                f"capacity={self.config.logical_pages}, LPN={lpn}"
            )
        if old_ppn is not None:
            self.metadata.invalidate_old_page(old_ppn)

        self._maybe_run_gc()
        if self.metadata.free_page_count == 0:
            self.run_gc()

        new_ppn = self._allocate_by_class(lifetime_class)
        self.metadata.register_valid_page(lpn, new_ppn)
        self.current_class_by_lpn[lpn] = lifetime_class
        self.host_write_pages += 1
        self.nand_write_pages += 1
        if lifetime_class is LifetimeClass.SHORT:
            self.short_host_write_pages += 1
        else:
            self.long_host_write_pages += 1
        if self.debug_check:
            self.assert_consistent()
        return new_ppn

    def write_lpn(self, lpn: int) -> int:
        return self._write_lpn_to_class(lpn, LifetimeClass.LONG)

    def capture_counter_snapshot(self) -> LifetimeCounterSnapshot:
        base = super().capture_counter_snapshot()
        return LifetimeCounterSnapshot(
            **base.__dict__,
            short_host_write_pages=self.short_host_write_pages,
            long_host_write_pages=self.long_host_write_pages,
            short_gc_migration_pages=self.short_gc_migration_pages,
            long_gc_migration_pages=self.long_gc_migration_pages,
            placement_fallback_pages=self.placement_fallback_pages,
        )

    def get_summary_since(self, snapshot: FTLCounterSnapshot) -> Dict[str, Any]:
        summary = super().get_summary_since(snapshot)
        if not isinstance(snapshot, LifetimeCounterSnapshot):
            raise TypeError("lifetime-aware FTL requires LifetimeCounterSnapshot")
        current = self.capture_counter_snapshot()
        summary.update(
            {
                "short_host_write_pages": current.short_host_write_pages
                - snapshot.short_host_write_pages,
                "long_host_write_pages": current.long_host_write_pages
                - snapshot.long_host_write_pages,
                "short_gc_migration_pages": current.short_gc_migration_pages
                - snapshot.short_gc_migration_pages,
                "long_gc_migration_pages": current.long_gc_migration_pages
                - snapshot.long_gc_migration_pages,
                "placement_fallback_pages": current.placement_fallback_pages
                - snapshot.placement_fallback_pages,
            }
        )
        return summary

    def get_summary(self) -> Dict[str, Any]:
        summary = super().get_summary()
        summary.update(
            {
                "short_host_write_pages": self.short_host_write_pages,
                "long_host_write_pages": self.long_host_write_pages,
                "short_gc_migration_pages": self.short_gc_migration_pages,
                "long_gc_migration_pages": self.long_gc_migration_pages,
                "placement_fallback_pages": self.placement_fallback_pages,
            }
        )
        return summary


class DeviceOnlyLifetimeFTL(SegregatedLifetimeFTL):
    def __init__(self, config: SSDConfig, *, boundary_writes: int, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.lifetime_tracker = OnlineLifetimeTracker(boundary_writes=boundary_writes)

    def write_lpn(self, lpn: int) -> int:
        self.lifetime_tracker.observe_write(lpn)
        prediction = self.lifetime_tracker.predict_next_class(lpn)
        return self._write_lpn_to_class(lpn, prediction)


class StaticHostGuidedFTL(SegregatedLifetimeFTL):
    def __init__(self, config: SSDConfig, *, boundary_writes: int, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.lifetime_tracker = OnlineLifetimeTracker(boundary_writes=boundary_writes)
        self.hintable_resolutions = 0
        self.hint_mismatches = 0

    def write_lpn_with_hint(self, lpn: int, host_hint: LifetimeClass) -> int:
        resolutions, _ = self.lifetime_tracker.observe_write(lpn, host_hint=host_hint)
        for resolution in resolutions:
            mismatch = resolution.hint_mismatch
            if mismatch is None:
                continue
            self.hintable_resolutions += 1
            self.hint_mismatches += int(mismatch)
        placement = host_hint if host_hint is not LifetimeClass.UNKNOWN else LifetimeClass.LONG
        return self._write_lpn_to_class(lpn, placement)

    def write_lpn(self, lpn: int) -> int:
        return self.write_lpn_with_hint(lpn, LifetimeClass.UNKNOWN)

    def get_summary(self) -> Dict[str, Any]:
        summary = super().get_summary()
        summary.update(
            {
                "hintable_resolutions": self.hintable_resolutions,
                "hint_mismatches": self.hint_mismatches,
                "observed_hint_mismatch_rate": (
                    self.hint_mismatches / self.hintable_resolutions
                    if self.hintable_resolutions
                    else 0.0
                ),
            }
        )
        return summary


class OracleLifetimeFTL(SegregatedLifetimeFTL):
    def write_lpn_with_true_class(self, lpn: int, true_class: LifetimeClass) -> int:
        placement = true_class if true_class is not LifetimeClass.UNKNOWN else LifetimeClass.LONG
        return self._write_lpn_to_class(lpn, placement)


class TrustFallbackFTL(SegregatedLifetimeFTL):
    def __init__(
        self,
        config: SSDConfig,
        *,
        boundary_writes: int,
        failure_association_threshold: Optional[float],
        detector_window: int = 128,
        detector_min_samples: int = 30,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.lifetime_tracker = OnlineLifetimeTracker(boundary_writes=boundary_writes)
        self.detector = RollingHintAssociationDetector(
            failure_association_threshold=failure_association_threshold,
            window_size=detector_window,
            min_samples=detector_min_samples,
        )
        self.host_guided_write_pages = 0
        self.device_fallback_write_pages = 0

    def write_lpn_with_hint(self, lpn: int, host_hint: LifetimeClass) -> int:
        resolutions, _ = self.lifetime_tracker.observe_write(lpn, host_hint=host_hint)
        for resolution in resolutions:
            self.detector.observe_resolution(resolution)

        if self.detector.triggered or host_hint is LifetimeClass.UNKNOWN:
            placement = self.lifetime_tracker.predict_next_class(lpn)
            if self.detector.triggered:
                self.device_fallback_write_pages += 1
        else:
            placement = host_hint
            self.host_guided_write_pages += 1
        return self._write_lpn_to_class(lpn, placement)

    def write_lpn(self, lpn: int) -> int:
        return self.write_lpn_with_hint(lpn, LifetimeClass.UNKNOWN)

    def get_summary(self) -> Dict[str, Any]:
        summary = super().get_summary()
        detector = self.detector.snapshot()
        summary.update(
            {
                "host_guided_write_pages": self.host_guided_write_pages,
                "device_fallback_write_pages": self.device_fallback_write_pages,
                "fallback_triggered": detector.triggered,
                "fallback_trigger_sequence": detector.trigger_sequence,
                "fallback_trigger_write_index": detector.trigger_sequence,
                "fallback_trigger_sample_count": detector.trigger_sample_count,
                "detector_sample_count": detector.sample_count,
                "hint_phi_correlation": detector.phi_correlation,
                "hint_absolute_phi": detector.absolute_phi,
                "hint_fisher_abs_upper_bound": detector.fisher_abs_upper_bound,
            }
        )
        return summary


class PersistenceFallbackFTL(SegregatedLifetimeFTL):
    """Safe-Fallback v2 policy with a separate persistence-aware detector.

    This policy intentionally shares the v1 device-side predictor and allocator
    semantics.  Only the evidence rule that enters sticky fallback differs.
    """

    def __init__(
        self,
        config: SSDConfig,
        *,
        boundary_writes: int,
        failure_association_threshold: Optional[float],
        block_size: int,
        persistence_k: int,
        min_marginal_support: int = 5,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.lifetime_tracker = OnlineLifetimeTracker(boundary_writes=boundary_writes)
        self.detector = PersistenceAssociationDetector(
            failure_association_threshold=failure_association_threshold,
            block_size=block_size,
            persistence_k=persistence_k,
            min_marginal_support=min_marginal_support,
        )
        self.host_guided_write_pages = 0
        self.device_fallback_write_pages = 0

    def write_lpn_with_hint(self, lpn: int, host_hint: LifetimeClass) -> int:
        resolutions, _ = self.lifetime_tracker.observe_write(lpn, host_hint=host_hint)
        for resolution in resolutions:
            self.detector.observe_resolution(resolution)

        if self.detector.triggered or host_hint is LifetimeClass.UNKNOWN:
            placement = self.lifetime_tracker.predict_next_class(lpn)
            if self.detector.triggered:
                self.device_fallback_write_pages += 1
        else:
            placement = host_hint
            self.host_guided_write_pages += 1
        return self._write_lpn_to_class(lpn, placement)

    def write_lpn(self, lpn: int) -> int:
        return self.write_lpn_with_hint(lpn, LifetimeClass.UNKNOWN)

    def get_summary(self) -> Dict[str, Any]:
        summary = super().get_summary()
        detector = self.detector.snapshot()
        summary.update(
            {
                "detector_version": detector.detector_version,
                "block_size": detector.block_size,
                "persistence_k": detector.persistence_k,
                "min_marginal_support": detector.min_marginal_support,
                "failure_association_threshold": detector.failure_association_threshold,
                "completed_block_count": detector.completed_block_count,
                "good_block_count": detector.good_block_count,
                "bad_block_count": detector.bad_block_count,
                "invalid_block_count": detector.invalid_block_count,
                "partial_block_sample_count": detector.partial_block_sample_count,
                "fallback_triggered": detector.triggered,
                "trigger_block_index": detector.trigger_block_index,
                "trigger_detector_observation_ordinal": (
                    detector.trigger_detector_observation_ordinal
                ),
                "trigger_host_write_index": detector.trigger_host_write_index,
                "trigger_bad_streak": detector.trigger_bad_streak,
                "trigger_block_absolute_phi": detector.trigger_block_absolute_phi,
                "host_guided_write_pages": self.host_guided_write_pages,
                "device_fallback_write_pages": self.device_fallback_write_pages,
            }
        )
        return summary


def run_page_write_events(ftl: BaselineFTL, events: List[PageWriteEvent]) -> Dict[str, Any]:
    """Run page-granular annotated writes without changing the standard trace API."""

    for event in events:
        if isinstance(ftl, (TrustFallbackFTL, PersistenceFallbackFTL)):
            ftl.write_lpn_with_hint(event.lpn, event.host_hint)
        elif isinstance(ftl, StaticHostGuidedFTL):
            ftl.write_lpn_with_hint(event.lpn, event.host_hint)
        elif isinstance(ftl, OracleLifetimeFTL):
            ftl.write_lpn_with_true_class(event.lpn, event.true_class)
        else:
            ftl.write_lpn(event.lpn)
    return ftl.get_summary()
