import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator.alibaba_trace import AlibabaRawIO
from simulator.alibaba_workload import analyze_alibaba_workloads
from simulator.run_analyze_alibaba import parse_args


class AlibabaProgressCheckpointTest(unittest.TestCase):
    def test_cli_defaults_enable_progress_and_checkpoints(self):
        args = parse_args(["--input", "-", "--output-dir", "out"])
        self.assertEqual(args.progress_every_rows, 1_000_000)
        self.assertEqual(args.checkpoint_every_rows, 5_000_000)

    def test_progress_callback_and_checkpoint_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "trace.csv"
            source.write_text(
                "device_id,opcode,offset,length,timestamp\n"
                "0,W,0,4096,0\n"
                "1,R,4096,4096,10\n"
                "0,W,8192,4096,20\n",
                encoding="utf-8",
            )
            events = []
            result = analyze_alibaba_workloads(
                source,
                root / "out",
                max_source_rows=3,
                minimum_candidate_requests=1,
                progress_every_rows=1,
                checkpoint_every_rows=2,
                progress_callback=events.append,
            )
            self.assertEqual(result.source_rows, 3)
            self.assertTrue((root / "out" / "checkpoint_alibaba_device_summary.csv").exists())
            self.assertTrue((root / "out" / "checkpoint_representative_candidates.csv").exists())
            checkpoint = json.loads(
                (root / "out" / "checkpoint_alibaba_dataset_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(checkpoint["source_rows"], 2)
            self.assertTrue(checkpoint["is_checkpoint"])
            self.assertGreaterEqual(len(events), 3)
            self.assertTrue(any(event["checkpoint_written"] for event in events))

    def test_keyboard_interrupt_saves_partial_results(self):
        rows = [
            AlibabaRawIO(1, 0, "W", 0, 4096, 0),
            AlibabaRawIO(2, 1, "R", 4096, 4096, 10),
        ]

        def interrupted_rows(_path):
            yield rows[0]
            yield rows[1]
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            with patch(
                "simulator.alibaba_workload.iter_alibaba_io_rows",
                side_effect=interrupted_rows,
            ):
                result = analyze_alibaba_workloads(
                    "-",
                    root / "out",
                    minimum_candidate_requests=1,
                    progress_every_rows=1,
                    checkpoint_every_rows=10,
                )
            self.assertEqual(result.stop_reason, "interrupted")
            self.assertEqual(result.source_rows, 2)
            payload = json.loads(result.dataset_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["stop_reason"], "interrupted")
            self.assertEqual(payload["source_rows"], 2)
            self.assertTrue(result.device_summary_path.exists())
            self.assertTrue(result.candidate_path.exists())


if __name__ == "__main__":
    unittest.main()
