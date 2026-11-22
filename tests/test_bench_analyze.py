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


def device_lines(package="turnip27", driver_version="0x2", driver_info="Mesa 1.0.0", present_mode=2):
    lines = [
        f"{PREFIX}Vulkan device 'Synthetic GPU': API 1.3.0, vendor 0x5143, device 0x1, driver version {driver_version}",
        f"{PREFIX}* driverInfo: {driver_info}",
        f"{PREFIX}VulkanPresenter: Created 1280x720 swapchain with format 37, color space 0, presentation mode {present_mode}",
    ]
    if package != "system":
        lines.insert(
            0,
            f"{PREFIX}Loaded custom Vulkan driver /data/data/example/files/gpu_drivers/{package}/"
            "libvulkan_freedreno.so via libadrenotools",
        )
    return lines


def comparison_report(**changes):
    metrics = {
        "sample_count": 1800,
        "median_fps": 30.0,
        "median_ms": 33.3,
        "p95_ms": 36.0,
        "p99_ms": 40.0,
        "worst_ms": 45.0,
        "over_40ms": 2,
        "over_50ms": 1,
        "over_100ms": 0,
        "window_seconds": 5,
        "windows": [],
    }
    identity = {
        "route": "forest_walk",
        "source": "SWAPTIMING",
        "samples": 1801,
        "frame_limit": "30",
        "vsync": "true",
        "vulkan_device": "Vulkan device 'Synthetic GPU': API 1.3.0, vendor 0x5143, device 0x1, driver version 0x2",
        "driver_package": "turnip27",
        "driver_info": "Mesa 1.0.0",
        "swapchain": "VulkanPresenter: Created 1280x720 swapchain with format 37, color space 0, presentation mode 2",
    }
    for key, value in changes.items():
        if key in metrics:
            metrics[key] = value
        else:
            identity[key] = value
    return {
        "tool": "fabledroid-bench-analyze",
        "tool_version": "1.1.0",
        "percentile_method": "nearest-rank",
        "results": [
            {
                "log": "synthetic.log",
                "log_sha256": "0" * 64,
                "route": identity["route"],
                "source": identity["source"],
                "capture": {
                    "samples": identity["samples"],
                    "frame_limit": identity["frame_limit"],
                    "vsync": identity["vsync"],
                    "meaning": "cpu_swap_processing",
                },
                "metrics": metrics,
                "components_ms": None,
                "periodic": {"guest_fps": None, "swap_queue": None, "gpu_stat": None, "gpu_time": None},
                "device": {
                    "vulkan_devices": [identity["vulkan_device"]],
                    "swapchains": [identity["swapchain"]],
                    "driver_package": identity["driver_package"],
                    "driver_info": identity["driver_info"],
                },
                "warnings": [],
            }
        ],
    }


class CompareTestCase(AnalyzerTestCase):
    def write_report(self, report, name):
        path = self.tmp / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def compare(self, baseline=None, candidate=None, *extra):
        base = self.write_report(comparison_report(**(baseline or {})), "baseline.json")
        cand = self.write_report(comparison_report(**(candidate or {})), "candidate.json")
        return run_tool("compare", base, cand, *extra)

    @staticmethod
    def last_line(result):
        return result.stdout.strip().splitlines()[-1]

    def assert_refused(self, result, phrase):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(phrase, result.stderr)


class CompareGateTests(CompareTestCase):
    def test_unchanged_result_passes_with_a_row_for_every_metric(self):
        result = self.compare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.last_line(result), "RESULT: pass")
        for label in ("Median FPS", "Median ms", "P95 ms", "P99 ms", "Worst ms", "Over 40 ms", "Over 50 ms", "Over 100 ms"):
            self.assertIn(label, result.stdout)

    def test_table_is_fixed_width_markdown(self):
        rows = [l for l in self.compare(candidate={"p99_ms": 41.5}).stdout.splitlines() if l.startswith("|")]
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({len(row) for row in rows}), 1)
        self.assertRegex(rows[1], r"^\|[-:| ]+\|$")

    def test_row_reports_baseline_candidate_and_change(self):
        output = self.compare(candidate={"p99_ms": 41.0, "over_50ms": 3}).stdout
        p99 = next(l for l in output.splitlines() if "P99 ms" in l)
        self.assertIn("40.0", p99)
        self.assertIn("41.0", p99)
        self.assertIn("+1.0 (+2.5%)", p99)
        over = next(l for l in output.splitlines() if "Over 50 ms" in l)
        self.assertIn("+2 (+200.0%)", over)

    def test_values_exactly_at_default_limits_pass(self):
        result = self.compare(candidate={"median_fps": 29.7, "p99_ms": 42.0})
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_each_default_limit_catches_its_regression(self):
        cases = {
            "median_fps": {"median_fps": 29.6},
            "p99_ms": {"p99_ms": 42.1},
            "over_50ms": {"over_50ms": 2},
            "over_100ms": {"over_100ms": 1},
        }
        for metric, candidate in cases.items():
            with self.subTest(metric=metric):
                result = self.compare(candidate=candidate)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(self.last_line(result), f"RESULT: regression ({metric})")

    def test_all_regressions_are_named(self):
        result = self.compare(candidate={"median_fps": 25.0, "p99_ms": 60.0, "over_50ms": 9, "over_100ms": 2})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.last_line(result), "RESULT: regression (median_fps, p99_ms, over_50ms, over_100ms)")

    def test_improvements_pass(self):
        result = self.compare(candidate={"median_fps": 31.0, "p99_ms": 35.0, "over_50ms": 0})
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_ungated_metrics_do_not_fail_the_gate(self):
        result = self.compare(candidate={"median_ms": 34.0, "p95_ms": 39.0, "worst_ms": 49.0, "over_40ms": 9})
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_baseline_with_zero_slow_frames(self):
        zero = {"over_50ms": 0, "over_100ms": 0}
        self.assertEqual(self.compare(zero, zero).returncode, 0)
        for metric in ("over_50ms", "over_100ms"):
            with self.subTest(metric=metric):
                result = self.compare(zero, {**zero, metric: 1})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.last_line(result), f"RESULT: regression ({metric})")
                self.assertIn("+1 (n/a)", result.stdout)


