package org.fable2.recomp;

import static org.junit.Assert.*;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import org.junit.Test;

public class GpuProfileTest {
    private static final String DOWNLOAD = "download-" + "a".repeat(64);
    private static final String[] DRIVERS = { "", "turnip27", DOWNLOAD };
    private static final String[] RENDERERS = { "Adreno (TM) 660", "Adreno (TM) 740", "Adreno (TM) 750",
        "Adreno (TM) 830", "Adreno (TM) 840", "Mali-G78", "Mali-G715", "", null };
    private static final String[] HARDWARE = { "adreno7xx-turnip", "adreno7xx-system", "adreno8xx-system",
        "adreno8xx-turnip", "adreno6xx-system", "adreno6xx-turnip", "generic" };
    private static final Set<String> REFERENCE_CLASSES =
        new HashSet<>(Arrays.asList("shader-codegen", "memory-upload", "render-target", "presentation"));

    // Frozen copy of Settings.TUNED and writeGameConfig as of release 0.6.3. Do not update
    // this to match a code change: AUTO must keep producing exactly these bytes.
    private static final String[][] FROZEN_TUNED = {
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
        // T-029 deliberately added these two keys after 0.6.3 (Principle 4: tear-free by
        // construction). Every other value and the key order stay as released.
        {"vulkan_allow_present_mode_immediate", "false"},
        {"vulkan_allow_present_mode_fifo_relaxed", "false"},
    };
    // Present modes that can tear. Every profile, Compatibility included, must disable them.
    private static final String[] TEARING_MODES = {
        "vulkan_allow_present_mode_immediate", "vulkan_allow_present_mode_fifo_relaxed" };
    private static final String[] FROZEN_REMOVED = { "capture_swap_timing_frames", "vulkan_android_driver_env" };

    private static LinkedHashMap<String, String> frozenManaged(int scale, boolean msaa, boolean triple, boolean skip,
            boolean lowLatency, boolean fxaa, boolean foliage, String driver, boolean logFps) {
        LinkedHashMap<String, String> managed = new LinkedHashMap<>();
        for (String[] kv : FROZEN_TUNED) managed.put(kv[0], kv[1]);
        managed.put("draw_resolution_scale_x", Integer.toString(scale));
        managed.put("draw_resolution_scale_y", Integer.toString(scale));
        managed.put("fable2_disable_msaa", msaa ? "false" : "true");
        managed.put("vulkan_max_frames_in_flight", triple ? "3" : "2");
        managed.put("fable2_skip_intros", skip ? "true" : "false");
        managed.put("guest_max_frames_ahead", lowLatency ? "1" : "0");
        managed.put("swap_post_effect", fxaa ? "\"fxaa\"" : "\"none\"");
        managed.put("spirv_alpha_to_coverage_1x_bayer", foliage ? "true" : "false");
        managed.put("vulkan_android_custom_driver", "\"" + driver + "\"");
        managed.put("log_guest_fps_seconds", logFps ? "5" : "0");
        return managed;
    }

    private static List<String> frozenMerge(List<String> lines, Map<String, String> managed) {
        Set<String> removed = new HashSet<>(Arrays.asList(FROZEN_REMOVED));
        Set<String> written = new HashSet<>();
        List<String> out = new ArrayList<>();
        boolean inTable = false;
        for (String line : lines) {
            String trimmed = line.trim();
            if (trimmed.startsWith("[")) inTable = true;
            int eq = trimmed.indexOf('=');
            String key = !inTable && eq > 0 && !trimmed.startsWith("#") ? trimmed.substring(0, eq).trim() : null;
            if (key != null && removed.contains(key)) continue;
            if (key != null && managed.containsKey(key)) {
                if (written.add(key)) out.add(key + " = " + managed.get(key));
                continue;
            }
            out.add(line);
        }
        int insert = 0;
        while (insert < out.size() && !out.get(insert).trim().startsWith("[")) ++insert;
        List<String> added = new ArrayList<>();
        for (Map.Entry<String, String> e : managed.entrySet())
            if (!written.contains(e.getKey())) added.add(e.getKey() + " = " + e.getValue());
        out.addAll(insert, added);
        return out;
    }

    private static List<List<String>> existingConfigs() {
        return Arrays.asList(
            Collections.emptyList(),
            Arrays.asList("# user comment", "", "unmanaged_key = 7"),
            Arrays.asList("gpu_bulk_type0 = false", "tessellation_factor_cap=2.0", "gpu_bulk_type0 = true"),
            Arrays.asList("capture_swap_timing_frames = 100", "vulkan_android_driver_env = \"TU_DEBUG=sysmem\"", "kept = 1"),
            Arrays.asList("kept = 1", "[table]", "gpu_bulk_type0 = false", "capture_swap_timing_frames = 2"),
            Arrays.asList("# gpu_bulk_type0 = false", "  draw_resolution_scale_x = 9  ", "[a]", "b = 1"));
    }

