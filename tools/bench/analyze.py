#!/usr/bin/env python3
"""Summarize FableDroid frame-timing logs.

Frame intervals come from a SWAPTIMING capture when the log has one. Otherwise
the summary falls back to the periodic GUESTFPS windows, which cannot produce
per-frame percentiles. A damaged capture is an error and never falls back.

Percentiles use the nearest-rank method: the value at 1-based rank
ceil(percent / 100 * count) of the sorted samples, with no interpolation. The
median is the 50th percentile. See docs/BENCHMARKING.md.
"""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

TOOL_NAME = "fabledroid-bench-analyze"
TOOL_VERSION = "1.1.0"
CAPTURE_VERSION = "1"
DEFAULT_WINDOW_SECONDS = 5
NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000
THRESHOLDS_MS = (40, 50, 100)

LOG_PREFIX = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\] \[\w+\] \[\w+\] \[t\d+\] ")
RECORD = re.compile(
    r"(SWAPTIMING_BEGIN|SWAPTIMING_END|SWAPTIMING2|SWAPTIMING3|SWAPTIMING|"
    r"GUESTFPS|SWAPQUEUE|GPUSTAT|GPUTIME)(?: (.*))?"
)
NUM = r"\d+(?:\.\d+)?"
FORMATS = {
    "SWAPTIMING_BEGIN": re.compile(
        r"version=(?P<version>\d+) samples=(?P<samples>\d+) frame_limit=(?P<frame_limit>\S+) "
        r"vsync=(?P<vsync>\S+) clock=(?P<clock>\S+) meaning=(?P<meaning>\S+)"
    ),
    "SWAPTIMING_END": re.compile(r"samples=(?P<samples>\d+)"),
    "SWAPTIMING": re.compile(
        r"row=(?P<row>\d+) sample_ns=(?P<sample>\d+) limiter_ns=(?P<limiter>\d+) "
        r"issue_ns=(?P<issue>\d+) wait_ns=(?P<wait>\d+) wait_packets=\d+"
    ),
    "SWAPTIMING2": re.compile(
        r"row=(?P<row>\d+) stall_ns=(?P<stall>\d+) draw_ns=(?P<draw>\d+) "
        r"throttle_ns=(?P<throttle>\d+) draws=\d+"
    ),
    "SWAPTIMING3": re.compile(
        r"row=(?P<row>\d+) fence_wait_ns=(?P<fence_wait>\d+) "
        r"fence_poll_ns=(?P<fence_poll>\d+) fence_waits=\d+ fence_polls=\d+"
    ),
    "GUESTFPS": re.compile(rf"fps=(?P<fps>{NUM}) frames=(?P<frames>[1-9]\d*) worst_ms=(?P<worst>{NUM})"),
    "SWAPQUEUE": re.compile(
        rf"swaps=(?P<swaps>\d+) guest_to_cp_ms avg=(?P<avg>{NUM}) max=(?P<max>{NUM}) "
        rf"queued avg={NUM} max=(?P<queued>\d+) max_ahead=(?P<ahead>\d+)"
    ),
    "GPUSTAT": re.compile(
        rf"per-frame: rp_begins=(?P<rp_begins>{NUM}) submits=(?P<submits>{NUM}) waits={NUM} "
        rf"wait_ms=(?P<wait_ms>{NUM}) upload_kb={NUM} upload_calls={NUM} in_order={NUM} "
        rf"subpage={NUM} wait_sems={NUM} \| draws=(?P<draws>{NUM}) pred_exec={NUM} "
        rf"pred_skip={NUM} bin_sel_changes={NUM} bin_sel_union=0x[0-9a-fA-F]+ \| rp_end.*"
    ),
}
GPUTIME_CALIBRATION = re.compile(
    r"calibration: device_period_ns=(?P<period>[0-9.eE+-]+) valid_bits=(?P<bits>\d+) queue_family=(?P<family>\d+)"
)
GPUTIME_SHADERS = re.compile(r"shaders\(\d+\):.*")
GPUTIME_SPAN = re.compile(rf"span=(?P<span>{NUM})ms marks=\d+ \|.*")
VULKAN_DEVICE = re.compile(r"Vulkan device '")
SWAPCHAIN = re.compile(r"(?:VulkanPresenter: )?Created \S+ swapchain\b")
COMPONENTS = ("limiter", "issue", "wait", "stall", "draw", "throttle", "fence_wait", "fence_poll")
CUSTOM_DRIVER = re.compile(r"Loaded custom Vulkan driver \S*?gpu_drivers/([^/\s]+)/")
DRIVER_INFO = re.compile(r"\* driverInfo: (.+)")
VULKAN_DEVICE_FIELDS = re.compile(
    r"Vulkan device '(?P<name>[^']+)': API [\d.]+, vendor (?P<vendor>0x[0-9A-Fa-f]+), "
    r"device (?P<device>0x[0-9A-Fa-f]+), driver version (?P<version>0x[0-9A-Fa-f]+)"
)
PRESENT_MODE = re.compile(r"presentation mode (\d+)")

EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_REFUSED = 2
COMPARE_ROWS = (
    ("median_fps", "Median FPS"),
    ("median_ms", "Median ms"),
    ("p95_ms", "P95 ms"),
    ("p99_ms", "P99 ms"),
    ("worst_ms", "Worst ms"),
    ("over_40ms", "Over 40 ms"),
    ("over_50ms", "Over 50 ms"),
    ("over_100ms", "Over 100 ms"),
)
COUNT_METRICS = ("over_40ms", "over_50ms", "over_100ms")
POSITIVE_METRICS = ("median_fps", "p99_ms")
DEFAULT_LIMITS = {"median_fps": Decimal(1), "p99_ms": Decimal(5), "over_50ms": 0, "over_100ms": 0}
LIMIT_TEXT = {
    "median_fps": "may fall by at most {}%",
    "p99_ms": "may rise by at most {}%",
    "over_50ms": "may rise by at most {}",
    "over_100ms": "may rise by at most {}",
}


class AnalyzeError(Exception):
    """A log that cannot produce a trustworthy result."""


def tenths(value):
    """Round to one decimal place, with halves rounded up."""
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def ms(nanoseconds):
    return tenths(Decimal(nanoseconds) / NS_PER_MS)


def fps(nanoseconds):
    return tenths(Decimal(NS_PER_S) / Decimal(nanoseconds))


