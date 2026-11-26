# Renderer and patch options

This is the reference for the 84 options that shape FableDroid's renderer and game patches on Android. It covers every key the launcher writes, every cvar added by `thirdparty/rexglue-sdk-android.patch`, every cvar seeded in `src/core/fable_2_app.h`, and every `[patches]` and `[perf]` key in `fable2_config.toml`.

Values were read from the code at SDK commit `cc5f03d` with the Android patch. `tests/test_renderer_options_doc.py` fails if a covered option is missing from this file.

## How to read the tables

| Column | Meaning |
|---|---|
| Option | The cvar or config key |
| File | Which file owns it: `fable_2.toml` (SDK cvars) or `fable2_config.toml` (game patches) |
| Default | The compiled default |
| Android today | The value on a fresh install using the launcher's default settings |
| Reference | The value that turns off what this option changes, as proven from the code |
| Risk | The kind of failure to suspect |
| Restart | Whether a value changed while the game runs needs a restart to apply |
| Effect and source | The recorded effect, or "Not measured" |

- **Edits to either file are read at launch.** `no` in the Restart column applies only to changes made at runtime, such as through the debug menu.
- **The launcher overwrites its managed keys.** The Play button rewrites them in `fable_2.toml`, so hand edits to those keys do not survive it. Debug builds started with `--ez benchmark true` skip that rewrite.
- **Seeded values are conditional.** `src/core/fable_2_app.h` applies a seed only while the cvar is still at its default source, so a value in `fable_2.toml` takes priority.
- **Measured effects come from the upstream AYN Thor (Adreno 740) work** unless the row says otherwise. Figures in a sequence were measured cumulatively, so they do not add up independently and may not carry over to other GPUs.

## What to know before changing options

- **Setting every option to its Reference does not recreate the unpatched SDK.** Each Reference restores only the code path that option guards, and the patch also makes unconditional changes.
- **1 option has no proven Reference.** `spirv_exponent_bias_bits` selects between two ways of building the exponent, but both read the patch's corrected field.
- **`spirv_strict_zero_multiply` is the reverse of most options.** Its default is `false`, and `true` restores the unpatched behavior.
- **`dog_fur_depth_bias` does nothing on Android.** Its `depth_bias_shader` cvars exist only in the D3D12 backend, so the seed fails and logs a warning. The dog-fur fix is therefore inactive on Android.
- **The tuned values apply to every GPU.** They were measured on Adreno 740, and the launcher also applies them to Adreno 8xx and the Qualcomm driver.

## cpu-wait

**What to look for:** Frame-time spikes, lower FPS, extra input latency, or a hang inside a wait.

| Option | File | Default | Android today | Reference | Risk | Restart | Effect and source |
|---|---|---|---|---|---|---|---|
| `frame_wait_sleep_us` | `fable_2.toml` | `0` | `100` | `0` | `cpu-wait` | no | Part of the CPU wait fixes with GPU diagnostics off: up to 10.3 FPS on an older Turnip build (`ANDROID_PERFORMANCE.md`). |
| `game_idle_sleep_us` | `fable_2.toml` | `0` | `100` | `0` | `cpu-wait` | no | Part of the CPU wait fixes with GPU diagnostics off: up to 10.3 FPS on an older Turnip build (`ANDROID_PERFORMANCE.md`). |
| `gpu_bulk_type0` | `fable_2.toml` | `false` | `true` | `false` | `cpu-wait` | no | Halved CPU time in the GPU command thread (`ANDROID_PERFORMANCE.md`). |
| `guest_max_frames_ahead` | `fable_2.toml` | `0` | `1`, or `0` with low latency off | `0` | `cpu-wait` | no | Not measured. |
| `hotfunc_yield_every` | `fable2_config.toml` | `1` | `1` | `1` | `cpu-wait` | yes | Not measured. |
| `mmio_fast_fault_path` | `fable_2.toml` | `true` | `true` | `false` | `cpu-wait` | no | Not measured. |
| `posix_precise_wait_sleep` | `fable_2.toml` | `false` | `true` | `false` | `cpu-wait` | no | Part of the CPU wait fixes with GPU diagnostics off: up to 10.3 FPS on an older Turnip build (`ANDROID_PERFORMANCE.md`). |
| `ui_android_poll_interval_ms` | `fable_2.toml` | `4` | `4` | `0` | `cpu-wait` | no | Not measured. |
| `vulkan_max_frames_in_flight` | `fable_2.toml` | `3` | `2`, or `3` with triple buffering | `3` | `cpu-wait` | no | Three instead of two: same FPS, one more frame of input latency (`ANDROID_PERFORMANCE.md`). |
| `vulkan_submit_on_primary_buffer_end` | `fable_2.toml` | `true` | `true` | `true` | `cpu-wait` | no | Not measured. |