    private static byte[] bytes(List<String> lines) {
        StringBuilder text = new StringBuilder();
        for (String line : lines) text.append(line).append('\n');
        return text.toString().getBytes(StandardCharsets.UTF_8);
    }

    private static GpuProfile.Choices defaults(String driver) {
        return new GpuProfile.Choices(1, false, false, false, true, true, true, driver, false);
    }

    private static Map<String, String[]> documentedRows() throws IOException {
        File directory = new File(System.getProperty("user.dir")).getAbsoluteFile();
        File doc = null;
        while (directory != null && doc == null) {
            File candidate = new File(directory, "docs/RENDERER_OPTIONS.md");
            if (candidate.isFile()) doc = candidate;
            directory = directory.getParentFile();
        }
        assertNotNull("docs/RENDERER_OPTIONS.md not found", doc);
        Map<String, String[]> rows = new HashMap<>();
        for (String line : Files.readAllLines(doc.toPath(), StandardCharsets.UTF_8)) {
            if (!line.startsWith("| `")) continue;
            String[] cells = line.substring(1, line.length() - 1).split("\\|");
            for (int i = 0; i < cells.length; ++i) cells[i] = cells[i].trim();
            rows.put(cells[0].replace("`", ""), cells);
        }
        return rows;
    }

    private static String firstCode(String cell) {
        int start = cell.indexOf('`');
        return start < 0 ? cell : cell.substring(start + 1, cell.indexOf('`', start + 1));
    }

    @Test public void resolvesEachBenchGpuByDriver() {
        String[][] cases = {
            {"Adreno (TM) 660", "adreno6xx"}, {"Adreno (TM) 740", "adreno7xx"}, {"Adreno (TM) 750", "adreno7xx"},
            {"Adreno (TM) 830", "adreno8xx"}, {"Adreno (TM) 840", "adreno8xx"},
        };
        for (String[] c : cases) {
            assertEquals(c[0], c[1] + "-system", GpuProfile.resolve(c[0], "", GpuProfile.Mode.AUTO));
            assertEquals(c[0], c[1] + "-system", GpuProfile.resolve(c[0], null, GpuProfile.Mode.AUTO));
            assertEquals(c[0], c[1] + "-turnip", GpuProfile.resolve(c[0], "turnip27", GpuProfile.Mode.AUTO));
            assertEquals(c[0], c[1] + "-turnip", GpuProfile.resolve(c[0], DOWNLOAD, GpuProfile.Mode.AUTO));
        }
    }

    @Test public void otherGpusResolveToGenericOnEveryDriver() {
        for (String renderer : new String[] { "Mali-G78", "Mali-G715", "", null, "Adreno (TM) 530", "PowerVR Rogue" })
            for (String driver : DRIVERS)
                assertEquals(renderer + "/" + driver, "generic", GpuProfile.resolve(renderer, driver, GpuProfile.Mode.AUTO));
    }

    @Test public void compatibilityModeWinsForEveryGpuAndDriver() {
        for (String renderer : RENDERERS)
            for (String driver : DRIVERS)
                assertEquals("compatibility", GpuProfile.resolve(renderer, driver, GpuProfile.Mode.COMPATIBILITY));
    }

    @Test public void modeParsingDefaultsToAuto() {
        assertEquals(GpuProfile.Mode.COMPATIBILITY, GpuProfile.Mode.parse("COMPATIBILITY"));
        assertEquals(GpuProfile.Mode.AUTO, GpuProfile.Mode.parse("AUTO"));
        for (String invalid : new String[] { null, "", "compatibility", "ULTRA" })
            assertEquals(GpuProfile.Mode.AUTO, GpuProfile.Mode.parse(invalid));
    }

    @Test public void autoOutputIsByteIdenticalToFrozenLauncher() {
        for (String renderer : RENDERERS) for (String driver : DRIVERS) {
            String profile = GpuProfile.resolve(renderer, driver, GpuProfile.Mode.AUTO);
            for (int scale = 1; scale <= 3; ++scale) for (int flags = 0; flags < 128; ++flags) {
                boolean msaa = (flags & 1) != 0, triple = (flags & 2) != 0, skip = (flags & 4) != 0;
                boolean low = (flags & 8) != 0, fxaa = (flags & 16) != 0, foliage = (flags & 32) != 0, fps = (flags & 64) != 0;
                LinkedHashMap<String, String> actual = GpuProfile.managed(profile,
                    new GpuProfile.Choices(scale, msaa, triple, skip, low, fxaa, foliage, driver, fps));
                LinkedHashMap<String, String> frozen = frozenManaged(scale, msaa, triple, skip, low, fxaa, foliage, driver, fps);
                assertEquals(new ArrayList<>(frozen.entrySet()), new ArrayList<>(actual.entrySet()));
                if (scale == 1 || flags == 127) {
                    for (List<String> existing : existingConfigs())
                        assertArrayEquals(profile, bytes(frozenMerge(existing, frozen)),
                            bytes(Settings.mergeManagedKeys(existing, actual)));
                }
            }
        }
    }