def nearest_rank(ordered, percent):
    """Return the nearest-rank percentile of an ascending, non-empty sequence."""
    rank = max(1, (percent * len(ordered) + 99) // 100)
    return ordered[rank - 1]


class Capture:
    """Validates one SWAPTIMING_BEGIN ... SWAPTIMING_END block."""

    KINDS = ("SWAPTIMING", "SWAPTIMING2", "SWAPTIMING3")

    def __init__(self):
        self.header = None
        self.samples = 0
        self.ended = False
        self.rows = {kind: {} for kind in self.KINDS}

    @property
    def present(self):
        return self.header is not None

    def add(self, kind, fields, line_number):
        if kind == "SWAPTIMING_BEGIN":
            self._begin(fields)
        elif kind == "SWAPTIMING_END":
            self._end(fields)
        else:
            self._row(kind, fields, line_number)

    def _begin(self, fields):
        if self.present:
            raise AnalyzeError("multiple SWAPTIMING captures in one log; split them into separate files")
        if fields["version"] != CAPTURE_VERSION:
            raise AnalyzeError(f"unsupported SWAPTIMING version={fields['version']}")
        if fields["clock"] != "steady_ns":
            raise AnalyzeError(f"unsupported SWAPTIMING clock={fields['clock']}")
        self.samples = int(fields["samples"])
        if self.samples == 0:
            raise AnalyzeError("SWAPTIMING capture has zero samples")
        if self.samples < 2:
            raise AnalyzeError("SWAPTIMING capture needs at least 2 samples to measure a frame interval")
        self.header = fields

    def _end(self, fields):
        if not self.present:
            raise AnalyzeError("SWAPTIMING_END appears before SWAPTIMING_BEGIN")
        if self.ended:
            raise AnalyzeError("duplicate SWAPTIMING_END")
        if int(fields["samples"]) != self.samples:
            raise AnalyzeError(
                f"SWAPTIMING_END samples={fields['samples']} does not match "
                f"SWAPTIMING_BEGIN samples={self.samples}"
            )
        self.ended = True

    def _row(self, kind, fields, line_number):
        index = int(fields["row"])
        if not self.present:
            raise AnalyzeError(f"line {line_number}: {kind} row={index} appears before SWAPTIMING_BEGIN")
        if self.ended:
            raise AnalyzeError(f"line {line_number}: {kind} row={index} appears after SWAPTIMING_END")
        if index >= self.samples:
            raise AnalyzeError(f"line {line_number}: {kind} row={index} is outside a {self.samples}-sample capture")
        if index in self.rows[kind]:
            raise AnalyzeError(f"line {line_number}: duplicate {kind} row={index}")
        self.rows[kind][index] = {k: int(v) for k, v in fields.items() if k != "row"}

    def validated_rows(self):
        if not self.ended:
            found = len(self.rows["SWAPTIMING"])
            raise AnalyzeError(f"truncated capture: missing SWAPTIMING_END ({found} of {self.samples} rows found)")
        merged = []
        for index in range(self.samples):
            row = {}
            for kind in self.KINDS:
                if index not in self.rows[kind]:
                    raise AnalyzeError(f"truncated capture: missing {kind} row={index}")
                row.update(self.rows[kind][index])
            merged.append(row)
        for previous, current in zip(merged, merged[1:]):
            if current["sample"] <= previous["sample"]:
                raise AnalyzeError("SWAPTIMING sample_ns values must strictly increase")
        return merged


def capture_metrics(rows, window_seconds):
    times = [row["sample"] for row in rows]
    intervals = [b - a for a, b in zip(times, times[1:])]
    ordered = sorted(intervals)
    median = nearest_rank(ordered, 50)
    metrics = {
        "sample_count": len(intervals),
        "median_fps": fps(median),
        "median_ms": ms(median),
        "p95_ms": ms(nearest_rank(ordered, 95)),
        "p99_ms": ms(nearest_rank(ordered, 99)),
        "worst_ms": ms(ordered[-1]),
    }
    for threshold in THRESHOLDS_MS:
        metrics[f"over_{threshold}ms"] = sum(v > threshold * NS_PER_MS for v in intervals)
    metrics["window_seconds"] = window_seconds
    metrics["windows"] = capture_windows(times, intervals, window_seconds)
    return metrics


def capture_windows(times, intervals, window_seconds):
    window_ns = window_seconds * NS_PER_S
    elapsed = times[-1] - times[0]
    buckets = [[] for _ in range((elapsed - 1) // window_ns + 1)]
    for end, interval in zip(times[1:], intervals):
        buckets[(end - times[0] - 1) // window_ns].append(interval)
    windows = []
    for index, bucket in enumerate(buckets):
        window = {
            "index": index,
            "start_s": index * window_seconds,
            "end_s": (index + 1) * window_seconds,
            "frames": len(bucket),
            "median_fps": None,
            "median_ms": None,
            "worst_ms": None,
            "complete": elapsed >= (index + 1) * window_ns,
        }
        if bucket:
            ordered = sorted(bucket)
            median = nearest_rank(ordered, 50)
            window.update(median_fps=fps(median), median_ms=ms(median), worst_ms=ms(ordered[-1]))
        windows.append(window)
    return windows


def capture_components(rows):
    # Row 0 also holds work accumulated before the first measured interval.
    measured = rows[1:]
    result = {}
    for name in COMPONENTS:
        ordered = sorted(row[name] for row in measured)
        result[name] = {
            "median": ms(nearest_rank(ordered, 50)),
            "p95": ms(nearest_rank(ordered, 95)),
            "p99": ms(nearest_rank(ordered, 99)),
            "max": ms(ordered[-1]),
        }
    return result


def guestfps_metrics(windows):
    ordered = sorted(float(w["fps"]) for w in windows)
    return {
        "sample_count": sum(int(w["frames"]) for w in windows),
        "median_fps": tenths(nearest_rank(ordered, 50)),
        "median_ms": None,
        "p95_ms": None,
        "p99_ms": None,
        "worst_ms": tenths(max(float(w["worst"]) for w in windows)),
        "over_40ms": None,
        "over_50ms": None,
        "over_100ms": None,
        "window_seconds": None,
        "windows": [
            {
                "index": index,
                "frames": int(w["frames"]),
                "mean_fps": tenths(w["fps"]),
                "worst_ms": tenths(w["worst"]),
            }
            for index, w in enumerate(windows)
        ],
    }


def median_of(records, key):
    return tenths(nearest_rank(sorted(float(r[key]) for r in records), 50))


def periodic_summary(guest, queue, stats, spans, shader_reports, calibration):
    summary = {"guest_fps": None, "swap_queue": None, "gpu_stat": None, "gpu_time": None}
    if guest:
        summary["guest_fps"] = {
            "windows": len(guest),
            "median_fps": median_of(guest, "fps"),
            "worst_ms": tenths(max(float(g["worst"]) for g in guest)),
        }
    if queue:
        summary["swap_queue"] = {
            "windows": len(queue),
            "swaps": sum(int(q["swaps"]) for q in queue),
            "median_guest_to_cp_avg_ms": median_of(queue, "avg"),
            "max_guest_to_cp_ms": tenths(max(float(q["max"]) for q in queue)),
            "max_queued": max(int(q["queued"]) for q in queue),
            "max_ahead": max(int(q["ahead"]) for q in queue),
        }
    if stats:
        summary["gpu_stat"] = {
            "windows": len(stats),
            "median_rp_begins": median_of(stats, "rp_begins"),
            "median_submits": median_of(stats, "submits"),
            "median_wait_ms": median_of(stats, "wait_ms"),
            "median_draws": median_of(stats, "draws"),
        }
    if spans or shader_reports or calibration:
        summary["gpu_time"] = {
            "calibration": calibration,
            "shader_reports": shader_reports,
            "spans": len(spans),
            "median_span_ms": median_of(spans, "span") if spans else None,
            "worst_span_ms": tenths(max(float(s["span"]) for s in spans)) if spans else None,
        }
    return summary


def parse_gputime(body):
    """Return (kind, fields) for a GPUTIME record, or None if it is malformed."""
    for kind, pattern in (("calibration", GPUTIME_CALIBRATION), ("shaders", GPUTIME_SHADERS), ("span", GPUTIME_SPAN)):
        match = pattern.fullmatch(body)
        if match:
            return kind, match.groupdict()
    return None


def summarize_log(path, window_seconds, route=None):
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AnalyzeError(f"cannot read log: {exc.strerror or exc}") from exc

    capture = Capture()
    guest, queue, stats, spans = [], [], [], []
    shader_reports = 0
    calibration = None
    malformed_guest = []
    warnings = []
    devices, swapchains = [], []
    driver_package, driver_info = None, None

    for line_number, raw in enumerate(data.decode("utf-8", "replace").split("\n"), start=1):
        line = LOG_PREFIX.sub("", raw.rstrip("\r"), count=1)
        if VULKAN_DEVICE.match(line):
            devices.append(line)
        elif SWAPCHAIN.match(line):
            swapchains.append(line)
        elif driver_package is None and (custom := CUSTOM_DRIVER.match(line)):
            # Keep only the package id: the full path is a device path.
            driver_package = custom.group(1)
        elif driver_info is None and (info := DRIVER_INFO.fullmatch(line)):
            driver_info = info.group(1).strip()
        record = RECORD.fullmatch(line)
        if not record:
            continue
        kind, body = record.group(1), record.group(2) or ""

        if kind == "GPUTIME":
            parsed = parse_gputime(body)
            if parsed is None:
                warnings.append(f"line {line_number}: malformed GPUTIME record ignored")
            elif parsed[0] == "calibration":
                fields = parsed[1]
                calibration = {
                    "device_period_ns": float(fields["period"]),
                    "valid_bits": int(fields["bits"]),
                    "queue_family": int(fields["family"]),
                }
            elif parsed[0] == "shaders":
                shader_reports += 1
            else:
                spans.append(parsed[1])
            continue

        match = FORMATS[kind].fullmatch(body)
        if kind.startswith("SWAPTIMING"):
            if not match:
                raise AnalyzeError(f"line {line_number}: malformed {kind} record")
            capture.add(kind, match.groupdict(), line_number)
        elif not match:
            if kind == "GUESTFPS":
                malformed_guest.append(line_number)
            warnings.append(f"line {line_number}: malformed {kind} record ignored")
        elif kind == "GUESTFPS":
            guest.append(match.groupdict())
        elif kind == "SWAPQUEUE":
            queue.append(match.groupdict())
        else:
            stats.append(match.groupdict())

    result = {"log": path.name, "log_sha256": hashlib.sha256(data).hexdigest(), "route": route}
    if capture.present:
        rows = capture.validated_rows()
        result["source"] = "SWAPTIMING"
        result["capture"] = {
            "samples": capture.samples,
            "frame_limit": capture.header["frame_limit"],
            "vsync": capture.header["vsync"],
            "meaning": capture.header["meaning"],
        }
        result["metrics"] = capture_metrics(rows, window_seconds)
        result["components_ms"] = capture_components(rows)
    elif malformed_guest:
        raise AnalyzeError(f"line {malformed_guest[0]}: malformed GUESTFPS record in the only timing source")
    elif guest:
        result["source"] = "GUESTFPS"
        result["capture"] = None
        result["metrics"] = guestfps_metrics(guest)
        result["components_ms"] = None
    else:
        raise AnalyzeError("no frame-timing records found (need SWAPTIMING or GUESTFPS)")

    result["periodic"] = periodic_summary(guest, queue, stats, spans, shader_reports, calibration)
    result["device"] = {
        "vulkan_devices": devices,
        "swapchains": swapchains,
        "driver_package": driver_package or ("system" if devices else None),
        "driver_info": driver_info,
    }
    result["warnings"] = warnings
    return result


def load_result(path):
    """Load a one-result summary and return it with its validated gate metrics."""
    name = path.name
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except OSError as exc:
        raise AnalyzeError(f"{name}: cannot read result: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise AnalyzeError(f"{name}: result is not UTF-8 JSON") from exc

    def reject_constant(constant):
        raise ValueError(f"{constant} is not a valid metric value")

    try:
        report = json.loads(text, parse_constant=reject_constant)
    except (ValueError, RecursionError) as exc:
        raise AnalyzeError(f"{name}: not a valid result file: {exc}") from exc
    results = report.get("results") if isinstance(report, dict) else None
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise AnalyzeError(f"{name}: result file must contain exactly one result")
    result = results[0]
    if result.get("source") != "SWAPTIMING":
        raise AnalyzeError(
            f"{name}: the gate needs a SWAPTIMING capture (found {clean(result.get('source'))}); "
            "GUESTFPS has no per-frame p99 or slow-frame counts"
        )
    raw = result.get("metrics")
    if not isinstance(raw, dict):
        raise AnalyzeError(f"{name}: result has no metrics")
    metrics = {}
    for key, _ in COMPARE_ROWS:
        value = raw.get(key)
        if key in COUNT_METRICS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AnalyzeError(f"{name}: metric {key} must be a non-negative whole number")
            metrics[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise AnalyzeError(f"{name}: metric {key} must be a non-negative number")
        if key in POSITIVE_METRICS and value == 0:
            raise AnalyzeError(f"{name}: metric {key} must be greater than zero")
        metrics[key] = Decimal(str(value))
    return result, metrics


def clean(value):
    """Render a value from a result file on one line."""
    return " ".join(str(value).split())


def result_identity(result):
    """Return what must match for two results to be comparable; None means unknown."""
    device = result.get("device") if isinstance(result.get("device"), dict) else {}
    capture = result.get("capture") if isinstance(result.get("capture"), dict) else {}
    lines = device.get("vulkan_devices")
    lines = lines if isinstance(lines, list) else []
    parsed = {m.groups() for m in (VULKAN_DEVICE_FIELDS.match(l) for l in lines if isinstance(l, str)) if m}
    name, vendor, device_id, version = parsed.pop() if len(parsed) == 1 else (None, None, None, None)
    swapchains = device.get("swapchains")
    swapchains = swapchains if isinstance(swapchains, list) else []
    modes = {m.group(1) for m in (PRESENT_MODE.search(l) for l in swapchains if isinstance(l, str)) if m}
    identity = {
        "gpu": f"{name} {vendor} {device_id}" if name else None,
        "driver version": version,
        "driver package": device.get("driver_package"),
        "driver info": device.get("driver_info"),
        "present mode": ",".join(sorted(modes, key=int)) if modes else None,
        "route": result.get("route"),
        "frame limit": capture.get("frame_limit"),
        "vsync": capture.get("vsync"),
        "capture samples": capture.get("samples"),
    }
    return {key: None if value is None else clean(value) for key, value in identity.items()}


def identity_differences(baseline, candidate):
    base, cand = result_identity(baseline), result_identity(candidate)
    differences = []
    for field in base:
        if base[field] is None or cand[field] is None:
            differences.append(f"{field} unknown (baseline={base[field]}, candidate={cand[field]})")
        elif base[field] != cand[field]:
            differences.append(f"{field} differs (baseline={base[field]}, candidate={cand[field]})")
    return differences


def is_regression(key, base, cand, limit):
    if key == "median_fps":
        return cand < base * (100 - limit) / 100
    if key == "p99_ms":
        return cand > base * (100 + limit) / 100
    return cand - base > limit


def one_decimal(value):
    rounded = Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return abs(rounded) if rounded == 0 else rounded


def change_text(key, base, cand):
    delta = cand - base
    amount = f"{delta:+d}" if key in COUNT_METRICS else format(one_decimal(delta), "+f")
    if base == 0:
        return f"{amount} (n/a)"
    percent = one_decimal(Decimal(delta) * 100 / Decimal(base))
    return f"{amount} ({format(percent, '+f')}%)"


def limit_text(key, limits):
    if key not in limits:
        return "-"
    if key in COUNT_METRICS:
        return f"+{limits[key]}"
    sign = "-" if key == "median_fps" else "+"
    return f"{sign}{format(limits[key].normalize(), 'f')}%"


def render_table(rows):
    header = ("Metric", "Baseline", "Candidate", "Change", "Limit", "Status")
    table = [header, *rows]
    widths = [max(len(row[column]) for row in table) for column in range(len(header))]

    def line(row):
        cells = [row[0].ljust(widths[0])]
        cells += [row[column].rjust(widths[column]) for column in range(1, 5)]
        cells.append(row[5].ljust(widths[5]))
        return "| " + " | ".join(cells) + " |"

    separator = ["-" * widths[0]] + ["-" * (widths[c] - 1) + ":" for c in range(1, 5)] + ["-" * widths[5]]
    return [line(header), "| " + " | ".join(separator) + " |", *[line(row) for row in rows]]


def run_compare(args):
    try:
        baseline, base_metrics = load_result(args.baseline)
        candidate, cand_metrics = load_result(args.candidate)
    except AnalyzeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    differences = identity_differences(baseline, candidate)
    if differences and not args.allow_mismatch:
        print(
            f"error: refusing to compare: {'; '.join(differences)}. Use --allow-mismatch to compare anyway.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    overrides = {
        "median_fps": args.max_fps_drop_pct,
        "p99_ms": args.max_p99_rise_pct,
        "over_50ms": args.max_over_50ms_rise,
        "over_100ms": args.max_over_100ms_rise,
    }
    limits = {key: DEFAULT_LIMITS[key] if value is None else value for key, value in overrides.items()}
    output = [f"Baseline: {clean(args.baseline.name)}", f"Candidate: {clean(args.candidate.name)}"]
    for key, value in overrides.items():
        if value is not None:
            shown = format(value.normalize(), "f") if isinstance(value, Decimal) else value
            default = DEFAULT_LIMITS[key]
            default = format(default.normalize(), "f") if isinstance(default, Decimal) else default
            suffix = "%" if isinstance(value, Decimal) else ""
            output.append(f"Limit override: {key} {LIMIT_TEXT[key].format(shown)} (default {default}{suffix})")
    if differences:
        output.append(f"Mismatch allowed: {'; '.join(differences)}")

    rows, regressions = [], []
    for key, label in COMPARE_ROWS:
        base, cand = base_metrics[key], cand_metrics[key]
        status = "info"
        if key in limits:
            status = "pass"
            if is_regression(key, base, cand, limits[key]):
                status = "REGRESSION"
                regressions.append(key)
        shown_base, shown_cand = (str(base), str(cand)) if key in COUNT_METRICS else (
            format(one_decimal(base), "f"),
            format(one_decimal(cand), "f"),
        )
        rows.append((label, shown_base, shown_cand, change_text(key, base, cand), limit_text(key, limits), status))

    output += ["", *render_table(rows), ""]
    output.append(f"RESULT: regression ({', '.join(regressions)})" if regressions else "RESULT: pass")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    sys.stdout.write("\n".join(output) + "\n")
    return EXIT_REGRESSION if regressions else EXIT_PASS


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def positive_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def percent_limit(maximum=None):
    def parse(value):
        if not re.fullmatch(r"\d+(?:\.\d+)?", value):
            raise argparse.ArgumentTypeError("must be a non-negative percentage")
        number = Decimal(value)
        if maximum is not None and number > maximum:
            raise argparse.ArgumentTypeError(f"must be at most {maximum}")
        return number

    return parse


def count_limit(value):
    if not re.fullmatch(r"\d+", value):
        raise argparse.ArgumentTypeError("must be a non-negative whole number")
    return int(value)


def route_name(value):
    name = clean(value)
    if not name:
        raise argparse.ArgumentTypeError("must not be blank")
    return name


def build_parser():
    parser = argparse.ArgumentParser(description="Summarize and compare FableDroid frame-timing logs.")
    commands = parser.add_subparsers(dest="command", required=True)
    summarize = commands.add_parser("summarize", help="summarize one or more log files as JSON")
    summarize.add_argument("logs", nargs="+", type=Path, metavar="log")
    summarize.add_argument("--out", type=Path, help="also write the JSON to this file")
    summarize.add_argument("--route", type=route_name, help="benchmark route name, required by compare")
    summarize.add_argument(
        "--window-seconds",
        type=positive_int,
        default=DEFAULT_WINDOW_SECONDS,
        help=f"per-window length for SWAPTIMING captures (default: {DEFAULT_WINDOW_SECONDS})",
    )
    compare = commands.add_parser("compare", help="gate a candidate result against a baseline result")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--allow-mismatch", action="store_true", help="compare despite identity differences")
    compare.add_argument(
        "--max-fps-drop-pct", type=percent_limit(Decimal(100)), metavar="PCT", help="default: 1"
    )
    compare.add_argument("--max-p99-rise-pct", type=percent_limit(), metavar="PCT", help="default: 5")
    compare.add_argument("--max-over-50ms-rise", type=count_limit, metavar="N", help="default: 0")
    compare.add_argument("--max-over-100ms-rise", type=count_limit, metavar="N", help="default: 0")
    return parser


def run_summarize(args):
    results = []
    for path in args.logs:
        try:
            results.append(summarize_log(path, args.window_seconds, args.route))
        except AnalyzeError as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            return 1
    report = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "percentile_method": "nearest-rank",
        "results": results,
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        try:
            write_atomic(args.out, text)
        except OSError as exc:
            print(f"error: {args.out.name}: cannot write result: {exc.strerror or exc}", file=sys.stderr)
            return 1
    sys.stdout.write(text)
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    return run_compare(args) if args.command == "compare" else run_summarize(args)


if __name__ == "__main__":
    sys.exit(main())