class CompareLimitTests(CompareTestCase):
    def test_each_limit_can_be_loosened_and_is_printed(self):
        cases = [
            ("--max-fps-drop-pct", "2", {"median_fps": 29.4}, {"median_fps": 29.3}, "median_fps"),
            ("--max-p99-rise-pct", "10", {"p99_ms": 44.0}, {"p99_ms": 44.1}, "p99_ms"),
            ("--max-over-50ms-rise", "2", {"over_50ms": 3}, {"over_50ms": 4}, "over_50ms"),
            ("--max-over-100ms-rise", "1", {"over_100ms": 1}, {"over_100ms": 2}, "over_100ms"),
        ]
        for option, value, passing, failing, metric in cases:
            with self.subTest(option=option):
                allowed = self.compare(None, passing, option, value)
                self.assertEqual(allowed.returncode, 0, allowed.stdout)
                override = next(l for l in allowed.stdout.splitlines() if l.startswith("Limit override"))
                self.assertIn(metric, override)
                self.assertIn(value, override)
                blocked = self.compare(None, failing, option, value)
                self.assertEqual(blocked.returncode, 1, blocked.stdout)

    def test_limit_can_be_tightened(self):
        result = self.compare(None, {"p99_ms": 40.1}, "--max-p99-rise-pct", "0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.last_line(result), "RESULT: regression (p99_ms)")

    def test_defaults_print_no_override(self):
        self.assertNotIn("Limit override", self.compare().stdout)

    def test_invalid_limits_are_rejected(self):
        cases = [
            ("--max-fps-drop-pct", "-1"),
            ("--max-fps-drop-pct", "101"),
            ("--max-p99-rise-pct", "nan"),
            ("--max-p99-rise-pct", "inf"),
            ("--max-over-50ms-rise", "-1"),
            ("--max-over-100ms-rise", "1.5"),
        ]
        for option, value in cases:
            with self.subTest(option=option, value=value):
                result = self.compare(None, None, f"{option}={value}")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertNotIn("Traceback", result.stderr)


class CompareIdentityTests(CompareTestCase):
    MISMATCHES = {
        "gpu": {"vulkan_device": "Vulkan device 'Other GPU': API 1.3.0, vendor 0x5143, device 0x9, driver version 0x2"},
        "driver version": {
            "vulkan_device": "Vulkan device 'Synthetic GPU': API 1.3.0, vendor 0x5143, device 0x1, driver version 0x3"
        },
        "driver package": {"driver_package": "download-abc"},
        "driver info": {"driver_info": "Mesa 2.0.0"},
        "present mode": {
            "swapchain": "VulkanPresenter: Created 1280x720 swapchain with format 37, color space 0, presentation mode 1"
        },
        "route": {"route": "town_walk"},
        "frame limit": {"frame_limit": "60"},
        "vsync": {"vsync": "false"},
        "capture samples": {"samples": 901},
    }

    def test_each_mismatch_is_refused(self):
        for field, candidate in self.MISMATCHES.items():
            with self.subTest(field=field):
                self.assert_refused(self.compare(None, candidate), field)

    def test_allow_mismatch_compares_and_prints_each_difference(self):
        for field, candidate in self.MISMATCHES.items():
            with self.subTest(field=field):
                result = self.compare(None, candidate, "--allow-mismatch")
                self.assertEqual(result.returncode, 0, result.stderr)
                warning = next(l for l in result.stdout.splitlines() if l.startswith("Mismatch allowed"))
                self.assertIn(field, warning)

    def test_allow_mismatch_still_reports_regressions(self):
        result = self.compare(None, {"route": "town_walk", "p99_ms": 60.0}, "--allow-mismatch")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.last_line(result), "RESULT: regression (p99_ms)")

    def test_unknown_identity_cannot_be_confirmed(self):
        for changes in ({"route": None}, {"driver_package": None}, {"driver_info": None}):
            with self.subTest(changes=changes):
                self.assert_refused(self.compare(changes, changes), "unknown")
                self.assertEqual(self.compare(changes, changes, "--allow-mismatch").returncode, 0)

    def test_guestfps_results_are_never_gated(self):
        for extra in ((), ("--allow-mismatch",)):
            with self.subTest(extra=extra):
                self.assert_refused(self.compare(None, {"source": "GUESTFPS"}, *extra), "SWAPTIMING")


