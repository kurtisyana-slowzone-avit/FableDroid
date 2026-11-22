# Benchmarking

`tools/bench/analyze.py` turns FableDroid runtime logs into a JSON summary and gates one result against another. It uses only the Python standard library and needs no device, game files or network.

```powershell
py -3 tools/bench/analyze.py summarize fable_2_001.log --route forest_walk --out result.json
```

On Linux and macOS, use `python3` instead of `py -3`.

| Option | Meaning |
|---|---|
| `<log> [<log> ...]` | One or more log files. Each gets its own entry in `results`; logs are never merged. |
| `--out <file>` | Also write the JSON to this file. The write is atomic, and nothing is written if any log fails. |
| `--route <name>` | Name of the benchmark route. `compare` needs it to confirm two runs are comparable. |
| `--window-seconds <n>` | Window length for capture summaries. The default is 5. |

`summarize` exits with `0` on success, `1` when a log cannot produce a trustworthy result, and `2` for invalid arguments.

## Records and the settings that produce them

The analyzer reads only the records below, and only when the record name starts the log message. A line such as `TEMPDIAG: GUESTFPS ...` is ignored.

| Record | Setting | Contents |
|---|---|---|
| `SWAPTIMING_BEGIN` | `capture_swap_timing_frames = 2..18000` | Capture version, sample count, frame limit, vsync and clock |
| `SWAPTIMING` | same | Swap timestamp, limiter, swap issue and guest-wait time |
| `SWAPTIMING2` | same | Command-worker stall, draw translation and throttle time |
| `SWAPTIMING3` | same | Fence wait and poll time |
| `SWAPTIMING_END` | same | Final sample count |
| `GUESTFPS` | `log_guest_fps_seconds > 0` | Mean guest FPS, frame count and worst frame in each window |
| `SWAPQUEUE` | `log_guest_fps_seconds > 0` | Guest-to-command-processor delay and queued swaps |
| `GPUSTAT` | `log_gpu_diagnostics = true` and `log_guest_fps_seconds > 0` | Per-frame render passes, submits, waits, uploads and draws |
| `GPUTIME` | see below | GPU timestamp calibration, shader use and frame span |

`GPUTIME calibration` is written once at GPU start-up. `GPUTIME shaders` and `GPUTIME span` need the same two settings as `GPUSTAT`.

A capture runs once per launch and holds its output until it finishes. The swaps being measured do no log formatting or I/O, but the flush delays the work that follows, so do not measure the frames right after a capture ends.

### Android launcher limitation

The launcher's Play button rewrites `fable_2.toml`, which affects these settings:

- `capture_swap_timing_frames` is deleted.
- `log_gpu_diagnostics` is forced to `false`.
- `log_guest_fps_seconds` is forced to `0` unless the `log_fps` preference is set, and that preference has no UI control.

Debug builds avoid this when `LauncherActivity` is started with `--ez benchmark true`, because that path opens the game without rewriting the file. The T-010 device runner is planned to use it.

## Metrics

| JSON field | Meaning |
|---|---|
| `source` | `SWAPTIMING` when the log has a capture, otherwise `GUESTFPS` |
| `metrics.sample_count` | Frame intervals for a capture, or total frames across `GUESTFPS` windows |
| `metrics.median_fps` | `1000 / median_ms` for a capture, or the median of the window FPS values for `GUESTFPS` |
| `metrics.median_ms` | Median frame interval |
| `metrics.p95_ms`, `metrics.p99_ms` | 95th and 99th percentile frame intervals |
| `metrics.worst_ms` | Longest frame interval |
| `metrics.over_40ms`, `over_50ms`, `over_100ms` | Intervals strictly longer than each threshold |
| `metrics.windows` | Per-window results |
| `components_ms` | Median, p95, p99 and maximum for each `SWAPTIMING` component |
| `periodic` | Summaries of `GUESTFPS`, `SWAPQUEUE`, `GPUSTAT` and `GPUTIME`, or `null` when absent |
| `route` | The `--route` name, or `null` |
| `device.vulkan_devices`, `device.swapchains` | `Vulkan device` and `Created ... swapchain` lines, which identify the GPU, driver version and present mode |
| `device.driver_package` | Custom driver package id, or `system`. Turnip packages can share a Mesa version, so this is what tells them apart |
| `device.driver_info` | The driver's `driverInfo` string |
| `log_sha256`, `tool_version` | The exact input and analyzer version |
| `warnings` | Malformed auxiliary records that were skipped |

### How values are calculated

- **Frame intervals** are the differences between consecutive `sample_ns` values, so a capture of N samples gives N - 1 intervals.
- **Percentiles use nearest rank.** The result is the sorted value at rank `ceil(percent / 100 * count)`. It is always a real measured interval and is never interpolated. The median is the 50th percentile, so an even-sized set uses the lower middle value.
- **Rounding:** times and FPS are reported to one decimal place, with halves rounded up.
- **Capture windows** are measured from the first sample. An interval belongs to the window containing its end time, and one ending exactly on a boundary belongs to the earlier window. Windows with no completed frame stay in the list with `frames: 0`, so a long stall stays visible. `complete: false` marks a final window that the capture did not fill.
- **Components** skip row 0, because it also holds work from before the first measured interval. `fence_wait` and `fence_poll` overlap other components, so components should not be added up into a frame time.

