# Adreno 8xx failure-log triage (T-016)

Prepared 2026-11-22 from the logs attached to upstream `hardsilinder/FableDroid` issues #1 (AYN Odin 3, Adreno 830) and #5 (RedMagic 11 Pro, Adreno 840). This covers all 18 runs: five logs from #1 and thirteen inside the `logs.zip` attached to #5. No raw logs are committed. Nothing here has been reproduced on the OnePlus 13 or Z Fold 8 Ultra yet, so every cause below is a hypothesis until its experiment runs.

Log names are written as `<issue>/<file>` because both issues contain a `fable_2_001.log` to `fable_2_005.log`.

## Findings

1. **One run of 18 got past the intro stage.** It used Turnip Gen8 V38_Patched on Adreno 830 and created 605 pipelines over 263.6 s. `#1/fable_2_005.log`: "download-2bbb279a".
2. **V38_Patched was never run on Adreno 840.** Issue #5 tried the system driver, bundled Turnip, V32, V33, V37 and regular V38. The launcher's recommended list for 8xx cannot show `_Patched` packages, because `DriverPolicy.matchingDownload` only accepts `Turnip_Gen8_V[0-9]+\.zip`. They appear only under "All packages".
3. **Regular V38 and V38_Patched report the same Mesa commit.** `#1/fable_2_001.log`: "Mesa 27.0.0-devel (git-f28352d117)". `#1/fable_2_005.log`: "Mesa 27.0.0-devel (git-f28352d117)". Their logged limits and features are also identical, so the difference is in the package's patch set. The upstream release describes that set as WinNative patches and hacks.
4. **The system-driver white screen is not a stuck shader compile.** Every pipeline that started also completed in all 18 logs, with zero failures. The GPU thread was still active 53 s after the last new pipeline in `#5/fable_2_001.log`: "WAIT_REG_MEM long-waits: 755 in 1000 ms".
5. **Three issue #5 runs are a different bug.** At 3x draw scale the Qualcomm driver could not create the scaled resolve buffer and the renderer failed in under a second. `#5/fable_2_009.log`: "Failed to create a scaled resolve buffer with 4608 MB address space".
6. **The logs cannot settle the intro-skip question.** Neither `fable2_skip_intros` nor `skip_intro_videos` is written to the log. The two long Qualcomm runs do argue against a simple "wait for the fade" explanation (see [Intro-stall check](#intro-stall-check)).

## How to read the evidence

| Stage | Meaning | Log marker |
|---|---|---|
| S1 | Vulkan device and swapchain created | `Created <w>x<h> swapchain` |
| S2 | First guest frame reached the presenter | `paint flow guest_active=true` |
| S3 | Second shader stage created, probably the intro-video path | `Creating graphics pipeline state with VS BD2477FEDE6BEAB1` |
| S4 | New pipelines appear more than 1 s after S3, meaning post-intro content | Any later `Creating graphics pipeline state` line |

S3 as the intro-video path is an inference. It fits the reporter's descriptions: the system driver showed the Microsoft intro and reached S3, while regular V38 showed no intro and did not.

Limits that affect every conclusion:

- **Guest swaps are only proven for about the first 3 seconds.** The presenter diagnostic stops after 50 paints, and `GUESTFPS` is off because the launcher writes `log_guest_fps_seconds = 0` unless a preference with no UI control is set.
- **Input is not logged.** A run that stops at S3 may or may not have received a button press.
- **App version and device model are not logged.** Issue #1's reporter replaced an Odin 3 Ultra with an Odin 3 Max before the V38_Patched result, so the one working run may differ from the failing runs in hardware as well as driver.
- **Two build generations are mixed.** Issue #1 logs use seed set A and issue #5 logs use seed set B, which adds `execute_unclipped_draw_vs_on_cpu=true`. That seed first shipped in v0.6.1, so the V38_Patched run appears to come from an older build and may not carry over to current builds.
- **Issue screenshots were not analysed.** Symptoms come from the reporters' text.

Seed set A is `user_language`, `keyboard_gamepad_map`, `mouse_look`, `mouse_look_scale`, `readback_resolve_force_addresses`, `rt_transfer_skip_mask=4`, `rt_clear_quad_vs_hashes=1E6883FCCDE1F688` and `readback_memexport=false`. Every run also applied the High Tick Rate and Higher HF Tick Rate patches.

Driver packages were identified by matching the SHA-256 in each log's driver directory against the published asset digests of `StevenMXZ/Adreno-Tools-Drivers`:

| Hash prefix | Release asset | Release tag |
|---|---|---|
| `a31e5a51` | `Turnip_Gen8_V32.zip` | `v32` |
| `b8b67b04` | `Turnip_Gen8_V33.zip` | `v33` |
| `d0335542` | `Turnip_Gen8_V37.zip` | `v37` |
| `642cb514` | `Turnip_Gen8_V38.zip` | `v27.0.0-R1` |
| `2bbb279a` | `Turnip_Gen8_V38_Patched.zip` | `v27.0.0-R1` |

V39 and V39_Patched were published on 2026-11-07, after every log here, and are untested.

## Run matrix

| Log | GPU | Driver | Scale | Length | Last stage | Pipelines | Outcome |
|---|---|---|---|---|---|---|---|
| `#1/fable_2_001.log` | Adreno 830 | Turnip Gen8 V38 | 1x | 23.3 s | S2 | 3 | No S3 in time |
| `#1/fable_2_002.log` | Adreno 830 | Turnip Gen8 V38 | 1x | 14.8 s | S2 | 3 | No S3 in time |
| `#1/fable_2_003.log` | Adreno 830 | Bundled Turnip v27 | 1x | 10.0 s | S2 | 3 | No S3 in time |
| `#1/fable_2_004.log` | Adreno 830 | Qualcomm system driver | 1x | 34.1 s | S3 | 6 | Stopped after S3 |
| `#1/fable_2_005.log` | Adreno 830 | Turnip Gen8 V38_Patched | 1x | 263.6 s | S4 | 605 | Reached post-intro content |
| `#5/fable_2_001.log` | Adreno 840 | Qualcomm system driver | 1x | 63.6 s | S3 | 6 | Stopped after S3 |
| `#5/fable_2_002.log` | Adreno 840 | Qualcomm system driver | 1x | 11.2 s | S2 | 2 | No S3 in time |
| `#5/fable_2_003.log` | Adreno 840 | Qualcomm system driver | 1x | 7.7 s | S2 | 2 | Too short to judge |
| `#5/fable_2_004.log` | Adreno 840 | Turnip Gen8 V38 | 1x | 9.7 s | S2 | 2 | Too short to judge |
| `#5/fable_2_005.log` | Adreno 840 | Bundled Turnip v27 | 1x | 2.8 s | S2 | 2 | Too short to judge |
| `#5/fable_2_006.log` | Adreno 840 | Turnip Gen8 V37 | 1x | 8.6 s | S3 | 5 | Ended right after S3 |
| `#5/fable_2_007.log` | Adreno 840 | Turnip Gen8 V32 | 1x | 18.2 s | S2 | 2 | No S3 in time |
| `#5/fable_2_008.log` | Adreno 840 | Turnip Gen8 V32 | 3x | 7.7 s | S2 | 2 | Too short to judge |
| `#5/fable_2_009.log` | Adreno 840 | Qualcomm system driver | 3x | 0.7 s | S1 | 0 | Init failure at 3x |
| `#5/fable_2_010.log` | Adreno 840 | Qualcomm system driver | 3x | 0.7 s | S1 | 0 | Init failure at 3x |
| `#5/fable_2_011.log` | Adreno 840 | Qualcomm system driver | 3x | 0.7 s | S1 | 0 | Init failure at 3x |
| `#5/fable_2_012.log` | Adreno 840 | Qualcomm system driver | 1x | 15.1 s | S2 | 2 | No S3 in time |
| `#5/fable_2_013.log` | Adreno 840 | Turnip Gen8 V33 | 1x | 12.6 s | S2 | 2 | No S3 in time |

"No S3 in time" means the run outlasted the latest S3 seen on that GPU (4.0 s on 830, 10.0 s on 840). If intro skip was on, a missing S3 is expected, so these runs show only that S4 was not reached before they ended.

## Capability differences

Present mode was MAILBOX (`1`) with swapchain format `37` in all 18 runs, so swapchain selection does not separate working from failing runs.

| Property | Qualcomm 830 and 840 | V38, V38_Patched, V37, bundled v27 | V33 | V32 |
|---|---|---|---|---|
| `maxTexelBufferElements` | 134217728 | 536870911 | 536870911 | 134217728 |
| `maxStorageBufferRange` | 2147483647 | 2147483647 | 2147483647 | 134217728 |
| `maxFragmentInputComponents` | 112 | 128 | 124 | 124 |
| `minUniformBufferOffsetAlignment` | 256 | 64 | 64 | 64 |
| `minStorageBufferOffsetAlignment` | 64 | 4 | 4 | 4 |
| `nonSeamlessCubeMap` | absent | present | present | present |
| `VK_EXT_memory_budget` enabled | no | yes | yes | yes |
| `VK_EXT_non_seamless_cube_map` enabled | no | yes | yes | yes |
| Present modes offered | 1, 2, 1000111000, 1000111001 | 1, 2 | 1, 2 | 1, 2 |

Evidence: `#5/fable_2_002.log`: "storage_buffer_bytes=2147483647 texel_buffer_elements=134217728". `#1/fable_2_005.log`: "storage_buffer_bytes=2147483647 texel_buffer_elements=536870911". `#5/fable_2_007.log`: "storage_buffer_bytes=134217728 texel_buffer_elements=134217728".

The warning `WriteRegister index out of bounds: 28685` is not a failure signal, because it also appears in the working run. `#1/fable_2_005.log`: "CommandProcessor::WriteRegister index out of bounds: 28685".

## Intro-stall check

The upstream maintainer suggested the white screen could be the Lionhead logo fading in, and a commenter on #5 asked whether logos were being skipped. The result is:

- **Skip state: not determinable.** The hook in `src/core/fable2_hooks.cpp` logs nothing and no log dumps the cvar. The launcher default is off.
- **Slow fade: not supported by the two long runs.** The working run created its next pipelines 23.1 s after launch. `#1/fable_2_005.log`: "Creating graphics pipeline state with VS 1E6883FCCDE1F688". The 830 system-driver run created nothing after 4.03 s and ran to 34.1 s. The 840 system-driver run created nothing after 10.00 s and ran to 63.6 s.
- **Uncached compile stall: rejected for these logs.** Each `Creating graphics pipeline state` line has a matching completion diagnostic, so no compile was in flight when any log ended.
- **Still open: the shorter runs.** Twelve of the 13 issue #5 runs ended within 19 s or failed at start-up, so they cannot rule out a slow intro on their own.

## Ranked hypotheses

Rank reflects how well the logs support each cause. Confidence is a judgement, not a measurement.

**Experiment setup.** The launcher rewrites its managed keys in `fable_2.toml` on every launch and deletes `vulkan_android_driver_env`. Experiments that change `log_guest_fps_seconds`, a `spirv_*` key, a `vulkan_shared_memory_*` key or the driver environment therefore need a debug launcher that leaves those keys alone, or T-019's override support. Unless stated otherwise, run each experiment on both the OnePlus 13 (830) and Z Fold 8 Ultra (840). Keep any screenshots local (principle 9).

### System-driver white screen (for T-022)

1. **SYS-1: The game keeps running while the Qualcomm path presents a wrong image. Confidence: medium.**
   - Evidence: no device loss or pipeline failure, and GPU-thread activity continues. `#5/fable_2_001.log`: "WAIT_REG_MEM long-waits: 755 in 1000 ms".
   - Experiment on OnePlus 13 and Z Fold 8 Ultra: system driver, `log_guest_fps_seconds = 5`, `fable2_skip_intros = false`, wait 120 s, press A at 30 s and 60 s.
   - Confirmed if `GUESTFPS` stays near 30 during the white screen. Falsified if it stops, which points to a guest or GPU wait hang.
2. **SYS-2: The full 512 MiB texel view sits exactly at Qualcomm's limit and misreads guest memory. Confidence: medium-low.**
   - Evidence: the shared-memory view needs 134217728 R32 elements, exactly the limit Qualcomm reports. `#1/fable_2_004.log`: "texel_buffer_elements=134217728". Turnip V38 has about four times that headroom.
   - Experiment: `vulkan_shared_memory_texel_reads = false` on both phones, 1x, 120 s.
   - Confirmed if the run reaches S4 or the image appears. Falsified if it stops at S3 unchanged.
3. **SYS-3: A shader-translation option tuned on Adreno 740 breaks on Qualcomm's compiler. Confidence: low-medium.**
   - Evidence: `Settings.TUNED` applies the same renderer keys to every GPU, and the logs show no explicit error. The Qualcomm runs also get past S2, so basic rendering works.
   - Experiment with T-020, one key at a time: `spirv_structured_forward_jumps`, `spirv_split_temp_registers`, `spirv_gamma_only_branch`, `spirv_unsigned_texture_variant`, each set to `false`.
   - Confirmed if one flip reaches S4 on both phones and reverting it restores the white screen.
4. **SYS-4: The white screen is only a slow Lionhead fade. Confidence: low.**
   - Evidence against: the 34.1 s and 63.6 s runs above. `#1/fable_2_004.log`: "Creating graphics pipeline state with VS BD2477FEDE6BEAB1".
   - Experiment: compare `fable2_skip_intros = true` and `false`, 120 s each, on both phones.
   - Confirmed if only the skip-on run reaches gameplay and the skip-off run gets there after a longer wait.

### Turnip Gen8 white screen and corruption (for T-023)

1. **TUR-1: Gen8 Turnip needs the `_Patched` set to boot this game. Confidence: medium-high.**
   - Evidence: same Mesa commit, different outcome. `#1/fable_2_001.log` stops at S2 and `#1/fable_2_005.log` reaches S4. Hardware and app build are possible confounders.
   - Experiment: install `Turnip_Gen8_V38_Patched.zip`, then `Turnip_Gen8_V39_Patched.zip`, from "All packages" on OnePlus 13 and Z Fold 8 Ultra, using a current build.
   - Confirmed if both phones reach S4. If only the 830 passes, the remaining fault is specific to the 840 or OneUI.
2. **TUR-2: Texture tiling comes from Turnip's compression or tile-memory path. Confidence: medium-low.**
   - Evidence: the reporter describes "Texture tiling", while `#1/fable_2_005.log` logs no renderer warning after S4, so the driver is not reporting a failure.
   - Experiment on V38_Patched: `vulkan_android_driver_env = "TU_DEBUG=noubwc"`, then `"TU_DEBUG=sysmem"`, at the same save and location. Confirm `noubwc` exists in that package's Mesa first.
   - Confirmed if the tiling disappears under one flag and returns without it.
3. **TUR-3: The temporary spiked models come from out-of-order guest-memory uploads or vertex fetch. Confidence: low-medium.**
   - Evidence: the reporter says geometry "goes wild" for a few seconds and then recovers, which fits stale vertex data better than a permanent shader fault. The logs contain no direct signal.
   - Experiment: `vulkan_shared_memory_upload_first = false`, then `vulkan_shared_memory_texel_reads = false`, in a scene that reproduces it.
   - Confirmed if the spikes stop under one setting across a repeatable route.
4. **TUR-4: The hacks that let V38_Patched boot also cause the corruption. Confidence: low.**
   - Evidence: V38_Patched is the only package that reached gameplay, so there is no clean package to compare it with.
   - Experiment: compare V39 and V39_Patched on the same route.
   - Confirmed if a package boots without the patch set and renders cleanly.

### Separate defect: 3x scale on the Qualcomm driver

`#5/fable_2_010.log`: "Failed to initialize the texture cache". The buffer size is 512 MiB times the scale area, so 3x needs 4608 MB of address space. This should become a clear start-up error or a limited scale option (T-024), and it should not be counted as a white-screen run. The 2x case is untested.

## Suggested run order

1. V38_Patched and V39_Patched on OnePlus 13 and Z Fold 8 Ultra (TUR-1). This needs no code change.
2. System driver with `GUESTFPS` on (SYS-1), which decides between a rendering fault and a hang.
3. `fable2_skip_intros` on and off (SYS-4), which removes the intro question from every later test.
4. The texel-read flip (SYS-2), then T-020 bisection (SYS-3).
5. Corruption experiments (TUR-2 to TUR-4), once a driver reaches gameplay on both phones.

## Proposed plan updates

These are not applied, because `MASTER_PLAN.md` is coordinator-owned:

- The issue #5 row says "every driver tried". It should list the six packages actually tried and note that V38_Patched was not one of them.
- T-011 or T-012 should log app version, device model, draw scale, `fable2_skip_intros` and a periodic guest-frame count, since their absence limits this triage.
- T-018 or T-059 should decide whether `_Patched` Gen8 packages appear in the 8xx recommended list, after TUR-1 runs.

## Per-log evidence

Boot-file warnings shared by every run (`lhdebug.log`, `update:\`, `build_version.txt`, optional `lang.ini` files, `gamecontrollerdb.txt` and the dog-fur cvar notice) are left out of the "first warning" field.

### `#1/fable_2_001.log`

- **Device and driver:** Adreno 830, Turnip Gen8 V38 (package `642cb514`). `driverInfo`: Mesa 27.0.0-devel (git-f28352d117). Driver version `0x6863063`.
- **Vulkan:** device API 1.4.363, instance API 1.3.0, conformance 1.4.6.1.
- **Config:** seed set A, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.021s, `#1/fable_2_001.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** 1.153s: "Skipping Vulkan frame presentation due to async placeholder draw usage in".
- **Pipelines:** 3 started, 3 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.789s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 11.605s.
- **Run end:** 23.328s, clean shutdown logged.

### `#1/fable_2_002.log`

- **Device and driver:** Adreno 830, Turnip Gen8 V38 (package `642cb514`). `driverInfo`: Mesa 27.0.0-devel (git-f28352d117). Driver version `0x6863063`.
- **Vulkan:** device API 1.4.363, instance API 1.3.0, conformance 1.4.6.1.
- **Config:** seed set A, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 0.839s, `#1/fable_2_002.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** 0.958s: "Skipping Vulkan frame presentation due to async placeholder draw usage in".
- **Pipelines:** 3 started, 3 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.576s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 14.588s.
- **Run end:** 14.771s, clean shutdown logged.

### `#1/fable_2_003.log`

- **Device and driver:** Adreno 830, Bundled Turnip v27 (bundled). `driverInfo`: Mesa 27.0.0-devel (git-c2d85dae14). Driver version `0x6863063`.
- **Vulkan:** device API 1.4.363, instance API 1.3.0, conformance 1.4.6.1.
- **Config:** seed set A, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 0.864s, `#1/fable_2_003.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** 1.001s: "Skipping Vulkan frame presentation due to async placeholder draw usage in".
- **Pipelines:** 3 started, 3 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.639s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 2.100s.
- **Run end:** 9.973s, clean shutdown logged.

### `#1/fable_2_004.log`

- **Device and driver:** Adreno 830, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 3967b80d3b, dated 02/12/26, compiler E031.47.18.49. Driver version `0x80320048`.
- **Vulkan:** device API 1.3.284, instance API 1.3.0, conformance 1.3.8.0.
- **Config:** seed set A, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S3 at 4.010s, `#1/fable_2_004.log`: "Creating graphics pipeline state with VS BD2477FEDE6BEAB1".
- **First warning or error after it:** 33.851s: "QueueUserCallback: signal delivery failed (-1)".
- **Pipelines:** 6 started, 6 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.964s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 29.198s.
- **Run end:** 34.051s, clean shutdown logged.

### `#1/fable_2_005.log`

- **Device and driver:** Adreno 830, Turnip Gen8 V38_Patched (package `2bbb279a`). `driverInfo`: Mesa 27.0.0-devel (git-f28352d117). Driver version `0x6863063`.
- **Vulkan:** device API 1.4.363, instance API 1.3.0, conformance 1.4.6.1.
- **Config:** seed set A, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S4 at 23.142s, `#1/fable_2_005.log`: "Creating graphics pipeline state with VS 1E6883FCCDE1F688".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 605 started, 605 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.579s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 263.632s.
- **Run end:** 263.632s, log stops with no shutdown line.

### `#5/fable_2_001.log`

- **Device and driver:** Adreno 840, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 718a07726a, dated 05/29/26, compiler E031.50.19.29. Driver version `0x8034A02C`.
- **Vulkan:** device API 1.4.295, instance API 1.4.0, conformance 1.4.1.0.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S3 at 9.971s, `#5/fable_2_001.log`: "Creating graphics pipeline state with VS BD2477FEDE6BEAB1".
- **First warning or error after it:** 63.565s: "VulkanPresenter: Failed to get Vulkan surface capabilities". Final error at 63.565s: "VulkanPresenter: Failed to get Vulkan surface capabilities".
- **Pipelines:** 6 started, 6 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 3.308s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 63.587s.
- **Run end:** 63.587s, log stops with no shutdown line.

### `#5/fable_2_002.log`

- **Device and driver:** Adreno 840, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 718a07726a, dated 05/29/26, compiler E031.50.19.29. Driver version `0x8034A02C`.
- **Vulkan:** device API 1.4.295, instance API 1.4.0, conformance 1.4.1.0.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.483s, `#5/fable_2_002.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 3.238s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 11.180s.
- **Run end:** 11.180s, log stops with no shutdown line.

### `#5/fable_2_003.log`

- **Device and driver:** Adreno 840, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 718a07726a, dated 05/29/26, compiler E031.50.19.29. Driver version `0x8034A02C`.
- **Vulkan:** device API 1.4.295, instance API 1.4.0, conformance 1.4.1.0.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.483s, `#5/fable_2_003.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 3.205s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 7.732s.
- **Run end:** 7.732s, log stops with no shutdown line.

### `#5/fable_2_004.log`

- **Device and driver:** Adreno 840, Turnip Gen8 V38 (package `642cb514`). `driverInfo`: Mesa 27.0.0-devel (git-f28352d117). Driver version `0x6863063`.
- **Vulkan:** device API 1.4.363, instance API 1.4.0, conformance 1.4.6.1.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.194s, `#5/fable_2_004.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.955s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 9.743s.
- **Run end:** 9.743s, log stops with no shutdown line.

### `#5/fable_2_005.log`

- **Device and driver:** Adreno 840, Bundled Turnip v27 (bundled). `driverInfo`: Mesa 27.0.0-devel (git-c2d85dae14). Driver version `0x6863063`.
- **Vulkan:** device API 1.4.363, instance API 1.4.0, conformance 1.4.6.1.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.114s, `#5/fable_2_005.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.817s. `GUESTFPS` absent, so later swaps are unproven. No GPU-thread wait line.
- **Run end:** 2.817s, log stops with no shutdown line.

### `#5/fable_2_006.log`

- **Device and driver:** Adreno 840, Turnip Gen8 V37 (package `d0335542`). `driverInfo`: Mesa 26.3.0-devel (git-a995eaeda4). Driver version `0x6802063`.
- **Vulkan:** device API 1.4.363, instance API 1.4.0, conformance 1.4.6.1.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S3 at 8.550s, `#5/fable_2_006.log`: "Creating graphics pipeline state with VS BD2477FEDE6BEAB1".
- **First warning or error after it:** 8.573s: "Skipping Vulkan frame presentation due to async placeholder draw usage in".
- **Pipelines:** 5 started, 5 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.831s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 8.557s.
- **Run end:** 8.573s, log stops with no shutdown line.

### `#5/fable_2_007.log`

- **Device and driver:** Adreno 840, Turnip Gen8 V32 (package `a31e5a51`). `driverInfo`: not reported. Driver version `0x6801063`.
- **Vulkan:** device API 1.4.352, instance API 1.4.0, conformance 1.4.0.0.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.034s, `#5/fable_2_007.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.767s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 13.528s.
- **Run end:** 18.202s, log stops with no shutdown line.

### `#5/fable_2_008.log`

- **Device and driver:** Adreno 840, Turnip Gen8 V32 (package `a31e5a51`). `driverInfo`: not reported. Driver version `0x6801063`.
- **Vulkan:** device API 1.4.352, instance API 1.4.0, conformance 1.4.0.0.
- **Config:** seed set B, 3x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.236s, `#5/fable_2_008.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 3.020s. `GUESTFPS` absent, so later swaps are unproven. No GPU-thread wait line.
- **Run end:** 7.749s, log stops with no shutdown line.

### `#5/fable_2_009.log`

- **Device and driver:** Adreno 840, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 718a07726a, dated 05/29/26, compiler E031.50.19.29. Driver version `0x8034A02C`.
- **Vulkan:** device API 1.4.295, instance API 1.4.0, conformance 1.4.1.0.
- **Config:** seed set B, 3x draw scale. Intro-skip state not logged.
- **Last stage reached:** S1 at 0.114s, `#5/fable_2_009.log`: "Created 2688x1216 swapchain with format 37, color space 0, presentation mode".
- **First warning or error after it:** 0.195s: "Vulkan draw resolution scaling is experimental and may not affect all". Final error at 0.690s: "Failed to initialize the texture cache".
- **Pipelines:** 0 started, 0 completed, 0 failed.
- **Guest swaps:** no guest frame reached the presenter. `GUESTFPS` absent, so later swaps are unproven. No GPU-thread wait line.
- **Run end:** 0.690s, log stops with no shutdown line.

### `#5/fable_2_010.log`

- **Device and driver:** Adreno 840, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 718a07726a, dated 05/29/26, compiler E031.50.19.29. Driver version `0x8034A02C`.
- **Vulkan:** device API 1.4.295, instance API 1.4.0, conformance 1.4.1.0.
- **Config:** seed set B, 3x draw scale. Intro-skip state not logged.
- **Last stage reached:** S1 at 0.116s, `#5/fable_2_010.log`: "Created 2688x1216 swapchain with format 37, color space 0, presentation mode".
- **First warning or error after it:** 0.183s: "Vulkan draw resolution scaling is experimental and may not affect all". Final error at 0.665s: "Failed to initialize the texture cache".
- **Pipelines:** 0 started, 0 completed, 0 failed.
- **Guest swaps:** no guest frame reached the presenter. `GUESTFPS` absent, so later swaps are unproven. No GPU-thread wait line.
- **Run end:** 0.665s, log stops with no shutdown line.

### `#5/fable_2_011.log`

- **Device and driver:** Adreno 840, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 718a07726a, dated 05/29/26, compiler E031.50.19.29. Driver version `0x8034A02C`.
- **Vulkan:** device API 1.4.295, instance API 1.4.0, conformance 1.4.1.0.
- **Config:** seed set B, 3x draw scale. Intro-skip state not logged.
- **Last stage reached:** S1 at 0.117s, `#5/fable_2_011.log`: "Created 2688x1216 swapchain with format 37, color space 0, presentation mode".
- **First warning or error after it:** 0.193s: "Vulkan draw resolution scaling is experimental and may not affect all". Final error at 0.687s: "Failed to initialize the texture cache".
- **Pipelines:** 0 started, 0 completed, 0 failed.
- **Guest swaps:** no guest frame reached the presenter. `GUESTFPS` absent, so later swaps are unproven. No GPU-thread wait line.
- **Run end:** 0.687s, log stops with no shutdown line.

### `#5/fable_2_012.log`

- **Device and driver:** Adreno 840, Qualcomm system driver (no custom driver loaded). `driverInfo`: Driver Build: 718a07726a, dated 05/29/26, compiler E031.50.19.29. Driver version `0x8034A02C`.
- **Vulkan:** device API 1.4.295, instance API 1.4.0, conformance 1.4.1.0.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.469s, `#5/fable_2_012.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 3.180s. `GUESTFPS` absent, so later swaps are unproven. No GPU-thread wait line.
- **Run end:** 15.147s, log stops with no shutdown line.

### `#5/fable_2_013.log`

- **Device and driver:** Adreno 840, Turnip Gen8 V33 (package `b8b67b04`). `driverInfo`: Mesa 26.2.0-devel (git-471f8dc66c). Driver version `0x6801063`.
- **Vulkan:** device API 1.4.354, instance API 1.4.0, conformance 1.4.0.0.
- **Config:** seed set B, 1x draw scale. Intro-skip state not logged.
- **Last stage reached:** S2 at 1.081s, `#5/fable_2_013.log`: "paint flow guest_active=true guest=1280x720".
- **First warning or error after it:** none (only the boot-file warnings every run shares).
- **Pipelines:** 2 started, 2 completed, 0 failed.
- **Guest swaps:** 48 guest frames consumed in the capped 50-paint diagnostic, ending at 2.783s. `GUESTFPS` absent, so later swaps are unproven. Last GPU-thread wait line at 2.817s.
- **Run end:** 12.628s, log stops with no shutdown line.
