import csv
import tempfile
import unittest

from simulator.latency_attribution import (
    CAUSE_DIRECT_GC,
    CAUSE_GC_SPILLOVER,
    CAUSE_UNQUEUED,
    CAUSE_WORKLOAD_QUEUE,
    attribute_latency_causes,
    attribution_sanity_checks,
    build_cause_summary,
    build_tail_attribution_summary,
    save_cause_summary,
    save_latency_attribution_log,
    save_tail_attribution_summary,
)
from simulator.latency_model import RequestLatencyEntry


def make_entry(
    request_id,
    *,
    op="W",
    gc=False,
    queue=0.0,
    service=10.0,
):
    latency = queue + service
    return RequestLatencyEntry(
        request_id=request_id,
        round_index=1,
        row_index=request_id,
        trace_timestamp_s=float(request_id),
        arrival_time_us=float(request_id * 100),
        flow_id="f",
        op=op,
        lba=request_id,
        size_pages=1,
        host_write_pages=1 if op == "W" else 0,
        nand_write_pages=1 if op == "W" else 0,
        migrated_pages=1 if gc else 0,
        gc_count=1 if gc else 0,
        gc_affected=gc,
        queue_delay_us=queue,
        service_time_us=service,
        latency_us=latency,
        completion_time_us=float(request_id * 100) + latency,
    )


class LatencyAttributionTest(unittest.TestCase):
    def setUp(self):
        self.source = [
            make_entry(1, queue=0.0),
            make_entry(2, queue=5.0),
            make_entry(3, gc=True, queue=2.0, service=100.0),
            make_entry(4, op="R", queue=30.0),
            make_entry(5, queue=0.0),
        ]
        self.attributed = attribute_latency_causes(self.source)

    def test_busy_chain_gc_spillover_is_labeled(self):
        self.assertEqual(self.attributed[0].latency_cause, CAUSE_UNQUEUED)
        self.assertEqual(self.attributed[1].latency_cause, CAUSE_WORKLOAD_QUEUE)
        self.assertEqual(self.attributed[2].latency_cause, CAUSE_DIRECT_GC)
        self.assertEqual(self.attributed[3].latency_cause, CAUSE_GC_SPILLOVER)
        self.assertEqual(self.attributed[3].causing_gc_request_id, 3)
        self.assertEqual(self.attributed[4].latency_cause, CAUSE_UNQUEUED)

    def test_direct_gc_resets_latest_gc_origin_inside_chain(self):
        source = [
            make_entry(1, gc=True, queue=0.0, service=50.0),
            make_entry(2, gc=True, queue=5.0, service=50.0),
            make_entry(3, queue=5.0),
        ]
        attributed = attribute_latency_causes(source)
        self.assertEqual(attributed[2].causing_gc_request_id, 2)

    def test_summaries_partition_requests_and_tail(self):
        cause_rows = build_cause_summary(self.attributed)
        counts = {row["latency_cause"]: row["request_count"] for row in cause_rows}
        self.assertEqual(counts[CAUSE_DIRECT_GC], 1)
        self.assertEqual(counts[CAUSE_GC_SPILLOVER], 1)
        self.assertEqual(counts[CAUSE_WORKLOAD_QUEUE], 1)
        self.assertEqual(counts[CAUSE_UNQUEUED], 2)

        tail_rows = build_tail_attribution_summary(self.attributed)
        self.assertEqual([row["tail_scope"] for row in tail_rows], ["p95", "p99", "p99_9", "max"])
        self.assertGreaterEqual(tail_rows[-1]["gc_related_requests"], 1)

    def test_attribution_sanity_checks(self):
        checks = attribution_sanity_checks(self.source, self.attributed)
        self.assertTrue(all(checks.values()))

    def test_saved_artifacts_have_stable_rows(self):
        cause_rows = build_cause_summary(self.attributed)
        tail_rows = build_tail_attribution_summary(self.attributed)
        with tempfile.TemporaryDirectory() as temp:
            log_path = save_latency_attribution_log(temp, self.attributed)
            cause_path = save_cause_summary(temp, cause_rows)
            tail_path = save_tail_attribution_summary(temp, tail_rows)
            with log_path.open("r", encoding="utf-8", newline="") as handle:
                log_rows = list(csv.DictReader(handle))
            with cause_path.open("r", encoding="utf-8", newline="") as handle:
                saved_causes = list(csv.DictReader(handle))
            with tail_path.open("r", encoding="utf-8", newline="") as handle:
                saved_tail = list(csv.DictReader(handle))

        self.assertEqual(len(log_rows), 5)
        self.assertEqual(len(saved_causes), 4)
        self.assertEqual(len(saved_tail), 4)
        self.assertEqual(log_rows[3]["latency_cause"], CAUSE_GC_SPILLOVER)


if __name__ == "__main__":
    unittest.main()