## shader-codegen

**What to look for:** Wrong colors or lighting, spiked or exploding geometry, flicker, black materials, or a driver crash while compiling.

| Option | File | Default | Android today | Reference | Risk | Restart | Effect and source |
|---|---|---|---|---|---|---|---|
| `spirv_allow_contraction_pixel` | `fable_2.toml` | `false` | `false` | `false` | `shader-codegen` | yes | Floating-point contraction: neutral or worse (`ANDROID_PERFORMANCE.md`). |
| `spirv_allow_contraction_vertex` | `fable_2.toml` | `false` | `false` | `false` | `shader-codegen` | yes | Floating-point contraction: neutral or worse (`ANDROID_PERFORMANCE.md`). |
| `spirv_alpha_to_coverage_1x_bayer` | `fable_2.toml` | `false` | `true`, or `false` with fine foliage off | `false` | `shader-codegen` | yes | Not measured. |
| `spirv_alpha_to_coverage_variant` | `fable_2.toml` | `true` | `true` | `false` | `shader-codegen` | no | Not measured. |
| `spirv_branchless_endian_swap` | `fable_2.toml` | `true` | `true` | `false` | `shader-codegen` | yes | Not measured. |
| `spirv_exponent_bias_bits` | `fable_2.toml` | `false` | `false` | unknown: both values use the patch's word 3 exponent, while the unpatched SDK read word 4 | `shader-codegen` | yes | Measured regression (`ANDROID_PERFORMANCE.md`). |
| `spirv_gamma_only_branch` | `fable_2.toml` | `false` | `true` | `false` | `shader-codegen` | yes | Part of the cheaper texture-fetch paths: 18.1 to 21.7 FPS (`ANDROID_PERFORMANCE.md`). |
| `spirv_implicit_lod_sampling` | `fable_2.toml` | `true` | `true` | `false` | `shader-codegen` | yes | 17.1 to 18.1 FPS (`ANDROID_PERFORMANCE.md`). |
| `spirv_implicit_lod_sampling_1d` | `fable_2.toml` | `false` | `false` | `false` | `shader-codegen` | yes | Not measured. |
| `spirv_relaxed_precision_pixel` | `fable_2.toml` | `false` | `false` | `false` | `shader-codegen` | yes | FP16 relaxed precision: neutral or worse (`ANDROID_PERFORMANCE.md`). |
| `spirv_split_temp_registers` | `fable_2.toml` | `true` | `true` | `false` | `shader-codegen` | yes | Not measured. |
| `spirv_strict_zero_multiply` | `fable_2.toml` | `false` | `false` | `true` | `shader-codegen` | yes | Relaxed handling (`false`): 14.6 to 17.1 FPS (`ANDROID_PERFORMANCE.md`). |
| `spirv_structured_forward_jumps` | `fable_2.toml` | `true` | `true` | `false` | `shader-codegen` | yes | Not measured. |
| `spirv_texel_reads_unguarded` | `fable_2.toml` | `true` | `true` | n/a: inert when `vulkan_shared_memory_texel_reads` is `false` | `shader-codegen` | yes | Not measured. |
| `spirv_unconditional_unsigned_sample` | `fable_2.toml` | `false` | `true` | `false` | `shader-codegen` | yes | Part of the cheaper texture-fetch paths: 18.1 to 21.7 FPS (`ANDROID_PERFORMANCE.md`). |
| `spirv_unsigned_texture_variant` | `fable_2.toml` | `false` | `true` | `false` | `shader-codegen` | no | Part of the cheaper texture-fetch paths: 18.1 to 21.7 FPS (`ANDROID_PERFORMANCE.md`). |
| `spirv_vfetch_single_binding_switch` | `fable_2.toml` | `true` | `true` | `false` | `shader-codegen` | yes | Not measured. |
| `tessellation_factor_cap` | `fable_2.toml` | `0.0` | `8.0` | `0.0` | `shader-codegen` | no | Factor 6 instead of 8: neutral or worse (`ANDROID_PERFORMANCE.md`). |