### What `GUESTFPS` cannot show

`GUESTFPS` reports a mean and a worst frame per window, with no per-frame data. When it is the only source, `median_ms`, the percentiles and the threshold counts are `null` instead of estimates. Its windows use `mean_fps`, and `window_seconds` is `null` because the log does not record it.

### What these numbers do not measure

`SWAPTIMING` measures guest swap processing on the CPU command worker, as its `meaning=cpu_swap_processing` field states. It does not measure when a frame reached the display, so it cannot prove even display cadence on its own. T-030 covers displayed-frame measurement.

## Regression gate

`compare` checks a candidate result against a baseline result. Each file must come from `summarize` and hold exactly one `SWAPTIMING` result.

```powershell
py -3 tools/bench/analyze.py compare baseline.json candidate.json
```

| Exit code | Meaning |
|---|---|
| `0` | Every gated metric is within its limit |
| `1` | At least one gated metric regressed |
| `2` | The comparison was refused, or an argument or input file is invalid |

### Limits

A value exactly at a limit passes. Percent limits are measured against the baseline value.

| Metric | Default limit | Override |
|---|---|---|
| Median FPS | May fall by at most 1% | `--max-fps-drop-pct <pct>` |
| P99 frame time | May rise by at most 5% | `--max-p99-rise-pct <pct>` |
| Frames over 50 ms | May not rise | `--max-over-50ms-rise <n>` |
| Frames over 100 ms | May not rise | `--max-over-100ms-rise <n>` |

Each override is printed above the table. Median, p95 and worst frame time and frames over 40 ms appear in the table as `info` and do not fail the gate. A slow-frame count with a zero baseline has no percentage, so its change shows as `n/a`.

### When a comparison is refused

`compare` refuses results whose identity differs or cannot be confirmed:

- GPU
- Driver version, package or `driverInfo`
- Present mode
- Route
- Frame limit or vsync
- Capture sample count

`--allow-mismatch` runs the comparison anyway and prints every difference above the table. Use it only for a deliberate cross-configuration comparison, because it does not prove that a change is free of regressions.

`GUESTFPS` results are always refused, even with `--allow-mismatch`, because they have no per-frame p99 or slow-frame counts.

### Pull request snippet

Copy this into the pull request and paste the command's table and `RESULT` line where shown:

```markdown
### Regression gate

- Device and display mode:
- Driver:
- Route and cache state:
- Baseline: `docs/perf/baselines/<run>/result.json`
- Candidate: `docs/perf/runs/<run>/result.json`
- Limit overrides or allowed mismatches, with the reason: none

<paste the table and RESULT line here>
```

Example output from synthetic results:

```text
| Metric      | Baseline | Candidate |        Change | Limit | Status     |
| ----------- | -------: | --------: | ------------: | ----: | ---------- |
| Median FPS  |     30.0 |      29.8 |  -0.2 (-0.7%) |   -1% | pass       |
| Median ms   |     33.3 |      33.5 |  +0.2 (+0.6%) |     - | info       |
| P95 ms      |     36.0 |      36.5 |  +0.5 (+1.4%) |     - | info       |
| P99 ms      |     40.0 |      43.0 |  +3.0 (+7.5%) |   +5% | REGRESSION |
| Worst ms    |     45.0 |      52.0 | +7.0 (+15.6%) |     - | info       |
| Over 40 ms  |        2 |         4 |  +2 (+100.0%) |     - | info       |
| Over 50 ms  |        1 |         2 |  +1 (+100.0%) |    +0 | REGRESSION |
| Over 100 ms |        0 |         0 |      +0 (n/a) |    +0 | pass       |

RESULT: regression (p99_ms, over_50ms)
```

## Rejected logs

The analyzer stops with a one-line error instead of reporting a number it cannot trust:

- No `SWAPTIMING` or `GUESTFPS` records
- A capture with zero samples, or with one sample and therefore no interval
- A missing `SWAPTIMING_END`, or any missing `SWAPTIMING`, `SWAPTIMING2` or `SWAPTIMING3` row
- Duplicate rows, rows outside the capture, or rows before `SWAPTIMING_BEGIN` or after `SWAPTIMING_END`
- `SWAPTIMING_BEGIN` and `SWAPTIMING_END` disagreeing on sample count
- More than one capture in a file
- Timestamps that do not strictly increase
- A malformed capture record, or a malformed `GUESTFPS` record when `GUESTFPS` is the only source
- An unsupported capture version or clock

A damaged capture is never replaced by `GUESTFPS` data. Malformed `SWAPQUEUE`, `GPUSTAT` or `GPUTIME` records do not affect frame metrics, so they are skipped and listed in `warnings`. The same applies to a malformed `GUESTFPS` record when a valid capture is present.

Do not commit raw logs from play sessions. Results kept under `docs/perf/` should contain only metrics and hashes.