    @Test public void hardwareProfilesKeepTodaysValues() {
        LinkedHashMap<String, String> expected = frozenManaged(1, false, false, false, true, true, true, "", false);
        for (String id : HARDWARE)
            assertEquals(id, new ArrayList<>(expected.entrySet()), new ArrayList<>(GpuProfile.keys(id).entrySet()));
    }

    @Test public void everyProfileWritesTheSameKeysInTheSameOrder() {
        List<String> expected = new ArrayList<>(GpuProfile.keys("generic").keySet());
        assertEquals(33, expected.size());
        for (String id : HARDWARE) assertEquals(id, expected, new ArrayList<>(GpuProfile.keys(id).keySet()));
        assertEquals(expected, new ArrayList<>(GpuProfile.keys("compatibility").keySet()));
    }

    @Test public void compatibilityMatchesTheOptionsReference() throws IOException {
        Map<String, String[]> rows = documentedRows();
        Map<String, String> compatibility = GpuProfile.keys("compatibility");
        Map<String, String> hardware = GpuProfile.keys("generic");
        Set<String> keptInCompatibility = new HashSet<>(Arrays.asList(TEARING_MODES));
        keptInCompatibility.add("vulkan_android_custom_driver");
        for (Map.Entry<String, String> entry : compatibility.entrySet()) {
            String key = entry.getKey();
            String[] row = rows.get(key);
            assertNotNull(key + " is not documented", row);
            assertEquals(key, 8, row.length);
            String risk = firstCode(row[5]);
            if (REFERENCE_CLASSES.contains(risk) && !keptInCompatibility.contains(key))
                assertEquals(key, firstCode(row[4]), entry.getValue());
            else
                assertEquals(key, hardware.get(key), entry.getValue());
        }
    }

    @Test public void everyProfileDisablesTearingPresentModes() {
        List<String> ids = new ArrayList<>(Arrays.asList(HARDWARE));
        ids.add("compatibility");
        for (String id : ids) {
            for (String key : TEARING_MODES) assertEquals(id + " " + key, "false", GpuProfile.keys(id).get(key));
            for (String driver : DRIVERS) for (int flags = 0; flags < 128; ++flags) {
                LinkedHashMap<String, String> out = GpuProfile.managed(id, new GpuProfile.Choices(1 + flags % 3,
                    (flags & 1) != 0, (flags & 2) != 0, (flags & 4) != 0, (flags & 8) != 0, (flags & 16) != 0,
                    (flags & 32) != 0, driver, (flags & 64) != 0));
                for (String key : TEARING_MODES) assertEquals(id + " " + key, "false", out.get(key));
            }
            // MAILBOX vs FIFO is left to T-030 and T-032, so the launcher must not manage it.
            assertFalse(id, GpuProfile.keys(id).containsKey("vulkan_allow_present_mode_mailbox"));
        }
    }

    @Test public void compatibilityLocksReferenceKeysAndKeepsOtherChoices() {
        Map<String, String> out = GpuProfile.managed("compatibility",
            new GpuProfile.Choices(3, true, true, true, false, true, true, DOWNLOAD, true));
        assertEquals("1", out.get("draw_resolution_scale_x"));
        assertEquals("1", out.get("draw_resolution_scale_y"));
        assertEquals("\"none\"", out.get("swap_post_effect"));
        assertEquals("false", out.get("spirv_alpha_to_coverage_1x_bayer"));
        assertEquals("false", out.get("fable2_disable_msaa"));
        assertEquals("3", out.get("vulkan_max_frames_in_flight"));
        assertEquals("true", out.get("fable2_skip_intros"));
        assertEquals("0", out.get("guest_max_frames_ahead"));
        assertEquals("\"" + DOWNLOAD + "\"", out.get("vulkan_android_custom_driver"));
        assertEquals("5", out.get("log_guest_fps_seconds"));
    }

    @Test public void switchingProfilesLeavesNoStaleValues() {
        List<String> start = Arrays.asList("kept = 1", "[table]", "gpu_bulk_type0 = false");
        LinkedHashMap<String, String> auto = GpuProfile.managed("generic", defaults("turnip27"));
        LinkedHashMap<String, String> compatibility = GpuProfile.managed("compatibility", defaults("turnip27"));
        List<String> first = Settings.mergeManagedKeys(start, auto);
        List<String> switched = Settings.mergeManagedKeys(first, compatibility);
        assertEquals(first.size(), switched.size());
        for (Map.Entry<String, String> e : compatibility.entrySet())
            assertTrue(e.getKey(), switched.contains(e.getKey() + " = " + e.getValue()));
        assertEquals(first, Settings.mergeManagedKeys(switched, auto));
    }

    @Test public void keysReturnsAnIndependentCopy() {
        GpuProfile.keys("generic").put("gpu_bulk_type0", "changed");
        assertEquals("true", GpuProfile.keys("generic").get("gpu_bulk_type0"));
    }

    @Test(expected = IllegalArgumentException.class)
    public void unknownProfileIsRejected() {
        GpuProfile.keys("adreno9xx-turnip");
    }
}