## memory-upload

**What to look for:** Corrupted or stale textures, broken geometry, black items, or lost-device errors.

| Option | File | Default | Android today | Reference | Risk | Restart | Effect and source |
|---|---|---|---|---|---|---|---|
| `readback_memexport` | `fable_2.toml` | `true` | `false` (seeded) | `true` | `memory-upload` | no | With upload-first: gameplay 4.5 to 7.2 FPS on Adreno 740, visuals unchanged (`src/core/fable_2_app.h`). |
| `readback_resolve_force_addresses` | `fable_2.toml` | `""` | `""` (seeded from `hero_dog_texture_readback`) | `""` | `memory-upload` | no | Not measured. |
| `vulkan_robust_buffer_access` | `fable_2.toml` | `true` | `true` | `true` | `memory-upload` | yes | Disabling it was slightly slower (`ANDROID_PERFORMANCE.md`). |
| `vulkan_shared_memory_texel_reads` | `fable_2.toml` | `true` | `true` | `false` | `memory-upload` | yes | Not measured. |
| `vulkan_shared_memory_upload_first` | `fable_2.toml` | `false` | `true` | `false` | `memory-upload` | no | 26.9 to 29.6 FPS in the menu scene once MSAA was off (`ANDROID_PERFORMANCE.md`). |
| `vulkan_shared_memory_upload_first_subpage` | `fable_2.toml` | `false` | `false` | `false` | `memory-upload` | no | Not measured. |
| `vulkan_vertex_buffer_always_request` | `fable_2.toml` | `false` | `false` | `false` | `memory-upload` | no | Not measured. |

## render-target

**What to look for:** Rectangular shadows, garbage at screen edges, a missing sky, or wrong depth.

| Option | File | Default | Android today | Reference | Risk | Restart | Effect and source |
|---|---|---|---|---|---|---|---|
| `depth_bias_shader` | `fable_2.toml` | `""` | not registered: defined only in the D3D12 backend | `""` | `render-target` | n/a | No effect on Android. The seed fails and logs a warning. |
| `depth_bias_shader_units` | `fable_2.toml` | `0` | not registered: defined only in the D3D12 backend | `0` | `render-target` | n/a | No effect on Android. The seed fails and logs a warning. |
| `draw_resolution_scale_x` | `fable_2.toml` | `1` | `1`, launcher allows 1 to 3 | `1` | `render-target` | yes | 3x failed at start-up on the Adreno 840 Qualcomm driver (`docs/investigations/adreno8xx-triage.md`). |
| `draw_resolution_scale_y` | `fable_2.toml` | `1` | `1`, launcher allows 1 to 3 | `1` | `render-target` | yes | 3x failed at start-up on the Adreno 840 Qualcomm driver (`docs/investigations/adreno8xx-triage.md`). |
| `execute_unclipped_draw_vs_on_cpu` | `fable_2.toml` | `false` | `true` (seeded) | `false` | `render-target` | no | Fixes the purple bottom strip with texture morphing (`src/core/fable_2_app.h`). |
| `rt_clear_quad_skip_targets` | `fable_2.toml` | `""` | `""` | `""` | `render-target` | no | Not measured. |
| `rt_clear_quad_vs_hashes` | `fable_2.toml` | `""` | `1E6883FCCDE1F688` (seeded) | `""` | `render-target` | no | Skips redundant depth copies for the D3D clear quad (`src/core/fable_2_app.h`). Size of the gain is not recorded. |
| `rt_transfer_skip_d2c_pitches` | `fable_2.toml` | `""` | `""` | `""` | `render-target` | no | Not measured. |
| `rt_transfer_skip_mask` | `fable_2.toml` | `0` | `4` (seeded) | `0` | `render-target` | no | Fixes the cyan sky and missing world (`src/core/fable_2_app.h`). |
| `vulkan_7e3_render_target_as_r11g11b10` | `fable_2.toml` | `false` | `false` | `false` | `render-target` | yes | Alternative render-target formats: neutral or worse (`ANDROID_PERFORMANCE.md`). |
| `vulkan_dynamic_rendering` | `fable_2.toml` | `true` | `true` | `true` | `render-target` | yes | Not measured. |

