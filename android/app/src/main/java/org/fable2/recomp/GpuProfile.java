package org.fable2.recomp;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Set;

/**
 * Renderer settings for each GPU family and driver. This class owns every key the
 * launcher manages in fable_2.toml and has no Android dependencies, so it is covered
 * by JVM tests. See docs/RENDERER_OPTIONS.md.
 */
final class GpuProfile {
    enum Mode {
        AUTO, COMPATIBILITY;

        /** Unknown or missing stored values fall back to AUTO. */
        static Mode parse(String value) {
            return "COMPATIBILITY".equals(value) ? COMPATIBILITY : AUTO;
        }
    }

    /** The launcher settings that feed managed keys. */
    static final class Choices {
        final int resolutionScale;
        final boolean msaa, tripleBuffering, skipIntros, lowLatency, fxaa, fineFoliage, logFps;
        final String driver;

        Choices(int resolutionScale, boolean msaa, boolean tripleBuffering, boolean skipIntros, boolean lowLatency,
                boolean fxaa, boolean fineFoliage, String driver, boolean logFps) {
            this.resolutionScale = resolutionScale;
            this.msaa = msaa;
            this.tripleBuffering = tripleBuffering;
            this.skipIntros = skipIntros;
            this.lowLatency = lowLatency;
            this.fxaa = fxaa;
            this.fineFoliage = fineFoliage;
            this.driver = driver;
            this.logFps = logFps;
        }
    }

