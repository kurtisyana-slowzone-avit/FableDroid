"""Frame-timing analyzer tests. Every log here is synthetic."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/bench/analyze.py"
DATA = ROOT / "tests/data/bench"
PREFIX = "[2026-01-01 00:00:00.000] [info] [gpu] [t1] "
MS = 1_000_000


def run_tool(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def begin(samples, version=1):
    return (
        f"{PREFIX}SWAPTIMING_BEGIN version={version} samples={samples} frame_limit=30 "
        "vsync=true clock=steady_ns meaning=cpu_swap_processing"
    )


def row(index, sample_ns):
    return [
        f"{PREFIX}SWAPTIMING row={index} sample_ns={sample_ns} limiter_ns=1 issue_ns=1 wait_ns=1 wait_packets=1",
        f"{PREFIX}SWAPTIMING2 row={index} stall_ns=1 draw_ns=1 throttle_ns=1 draws=1",
        f"{PREFIX}SWAPTIMING3 row={index} fence_wait_ns=1 fence_poll_ns=1 fence_waits=1 fence_polls=1",
    ]


def capture_lines(intervals_ns):
    times = [1_000_000_000]
    for interval in intervals_ns:
        times.append(times[-1] + interval)
    lines = [begin(len(times))]
    for index, sample_ns in enumerate(times):
        lines += row(index, sample_ns)
    return lines + [f"{PREFIX}SWAPTIMING_END samples={len(times)}"]


class AnalyzerTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="fable2-bench-test-")
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def write_log(self, lines, name="synthetic.log", newline="\n"):
        path = self.tmp / name
        path.write_bytes((newline.join(lines) + newline).encode("utf-8"))
        return path

    def summarize(self, path, *extra):
        result = run_tool("summarize", path, *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["results"][0]

    def assert_clear_error(self, path, phrase):
        result = run_tool("summarize", path)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(Path(path).name, result.stderr)
        self.assertIn(phrase, result.stderr)


class CaptureMetricsTests(AnalyzerTestCase):
    def test_capture_metrics_use_nearest_rank_and_strict_thresholds(self):
        metrics = self.summarize(DATA / "capture_ok.log")["metrics"]
        self.assertEqual(metrics["sample_count"], 20)
        self.assertEqual(metrics["median_fps"], 30.0)
        self.assertEqual(metrics["median_ms"], 33.3)
        self.assertEqual(metrics["p95_ms"], 50.1)
        self.assertEqual(metrics["p99_ms"], 100.1)
        self.assertEqual(metrics["worst_ms"], 100.1)
        self.assertEqual(metrics["over_40ms"], 3)
        self.assertEqual(metrics["over_50ms"], 2)
        self.assertEqual(metrics["over_100ms"], 1)

    def test_percentiles_never_interpolate(self):
        cases = [
            ([10, 20, 30, 40], 20.0, 40.0, 40.0),
            (list(range(1, 101)), 50.0, 95.0, 99.0),
            ([7], 7.0, 7.0, 7.0),
        ]
        for values, median, p95, p99 in cases:
            with self.subTest(count=len(values)):
                log = self.write_log(capture_lines([v * MS for v in values]))
                metrics = self.summarize(log)["metrics"]
                self.assertEqual(
                    (metrics["median_ms"], metrics["p95_ms"], metrics["p99_ms"]),
                    (median, p95, p99),
                )

    def test_times_round_half_up_to_one_decimal(self):
        for interval, expected in ((33_350_000, 33.4), (33_349_999, 33.3)):
            with self.subTest(interval=interval):
                log = self.write_log(capture_lines([interval]))
                self.assertEqual(self.summarize(log)["metrics"]["median_ms"], expected)

    def test_windows_keep_empty_gaps_and_mark_partial_tail(self):
        intervals = [500, 500, 250, 250, 250, 250, 3000, 100]
        log = self.write_log(capture_lines([v * MS for v in intervals]))
        windows = self.summarize(log, "--window-seconds", 1)["metrics"]["windows"]
        got = [
            (w["index"], w["frames"], w["median_ms"], w["median_fps"], w["worst_ms"], w["complete"])
            for w in windows
        ]
        self.assertEqual(
            got,
            [
                (0, 2, 500.0, 2.0, 500.0, True),
                (1, 4, 250.0, 4.0, 250.0, True),
                (2, 0, None, None, None, True),
                (3, 0, None, None, None, True),
                (4, 1, 3000.0, 0.3, 3000.0, True),
                (5, 1, 100.0, 10.0, 100.0, False),
            ],
        )

    def test_components_exclude_precapture_first_row(self):
        components = self.summarize(DATA / "capture_ok.log")["components_ms"]
        self.assertEqual(components["limiter"]["max"], 1.0)
        self.assertEqual(components["issue"]["median"], 2.0)
        self.assertEqual(components["wait"]["median"], 3.0)
        self.assertEqual(components["stall"]["median"], 4.0)
        self.assertEqual(components["draw"]["p99"], 5.0)
        self.assertEqual(components["throttle"]["median"], 6.0)
        self.assertEqual(components["fence_wait"]["median"], 7.0)
        self.assertEqual(components["fence_poll"]["max"], 8.0)

    def test_crlf_log_matches_lf_log(self):
        lines = capture_lines([33 * MS, 34 * MS])
        lf = self.summarize(self.write_log(lines, "lf.log"))
        crlf = self.summarize(self.write_log(lines, "crlf.log", "\r\n"))
        self.assertEqual(lf["metrics"], crlf["metrics"])


class RecordParsingTests(AnalyzerTestCase):
    def test_capture_takes_priority_over_guestfps(self):
        self.assertEqual(self.summarize(DATA / "capture_ok.log")["source"], "SWAPTIMING")

    def test_guestfps_fallback_reports_only_what_it_can_measure(self):
        result = self.summarize(DATA / "guestfps_only.log")
        metrics = result["metrics"]
        self.assertEqual(result["source"], "GUESTFPS")
        self.assertEqual(metrics["sample_count"], 395)
        self.assertEqual(metrics["median_fps"], 29.0)
        self.assertEqual(metrics["worst_ms"], 120.7)
        for unavailable in ("median_ms", "p95_ms", "p99_ms", "over_40ms", "over_50ms", "over_100ms"):
            self.assertIsNone(metrics[unavailable], unavailable)
        self.assertEqual(
            [(w["index"], w["frames"], w["mean_fps"], w["worst_ms"]) for w in metrics["windows"]],
            [(0, 145, 29.0, 45.5), (1, 150, 30.0, 60.2), (2, 100, 20.0, 120.7)],
        )

    def test_periodic_records_are_summarized(self):
        periodic = self.summarize(DATA / "capture_ok.log")["periodic"]
        self.assertEqual(periodic["guest_fps"], {"windows": 3, "median_fps": 29.9, "worst_ms": 100.1})
        self.assertEqual(
            periodic["swap_queue"],
            {
                "windows": 2,
                "swaps": 301,
                "median_guest_to_cp_avg_ms": 1.5,
                "max_guest_to_cp_ms": 9.5,
                "max_queued": 2,
                "max_ahead": 1,
            },
        )
        self.assertEqual(
            periodic["gpu_stat"],
            {
                "windows": 1,
                "median_rp_begins": 140.0,
                "median_submits": 2.0,
                "median_wait_ms": 3.5,
                "median_draws": 900.0,
            },
        )
        self.assertEqual(
            periodic["gpu_time"],
            {
                "calibration": {"device_period_ns": 52.08333206176758, "valid_bits": 48, "queue_family": 0},
                "shader_reports": 1,
                "spans": 2,
                "median_span_ms": 30.5,
                "worst_span_ms": 35.2,
            },
        )

    def test_lookalike_lines_are_ignored(self):
        lines = capture_lines([33 * MS])
        noise = [
            f"{PREFIX}TEMPDIAG: GUESTFPS fps=1.0 frames=1 worst_ms=999.0",
            f"{PREFIX}SWAPTIMINGX row=9 sample_ns=1",
            f"{PREFIX}message mentioning SWAPTIMING_END samples=99",
        ]
        result = self.summarize(self.write_log(noise + lines))
        self.assertEqual(result["metrics"]["worst_ms"], 33.0)
        self.assertIsNone(result["periodic"]["guest_fps"])
        self.assertEqual(result["warnings"], [])

    def test_result_identifies_log_gpu_driver_and_present_mode(self):
        path = DATA / "capture_ok.log"
        result = self.summarize(path)
        self.assertEqual(result["log"], "capture_ok.log")
        self.assertEqual(result["log_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(result["device"]["vulkan_devices"]), 1)
        self.assertIn("driver version 0x2", result["device"]["vulkan_devices"][0])
        self.assertEqual(len(result["device"]["swapchains"]), 1)
        self.assertIn("presentation mode 2", result["device"]["swapchains"][0])

    def test_malformed_auxiliary_record_is_a_warning(self):
        lines = capture_lines([33 * MS]) + [f"{PREFIX}SWAPQUEUE swaps="]
        result = self.summarize(self.write_log(lines))
        self.assertEqual(result["metrics"]["median_ms"], 33.0)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("SWAPQUEUE", result["warnings"][0])

    def test_malformed_guestfps_is_an_error_when_it_is_the_source(self):
        log = self.write_log(
            [f"{PREFIX}GUESTFPS fps=30.0 frames=150 worst_ms=33.4", f"{PREFIX}GUESTFPS fps=30.0 frames="]
        )
        self.assert_clear_error(log, "GUESTFPS")


class ErrorTests(AnalyzerTestCase):
    def test_bad_fixtures_produce_clear_errors(self):
        cases = {
            "truncated_no_end.log": "SWAPTIMING_END",
            "truncated_missing_rows.log": "SWAPTIMING2 row=1",
            "truncated_last_line.log": "malformed",
            "duplicate_row.log": "duplicate",
            "zero_samples.log": "zero samples",
            "no_records.log": "no frame-timing records",
        }
        for name, phrase in cases.items():
            with self.subTest(name=name):
                self.assert_clear_error(DATA / name, phrase)

    def test_broken_capture_never_falls_back_to_guestfps(self):
        lines = capture_lines([33 * MS])[:-1] + [f"{PREFIX}GUESTFPS fps=30.0 frames=150 worst_ms=33.4"]
        self.assert_clear_error(self.write_log(lines), "SWAPTIMING_END")

    def test_invalid_captures_are_rejected(self):
        one_sample = [begin(1), *row(0, 1000), f"{PREFIX}SWAPTIMING_END samples=1"]
        backwards = [begin(2), *row(0, 2000), *row(1, 1000), f"{PREFIX}SWAPTIMING_END samples=2"]
        repeated = capture_lines([33 * MS]) + capture_lines([33 * MS])
        no_begin = capture_lines([33 * MS])[1:]
        mismatch = capture_lines([33 * MS])[:-1] + [f"{PREFIX}SWAPTIMING_END samples=5"]
        wrong_version = [begin(2, version=2)] + capture_lines([33 * MS])[1:]
        out_of_range = [begin(2), *row(0, 1000), *row(2, 2000), f"{PREFIX}SWAPTIMING_END samples=2"]
        cases = {
            "at least 2": one_sample,
            "increase": backwards,
            "multiple": repeated,
            "SWAPTIMING_BEGIN": no_begin,
            "samples=5": mismatch,
            "version": wrong_version,
            "row=2": out_of_range,
        }
        for phrase, lines in cases.items():
            with self.subTest(phrase=phrase):
                self.assert_clear_error(self.write_log(lines), phrase)

    def test_missing_file_is_a_clear_error(self):
        self.assert_clear_error(self.tmp / "absent.log", "cannot read")

    def test_nonpositive_window_is_rejected(self):
        log = self.write_log(capture_lines([33 * MS]))
        result = run_tool("summarize", log, "--window-seconds", 0)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)


class OutputTests(AnalyzerTestCase):
    def test_out_file_matches_stdout_with_tool_version(self):
        out = self.tmp / "nested" / "result.json"
        result = run_tool("summarize", DATA / "capture_ok.log", "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written, json.loads(result.stdout))
        self.assertTrue(written["tool_version"])

    def test_multiple_logs_stay_separate(self):
        result = run_tool("summarize", DATA / "capture_ok.log", DATA / "guestfps_only.log")
        self.assertEqual(result.returncode, 0, result.stderr)
        results = json.loads(result.stdout)["results"]
        self.assertEqual([r["source"] for r in results], ["SWAPTIMING", "GUESTFPS"])
        self.assertEqual([r["metrics"]["sample_count"] for r in results], [20, 395])

    def test_failure_leaves_existing_out_file_untouched(self):
        out = self.tmp / "result.json"
        out.write_text("previous", encoding="utf-8")
        result = run_tool("summarize", DATA / "capture_ok.log", DATA / "zero_samples.log", "--out", out)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(out.read_text(encoding="utf-8"), "previous")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["result.json"])


if __name__ == "__main__":
    unittest.main()