## presentation

**What to look for:** White or black screens, tearing, judder, wrong picture shape, or audio output problems.

| Option | File | Default | Android today | Reference | Risk | Restart | Effect and source |
|---|---|---|---|---|---|---|---|
| `audio_force_stereo` | `fable_2.toml` | `true` on Android, `false` elsewhere | `true` | `false` | `presentation` | yes | Not measured. Keeps center-channel dialogue audible on built-in speakers (SDK patch comment). |
| `present_render_pass_clear` | `fable_2.toml` | `true` | `false` | `true` | `presentation` | yes | `false` saves about 0.24 ms of GPU time per frame (`ANDROID_PERFORMANCE.md`). |
| `swap_post_effect` | `fable_2.toml` | `"none"` | `"fxaa"`, or `"none"` with FXAA off | `"none"` | `presentation` | yes | Not measured. |
| `vulkan_allow_present_mode_fifo_relaxed` | `fable_2.toml` | `true` | `false` | `true` | `presentation` | yes | Not measured. `true` lets the swapchain use FIFO_RELAXED, which tears when a frame is late. `false` keeps Android tear-free (T-029). Android surfaces normally offer only MAILBOX and FIFO, so conformant drivers pick the same mode either way. |
| `vulkan_allow_present_mode_immediate` | `fable_2.toml` | `true` | `false` | `true` | `presentation` | yes | Not measured. `true` makes IMMEDIATE, which tears, the first choice. `false` keeps Android tear-free (T-029). Android surfaces normally offer only MAILBOX and FIFO, so conformant drivers pick the same mode either way. |
| `vulkan_android_custom_driver` | `fable_2.toml` | `""` | `"turnip27"` on Adreno 7xx, `""` on other GPUs, or the chosen driver | `""` | `presentation` | yes | Mesa Turnip v27: 10.3 to 15.5 FPS. Gen8 V38: same average FPS as v27 (`ANDROID_PERFORMANCE.md`). |
| `vulkan_android_driver_env` | `fable_2.toml` | `""` | deleted from the file on Play | `""` | `presentation` | yes | GMEM and tiling driver options: neutral or worse (`ANDROID_PERFORMANCE.md`). |

## game-patch

**What to look for:** Changes to game logic, physics, unlocked content, intros, or character textures.