    static final String COMPATIBILITY_ID = "compatibility";
    static final String GENERIC_ID = "generic";
    private static final Set<String> IDS = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
        "adreno7xx-turnip", "adreno7xx-system", "adreno8xx-system", "adreno8xx-turnip",
        "adreno6xx-system", "adreno6xx-turnip", GENERIC_ID, COMPATIBILITY_ID)));

    // Measured on Adreno 740 with Turnip (see docs/ANDROID_PERFORMANCE.md). Every hardware
    // profile uses these values until a measured device task changes one.
    private static final String[][] TUNED_BASE = {
        {"frame_wait_sleep_us", "100"},
        {"posix_precise_wait_sleep", "true"},
        {"game_idle_sleep_us", "100"},
        {"log_gpu_diagnostics", "false"},
        {"vulkan_robust_buffer_access", "true"},
        {"gpu_bulk_type0", "true"},
        {"vulkan_shared_memory_upload_first", "true"},
        {"vulkan_submit_on_primary_buffer_end", "true"},
        {"vulkan_dynamic_rendering", "true"},
        {"spirv_split_temp_registers", "true"},
        {"vulkan_shared_memory_texel_reads", "true"},
        {"spirv_structured_forward_jumps", "true"},
        {"spirv_implicit_lod_sampling_1d", "false"},
        {"spirv_allow_contraction_pixel", "false"},
        {"spirv_allow_contraction_vertex", "false"},
        {"spirv_relaxed_precision_pixel", "false"},
        {"tessellation_factor_cap", "8.0"},
        {"spirv_unconditional_unsigned_sample", "true"},
        {"spirv_gamma_only_branch", "true"},
        {"spirv_unsigned_texture_variant", "true"},
        {"present_render_pass_clear", "false"},
    };

    // Keys driven by launcher settings, with the values for a fresh install.
    private static final String[][] USER_DEFAULTS = {
        {"draw_resolution_scale_x", "1"},
        {"draw_resolution_scale_y", "1"},
        {"fable2_disable_msaa", "true"},
        {"vulkan_max_frames_in_flight", "2"},
        {"fable2_skip_intros", "false"},
        {"guest_max_frames_ahead", "1"},
        {"swap_post_effect", "\"fxaa\""},
        {"spirv_alpha_to_coverage_1x_bayer", "true"},
        {"vulkan_android_custom_driver", "\"\""},
        {"log_guest_fps_seconds", "0"},
    };

    // Reference values from docs/RENDERER_OPTIONS.md for managed shader-codegen, memory-upload,
    // render-target and presentation keys. The driver is deliberately absent: Compatibility
    // must be testable on each driver, so the selected driver always stays in effect.
    private static final String[][] COMPATIBILITY_VALUES = {
        {"vulkan_robust_buffer_access", "true"},
        {"vulkan_shared_memory_upload_first", "false"},
        {"vulkan_dynamic_rendering", "true"},
        {"spirv_split_temp_registers", "false"},
        {"vulkan_shared_memory_texel_reads", "false"},
        {"spirv_structured_forward_jumps", "false"},
        {"spirv_implicit_lod_sampling_1d", "false"},
        {"spirv_allow_contraction_pixel", "false"},
        {"spirv_allow_contraction_vertex", "false"},
        {"spirv_relaxed_precision_pixel", "false"},
        {"tessellation_factor_cap", "0.0"},
        {"spirv_unconditional_unsigned_sample", "false"},
        {"spirv_gamma_only_branch", "false"},
        {"spirv_unsigned_texture_variant", "false"},
        {"present_render_pass_clear", "true"},
        {"draw_resolution_scale_x", "1"},
        {"draw_resolution_scale_y", "1"},
        {"swap_post_effect", "\"none\""},
        {"spirv_alpha_to_coverage_1x_bayer", "false"},
    };

    private GpuProfile() {}

    /** Returns the profile id. A null or empty driver id means the system driver. */
    static String resolve(String renderer, String driverId, Mode mode) {
        if (mode == Mode.COMPATIBILITY) return COMPATIBILITY_ID;
        String family = DriverPolicy.family(renderer);
        String prefix;
        switch (family) {
            case "Adreno 6xx": prefix = "adreno6xx"; break;
            case "Adreno 7xx": prefix = "adreno7xx"; break;
            case "Adreno 8xx": prefix = "adreno8xx"; break;
            default: return GENERIC_ID;
        }
        return prefix + (driverId == null || driverId.isEmpty() ? "-system" : "-turnip");
    }

    /** Returns a new map of every managed key for the profile, in stable order. */
    static LinkedHashMap<String, String> keys(String profileId) {
        if (!IDS.contains(profileId)) throw new IllegalArgumentException("Unknown GPU profile: " + profileId);
        LinkedHashMap<String, String> keys = new LinkedHashMap<>();
        for (String[] kv : TUNED_BASE) keys.put(kv[0], kv[1]);
        for (String[] kv : USER_DEFAULTS) keys.put(kv[0], kv[1]);
        if (COMPATIBILITY_ID.equals(profileId)) {
            for (String[] kv : COMPATIBILITY_VALUES) keys.put(kv[0], kv[1]);
        }
        return keys;
    }

    /** Returns the values to write: the profile plus the launcher choices it allows. */
    static LinkedHashMap<String, String> managed(String profileId, Choices choices) {
        LinkedHashMap<String, String> managed = keys(profileId);
        if (!COMPATIBILITY_ID.equals(profileId)) {
            String scale = Integer.toString(choices.resolutionScale);
            managed.put("draw_resolution_scale_x", scale);
            managed.put("draw_resolution_scale_y", scale);
            managed.put("swap_post_effect", choices.fxaa ? "\"fxaa\"" : "\"none\"");
            managed.put("spirv_alpha_to_coverage_1x_bayer", choices.fineFoliage ? "true" : "false");
        }
        managed.put("fable2_disable_msaa", choices.msaa ? "false" : "true");
        managed.put("vulkan_max_frames_in_flight", choices.tripleBuffering ? "3" : "2");
        managed.put("fable2_skip_intros", choices.skipIntros ? "true" : "false");
        managed.put("guest_max_frames_ahead", choices.lowLatency ? "1" : "0");
        managed.put("vulkan_android_custom_driver", "\"" + choices.driver + "\"");
        managed.put("log_guest_fps_seconds", choices.logFps ? "5" : "0");
        return managed;
    }
}