class CompareInputTests(CompareTestCase):
    def test_unusable_inputs_are_refused(self):
        good = comparison_report()
        missing_metric = comparison_report()
        del missing_metric["results"][0]["metrics"]["p99_ms"]
        two_results = comparison_report()
        two_results["results"] *= 2
        text_metric = comparison_report(p99_ms="40.0")
        bool_count = comparison_report(over_50ms=True)
        negative_count = comparison_report(over_100ms=-1)
        zero_fps = comparison_report(median_fps=0.0)
        zero_p99 = comparison_report(p99_ms=0.0)
        cases = {
            "p99_ms": missing_metric,
            "exactly one": two_results,
            "p99_ms ": text_metric,
            "over_50ms": bool_count,
            "over_100ms": negative_count,
            "median_fps": zero_fps,
            " p99_ms": zero_p99,
        }
        candidate = self.write_report(good, "candidate.json")
        for phrase, report in cases.items():
            with self.subTest(phrase=phrase):
                baseline = self.write_report(report, "baseline.json")
                self.assert_refused(run_tool("compare", baseline, candidate), phrase.strip())

    def test_damaged_files_are_refused(self):
        candidate = self.write_report(comparison_report(), "candidate.json")
        not_json = self.tmp / "baseline.json"
        not_json.write_text("{not json", encoding="utf-8")
        self.assert_refused(run_tool("compare", not_json, candidate), "baseline.json")
        nan = self.tmp / "nan.json"
        nan.write_text(json.dumps(comparison_report()).replace('"p99_ms": 40.0', '"p99_ms": NaN'), encoding="utf-8")
        self.assert_refused(run_tool("compare", nan, candidate), "nan.json")
        self.assert_refused(run_tool("compare", self.tmp / "absent.json", candidate), "absent.json")


class CompareEndToEndTests(CompareTestCase):
    def summarize_to(self, name, intervals, route="forest_walk", **device):
        log = self.write_log(device_lines(**device) + capture_lines(intervals), f"{name}.log")
        out = self.tmp / f"{name}.json"
        result = run_tool("summarize", log, "--route", route, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        return out

    def test_summarize_records_comparison_identity_without_host_paths(self):
        out = self.summarize_to("run", [33 * MS, 34 * MS], package="download-abc", driver_info="Mesa 9.9 (git-1)")
        text = out.read_text(encoding="utf-8")
        result = json.loads(text)["results"][0]
        self.assertEqual(result["route"], "forest_walk")
        self.assertEqual(result["device"]["driver_package"], "download-abc")
        self.assertEqual(result["device"]["driver_info"], "Mesa 9.9 (git-1)")
        self.assertNotIn("/data/data", text.replace(result["log"], ""))

    def test_summarize_labels_the_system_driver(self):
        out = self.summarize_to("run", [33 * MS], package="system")
        result = json.loads(out.read_text(encoding="utf-8"))["results"][0]
        self.assertEqual(result["device"]["driver_package"], "system")

    def test_summaries_compare_and_catch_a_slow_frame(self):
        baseline = self.summarize_to("baseline", [33 * MS] * 10)
        same = self.summarize_to("same", [33 * MS] * 10)
        slower = self.summarize_to("slower", [33 * MS] * 9 + [60 * MS])
        self.assertEqual(run_tool("compare", baseline, same).returncode, 0)
        result = run_tool("compare", baseline, slower)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.last_line(result), "RESULT: regression (p99_ms, over_50ms)")

    def test_same_mesa_commit_from_different_packages_is_a_mismatch(self):
        regular = self.summarize_to("regular", [33 * MS] * 3, package="download-regular")
        patched = self.summarize_to("patched", [33 * MS] * 3, package="download-patched")
        self.assert_refused(run_tool("compare", regular, patched), "driver package")


if __name__ == "__main__":
    unittest.main()