| Option | File | Default | Android today | Reference | Risk | Restart | Effect and source |
|---|---|---|---|---|---|---|---|
| `disable_motion_blur` | `fable2_config.toml` | `false` | `false` | `false` | `game-patch` | yes | Not measured. |
| `disable_texture_morphing` | `fable2_config.toml` | `true` on Android, `false` elsewhere | `true` | `false` | `game-patch` | yes | Not measured. Keeps base skin textures because morphed ones render black on Vulkan (`config/fable2_config.toml`). |
| `dog_fur_depth_bias` | `fable2_config.toml` | `true` | `true` | `false` | `game-patch` | yes | No effect on Android, because its target cvars exist only in the D3D12 backend. |
| `fable2_disable_msaa` | `fable_2.toml` | `false` | `true`, or `false` with MSAA on | `false` | `game-patch` | yes | 21.7 to 26.9 FPS (`ANDROID_PERFORMANCE.md`). |
| `fable2_skip_intros` | `fable_2.toml` | `false` | `false`, launcher toggle | `false` | `game-patch` | yes | Not measured. |
| `hero_dog_texture_readback` | `fable2_config.toml` | `false` | `false` | `false` | `game-patch` | yes | Not measured. |
| `high_tick_rate` | `fable2_config.toml` | `true` on Android, `false` elsewhere | `true` | `false` | `game-patch` | yes | 25.5 to 25.0 FPS, about 2% cost, on AYN Thor (`config/fable2_config.toml`). |
| `high_tick_rate_stairs_fix` | `fable2_config.toml` | `true` | `true` | `false` | `game-patch` | yes | Not measured. Only acts while `high_tick_rate` is on. |
| `higher_hf_tick_rate` | `fable2_config.toml` | `true` on Android, `false` elsewhere | `true` | `false` | `game-patch` | yes | Not measured. Requires `high_tick_rate`. |
| `realtime_texture_morphing` | `fable2_config.toml` | `true` | `true` | `false` | `game-patch` | yes | Not measured. |
| `skip_intro_videos` | `fable2_config.toml` | `false` | `false` | `false` | `game-patch` | yes | Not measured. |
| `unlock_ce` | `fable2_config.toml` | `true` | `true` | `false` | `game-patch` | yes | Not measured. |
| `unlock_website` | `fable2_config.toml` | `true` | `true` | `false` | `game-patch` | yes | Not measured. |

## diagnostic

**What to look for:** Extra logging and CPU cost. The `diag_*` options deliberately break rendering and must stay off in benchmarks.

| Option | File | Default | Android today | Reference | Risk | Restart | Effect and source |
|---|---|---|---|---|---|---|---|
| `capture_swap_timing_frames` | `fable_2.toml` | `0` | deleted from the file on Play | `0` | `diagnostic` | no | Produces `SWAPTIMING` records (`docs/BENCHMARKING.md`). |
| `diag_clear_ps` | `fable_2.toml` | `""` | `""` | `""` | `diagnostic` | no | Not measured. |
| `diag_clear_skip_draw` | `fable_2.toml` | `false` | `false` | `false` | `diagnostic` | no | Not measured. |
| `diag_draw_vertex_div` | `fable_2.toml` | `0` | `0` | `0` | `diagnostic` | no | Not measured. |
| `diag_render_area_max_h` | `fable_2.toml` | `0` | `0` | `0` | `diagnostic` | no | Not measured. |
| `diag_scissor_percent` | `fable_2.toml` | `0` | `0` | `0` | `diagnostic` | no | Not measured. |
| `diag_skip_ps` | `fable_2.toml` | `""` | `""` | `""` | `diagnostic` | no | Not measured. |
| `gputime_period_ns` | `fable_2.toml` | `0.0` | `0.0` | `0.0` | `diagnostic` | no | Not measured. |
| `log_gpu_diagnostics` | `fable_2.toml` | `true` | `false` | `false` | `diagnostic` | no | Part of the CPU wait fixes with GPU diagnostics off: up to 10.3 FPS on an older Turnip build (`ANDROID_PERFORMANCE.md`). |
| `log_guest_fps_seconds` | `fable_2.toml` | `0` | `0`, or `5` with the hidden `log_fps` preference | `0` | `diagnostic` | no | Produces `GUESTFPS` and `SWAPQUEUE` records (`docs/BENCHMARKING.md`). |
| `log_resolve_targets` | `fable_2.toml` | `false` | `false` | `false` | `diagnostic` | no | Not measured. |
| `log_ui_event_stats` | `fable_2.toml` | `false` | `false` | `false` | `diagnostic` | no | Not measured. |
| `rt_clear_quad_log` | `fable_2.toml` | `false` | `false` | `false` | `diagnostic` | no | Not measured. |
| `rt_diag_lines` | `fable_2.toml` | `1500` | `1500` | n/a: only read when `rt_diag_start_seconds` is above 0 | `diagnostic` | no | Not measured. |
| `rt_diag_start_seconds` | `fable_2.toml` | `0` | `0` | `0` | `diagnostic` | no | Not measured. |
| `rt_transfer_log_d2c_pitch` | `fable_2.toml` | `0` | `0` | `0` | `diagnostic` | no | Not measured. |
| `texture_diag_end` | `fable_2.toml` | `0` | `0` | `0` | `diagnostic` | no | Not measured. |
| `texture_diag_start` | `fable_2.toml` | `0` | `0` | `0` | `diagnostic` | no | Not measured. |

