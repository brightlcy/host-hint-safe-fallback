import csv
import tempfile
import unittest
from pathlib import Path

from simulator.baseline_ftl import BaselineFTL
from simulator.config import SSDConfig
from simulator.latency_model import (
    LatencyModelConfig,
    LatencyModelError,
    RequestLatencyRecorder,
    build_latency_summary,
    latency_sanity_checks,
    nearest_rank_percentile,
    save_latency_summary,
    save_request_latency_log,
)


class LatencyModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SSDConfig(
            page_size=4096,
            chips=1,
            blocks_per_chip=4,
            pages_per_block=4,
            gc_threshold_ratio=0.25,
            reserved_gc_blocks=1,
        )
        self.model = LatencyModelConfig(
            command_overhead_us=10.0,
            read_page_us=5.0,
            program_page_us=7.0,
            erase_block_us=100.0,
        )

    def test_nearest_rank_percentile(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 100.0]
        self.assertEqual(nearest_rank_percentile(values, 50), 3.0)
        self.assertEqual(nearest_rank_percentile(values, 95), 100.0)
        self.assertEqual(nearest_rank_percentile([], 99), 0.0)

    def test_read_service_and_queue_delay(self) -> None:
        ftl = BaselineFTL(self.config)
        recorder = RequestLatencyRecorder(self.model)
        row = {"timestamp": 0.0, "flow_id": "f", "op": "R", "lba": 0, "size": 2}

        first = recorder.record_row(
            ftl, row, round_index=1, row_index=1, arrival_time_us=0.0
        )
        second = recorder.record_row(
            ftl, row, round_index=1, row_index=2, arrival_time_us=0.0
        )

        self.assertEqual(first.service_time_us, 20.0)
        self.assertEqual(first.queue_delay_us, 0.0)
        self.assertEqual(first.latency_us, 20.0)
        self.assertEqual(second.queue_delay_us, 20.0)
        self.assertEqual(second.latency_us, 40.0)

    def test_write_without_gc_uses_host_program_cost(self) -> None:
        ftl = BaselineFTL(self.config)
        recorder = RequestLatencyRecorder(self.model)
        row = {"timestamp": 0.0, "flow_id": "f", "op": "W", "lba": 10, "size": 2}

        entry = recorder.record_row(
            ftl, row, round_index=1, row_index=1, arrival_time_us=0.0
        )

        self.assertEqual(entry.host_write_pages, 2)
        self.assertEqual(entry.nand_write_pages, 2)
        self.assertEqual(entry.migrated_pages, 0)
        self.assertEqual(entry.gc_count, 0)
        self.assertEqual(entry.service_time_us, 24.0)
        self.assertFalse(entry.gc_affected)

    def test_gc_affected_write_includes_migration_and_erase_cost(self) -> None:
        ftl = BaselineFTL(self.config)
        for lpn in range(12):
            ftl.write_lpn(lpn)

        recorder = RequestLatencyRecorder(self.model)
        row = {"timestamp": 0.0, "flow_id": "f", "op": "W", "lba": 0, "size": 1}
        entry = recorder.record_row(
            ftl, row, round_index=1, row_index=1, arrival_time_us=0.0
        )

        self.assertEqual(entry.host_write_pages, 1)
        self.assertEqual(entry.nand_write_pages, 4)
        self.assertEqual(entry.migrated_pages, 3)
        self.assertEqual(entry.gc_count, 1)
        self.assertEqual(entry.service_time_us, 153.0)
        self.assertTrue(entry.gc_affected)

    def test_run_trace_round_rejects_decreasing_timestamps(self) -> None:
        ftl = BaselineFTL(self.config)
        recorder = RequestLatencyRecorder(self.model)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.csv"
            path.write_text(
                "timestamp,flow_id,op,lba,size\n"
                "1.0,f,R,0,1\n"
                "0.5,f,R,1,1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LatencyModelError, "nondecreasing"):
                recorder.run_trace_round(
                    ftl,
                    path,
                    round_index=1,
                    trace_min_timestamp=0.5,
                )

    def test_summary_scopes_and_sanity(self) -> None:
        ftl = BaselineFTL(self.config)
        recorder = RequestLatencyRecorder(self.model)
        rows = [
            {"timestamp": 0.0, "flow_id": "f", "op": "W", "lba": 0, "size": 1},
            {"timestamp": 0.1, "flow_id": "f", "op": "R", "lba": 0, "size": 1},
        ]
        for index, row in enumerate(rows, start=1):
            recorder.record_row(
                ftl,
                row,
                round_index=1,
                row_index=index,
                arrival_time_us=(index - 1) * 100_000.0,
            )

        summary_rows = build_latency_summary(recorder.entries)
        by_scope = {row["scope"]: row for row in summary_rows}
        self.assertEqual(by_scope["all"]["request_count"], 2)
        self.assertEqual(by_scope["read"]["request_count"], 1)
        self.assertEqual(by_scope["write"]["request_count"], 1)
        self.assertEqual(by_scope["write_gc_affected"]["request_count"], 0)

        checks = latency_sanity_checks(
            recorder.entries,
            expected_requests=2,
            measurement_summary=ftl.get_summary(),
        )
        self.assertTrue(all(checks.values()))

    def test_latency_artifacts_have_stable_headers(self) -> None:
        ftl = BaselineFTL(self.config)
        recorder = RequestLatencyRecorder(self.model)
        recorder.record_row(
            ftl,
            {"timestamp": 0.0, "flow_id": "f", "op": "R", "lba": 0, "size": 1},
            round_index=1,
            row_index=1,
            arrival_time_us=0.0,
        )
        summary_rows = build_latency_summary(recorder.entries)

        with tempfile.TemporaryDirectory() as temp:
            request_path = save_request_latency_log(temp, recorder.entries)
            summary_path = save_latency_summary(temp, summary_rows)
            with request_path.open("r", encoding="utf-8", newline="") as handle:
                request_rows = list(csv.DictReader(handle))
            with summary_path.open("r", encoding="utf-8", newline="") as handle:
                saved_summary = list(csv.DictReader(handle))

        self.assertEqual(len(request_rows), 1)
        self.assertEqual(request_rows[0]["op"], "R")
        self.assertEqual(len(saved_summary), 5)
        self.assertEqual(saved_summary[0]["scope"], "all")


if __name__ == "__main__":
    unittest.main()