## Profiles

`android/app/src/main/java/org/fable2/recomp/GpuProfile.java` owns the 33 keys the launcher manages. Every profile writes the same keys in the same order, so switching profiles leaves no stale values.

| Used for | Profiles | Values today |
|---|---|---|
| Adreno 6xx | `adreno6xx-system`, `adreno6xx-turnip` | Same as the Adreno 740 tuning |
| Adreno 7xx | `adreno7xx-system`, `adreno7xx-turnip` | The Adreno 740 tuning |
| Adreno 8xx | `adreno8xx-system`, `adreno8xx-turnip` | Same as the Adreno 740 tuning |
| Any other or undetected GPU | `generic` | Same as the Adreno 740 tuning |
| Compatibility mode on any GPU | `compatibility` | Managed reference values |

An empty driver id selects the `-system` profile, and any bundled or downloaded driver selects `-turnip`. `AUTO` is byte-identical to the 0.6.3 launcher's output for every GPU, driver and launcher setting, except for the two tear-free present-mode keys added by T-029. The hardware profiles stay identical until measured device tasks change them.

The mode is stored in the `graphics_profile` preference and defaults to `AUTO`. T-019 adds the launcher control.

### What Compatibility changes

- **Uses the Reference value:** managed `shader-codegen`, `memory-upload`, `render-target` and `presentation` keys. Resolution scale, FXAA and fine foliage settings are ignored in this mode.
- **Keeps its current value:** `cpu-wait`, `diagnostic` and `game-patch` keys, including the MSAA, intro-skip, frame-queue and FPS-logging settings.
- **Stays tear-free.** `vulkan_allow_present_mode_immediate` and `vulkan_allow_present_mode_fifo_relaxed` are `presentation` keys, but Compatibility keeps them `false`, because only MAILBOX and FIFO are allowed on Android (T-029).
- **Keeps the selected driver.** `vulkan_android_custom_driver` is a `presentation` key, but forcing its empty Reference would make Compatibility impossible to test on Turnip.

### What Compatibility does not change

Compatibility does not recreate the unpatched renderer. It can only set the managed keys, because every profile must write the same key set and `AUTO` output must not change. These options stay away from their Reference value:

- **Shader code:** `spirv_alpha_to_coverage_variant`, `spirv_branchless_endian_swap`, `spirv_implicit_lod_sampling`, `spirv_strict_zero_multiply` and `spirv_vfetch_single_binding_switch`
- **Guest memory:** `readback_memexport`
- **Render targets:** `execute_unclipped_draw_vs_on_cpu`, `rt_clear_quad_vs_hashes` and `rt_transfer_skip_mask`
- **Audio output:** `audio_force_stereo`

`spirv_exponent_bias_bits` has no proven Reference, and the patch's unconditional changes also remain.

## Keeping this file current

When a change adds or changes one of these options, update its row in the same pull request. Mark a Reference as `unknown: <reason>` when the code does not prove it. Run the check with:

```powershell
py -3 -m unittest tests.test_renderer_options_doc
```
