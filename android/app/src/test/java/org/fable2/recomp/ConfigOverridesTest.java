package org.fable2.recomp;

import static org.junit.Assert.*;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.Test;

public class ConfigOverridesTest {
    private static ConfigOverrides parse(String... lines) {
        return ConfigOverrides.parse(String.join("\n", lines).getBytes(StandardCharsets.UTF_8));
    }

    private static Map<String, String> map(String... kv) {
        LinkedHashMap<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put(kv[i], kv[i + 1]);
        return m;
    }

    private static void assertRejected(String line, String reasonPart) {
        ConfigOverrides o = parse("ok_key = 1", line);
        assertEquals(line, 1, o.count());
        assertEquals(line, 1, o.rejectedLines.size());
        String reason = o.rejectedLines.get(0);
        assertTrue(line + " -> " + reason, reason.startsWith("line 2: "));
        assertTrue(line + " -> " + reason, reason.contains(reasonPart));
    }

    // Parsing

    @Test public void topLevelScalarsBecomeEngineOverrides() {
        ConfigOverrides o = parse(
            "capture_swap_timing_frames = 600",
            "tessellation_factor_cap = -1.5e2",
            "vulkan_dynamic_rendering = false",
            "vulkan_android_driver_env = \"TU_DEBUG=noconform\"",
            "empty_string = \"\"");
        assertEquals(map("capture_swap_timing_frames", "600", "tessellation_factor_cap", "-1.5e2",
            "vulkan_dynamic_rendering", "false", "vulkan_android_driver_env", "\"TU_DEBUG=noconform\"",
            "empty_string", "\"\""), o.engineOverrides);
        assertTrue(o.gameConfigOverrides.isEmpty());
        assertTrue(o.rejectedLines.isEmpty());
        assertEquals(5, o.count());
    }

    @Test public void fable2ConfigSectionBecomesGameConfigOverrides() {
        ConfigOverrides o = parse(
            "present_render_pass_clear = true",
            "[fable2_config]",
            "disable_texture_morphing = false",
            "high_tick_rate = false");
        assertEquals(map("present_render_pass_clear", "true"), o.engineOverrides);
        assertEquals(map("disable_texture_morphing", "false", "high_tick_rate", "false"), o.gameConfigOverrides);
        assertTrue(o.rejectedLines.isEmpty());
        assertEquals(3, o.count());
    }

    @Test public void commentsBlankLinesTrailingCommentsAndCrlfAreAccepted() {
        ConfigOverrides o = ConfigOverrides.parse(("\uFEFF# tester pins\r\n"
            + "\r\n"
            + "   # indented comment\r\n"
            + "frame_wait_sleep_us = 200   # was 100\r\n"
            + "swap_post_effect = \"none # not a comment\" # a comment\r\n"
            + "[fable2_config] # game patches\r\n"
            + "high_tick_rate=false#x\r\n").getBytes(StandardCharsets.UTF_8));
        assertTrue(o.rejectedLines.toString(), o.rejectedLines.isEmpty());
        assertEquals(map("frame_wait_sleep_us", "200", "swap_post_effect", "\"none # not a comment\""),
            o.engineOverrides);
        assertEquals(map("high_tick_rate", "false"), o.gameConfigOverrides);
    }

    @Test public void unknownSectionsAndTheirKeysAreRejected() {
        ConfigOverrides o = parse(
            "a = 1",
            "[patches]",
            "disable_motion_blur = true",
            "[[array]]",
            "b = 2",
            "[fable2_config]",
            "high_tick_rate = false");
        assertEquals(map("a", "1"), o.engineOverrides);
        assertEquals(map("high_tick_rate", "false"), o.gameConfigOverrides);
        assertEquals(4, o.rejectedLines.size());
        assertTrue(o.rejectedLines.get(0), o.rejectedLines.get(0).startsWith("line 2: unknown section [patches]"));
        assertTrue(o.rejectedLines.get(1), o.rejectedLines.get(1).startsWith("line 3: "));
        assertTrue(o.rejectedLines.get(1), o.rejectedLines.get(1).contains("unknown section"));
        assertTrue(o.rejectedLines.get(2), o.rejectedLines.get(2).startsWith("line 4: unknown section"));
        assertTrue(o.rejectedLines.get(3), o.rejectedLines.get(3).startsWith("line 5: "));
    }

    @Test public void repeatedFable2ConfigHeaderIsRejectedButKeepsTheSection() {
        ConfigOverrides o = parse("[fable2_config]", "a = 1", "[fable2_config]", "b = 2");
        assertEquals(map("a", "1", "b", "2"), o.gameConfigOverrides);
        assertEquals(1, o.rejectedLines.size());
        assertTrue(o.rejectedLines.get(0), o.rejectedLines.get(0).startsWith("line 3: "));
    }

    @Test public void duplicateKeysKeepTheFirstValueAndNameBothLines() {
        ConfigOverrides o = parse("a = 1", "a = 2", "[fable2_config]", "a = 3", "a = 4");
        assertEquals(map("a", "1"), o.engineOverrides);
        assertEquals(map("a", "3"), o.gameConfigOverrides);
        assertEquals(Arrays.asList("line 2: duplicate key a (first set on line 1)",
            "line 5: duplicate key a (first set on line 4)"), o.rejectedLines);
    }

    @Test public void oversizedFileAppliesNothing() {
        StringBuilder s = new StringBuilder("a = 1\n");
        while (s.length() <= ConfigOverrides.MAX_BYTES) s.append("# padding padding padding padding\n");
        ConfigOverrides o = ConfigOverrides.parse(s.toString().getBytes(StandardCharsets.UTF_8));
        assertEquals(0, o.count());
        assertEquals(1, o.rejectedLines.size());
        assertTrue(o.rejectedLines.get(0), o.rejectedLines.get(0).contains("64 KB"));
    }

    @Test public void fileAtTheSizeLimitIsAccepted() {
        StringBuilder s = new StringBuilder("a = 1\n");
        while (s.length() < ConfigOverrides.MAX_BYTES) s.append('#');
        s.setLength(ConfigOverrides.MAX_BYTES);
        ConfigOverrides o = ConfigOverrides.parse(s.toString().getBytes(StandardCharsets.UTF_8));
        assertEquals(1, o.count());
        assertTrue(o.rejectedLines.isEmpty());
    }

    @Test public void entriesPastTheLimitAreRejected() {
        List<String> lines = new ArrayList<>();
        for (int i = 0; i < ConfigOverrides.MAX_ENTRIES; ++i) lines.add("k" + i + " = " + i);
        lines.add("[fable2_config]");
        lines.add("one_too_many = true");
        ConfigOverrides o = parse(lines.toArray(new String[0]));
        assertEquals(ConfigOverrides.MAX_ENTRIES, o.count());
        assertEquals(1, o.rejectedLines.size());
        assertTrue(o.rejectedLines.get(0), o.rejectedLines.get(0).startsWith("line 202: "));
        assertTrue(o.gameConfigOverrides.isEmpty());
    }

    @Test public void invalidUtf8AppliesNothing() {
        ConfigOverrides o = ConfigOverrides.parse(new byte[] { 'a', ' ', '=', ' ', '"', (byte) 0xC3, '"' });
        assertEquals(0, o.count());
        assertEquals(1, o.rejectedLines.size());
        assertTrue(o.rejectedLines.get(0), o.rejectedLines.get(0).contains("UTF-8"));
    }

    @Test public void rejectsEverySyntaxOutsideTheAllowedSubset() {
        assertRejected("just_a_key", "key = value");
        assertRejected("= 1", "invalid key");
        assertRejected("Upper_Case = 1", "invalid key");
        assertRejected("dotted.key = 1", "invalid key");
        assertRejected("\"quoted_key\" = 1", "invalid key");
        assertRejected("bad-key = 1", "invalid key");
        assertRejected("empty =", "invalid value");
        assertRejected("bare = word", "invalid value");
        assertRejected("caps = True", "invalid value");
        assertRejected("single = 'quoted'", "invalid value");
        assertRejected("escaped = \"a\\\"b\"", "invalid value");
        assertRejected("backslash = \"C:\\path\"", "invalid value");
        assertRejected("multi = \"\"\"text\"\"\"", "invalid value");
        assertRejected("unterminated = \"text", "invalid value");
        assertRejected("trailing = \"text\" junk", "invalid value");
        assertRejected("array = [1, 2]", "invalid value");
        assertRejected("table = { a = 1 }", "invalid value");
        assertRejected("leading_zero = 007", "invalid value");
        assertRejected("hex = 0x10", "invalid value");
        assertRejected("date = 2026-11-23", "invalid value");
        assertRejected("two = 1 2", "invalid value");
        assertRejected("tab_in_string = \"a\tb\"", "invalid value");
    }

    @Test public void rejectionsDoNotEchoLongValues() {
        String value = "x".repeat(5000);
        ConfigOverrides o = parse("long = " + value);
        assertEquals(1, o.rejectedLines.size());
        assertTrue(o.rejectedLines.get(0).length() < 200);
    }

    @Test public void emptyInputHasNoOverrides() {
        ConfigOverrides o = ConfigOverrides.parse(new byte[0]);
        assertEquals(0, o.count());
        assertTrue(o.rejectedLines.isEmpty());
        assertEquals(0, ConfigOverrides.empty().count());
    }

    // fable_2.toml

    private static final List<String> ENGINE_FILE = Arrays.asList(
        "# Auto-generated cvar configuration",
        "frame_wait_sleep_us = 100",
        "user_cvar = 7",
        "capture_swap_timing_frames = 900",
        "[some_table]",
        "inner = 1");

    private static Map<String, String> managed() {
        return map("frame_wait_sleep_us", "100", "vulkan_android_custom_driver", "\"turnip27\"");
    }

    @Test public void noOverridesAndNoRecordMatchesTheManagedMerge() {
        ConfigOverrides.Applied a = Settings.composeEngineConfig(ENGINE_FILE, managed(),
            ConfigOverrides.empty(), Collections.emptyMap());
        assertEquals(Settings.mergeManagedKeys(ENGINE_FILE, managed()), a.lines);
        assertTrue(a.restore.isEmpty());
    }

    @Test public void engineOverridesWinOverManagedAndRemovedKeys() {
        ConfigOverrides o = parse("frame_wait_sleep_us = 250", "capture_swap_timing_frames = 600",
            "user_cvar = 8", "new_cvar = true");
        ConfigOverrides.Applied a = Settings.composeEngineConfig(ENGINE_FILE, managed(), o, Collections.emptyMap());
        assertEquals(Arrays.asList(
            "# Auto-generated cvar configuration",
            "frame_wait_sleep_us = 250",
            "user_cvar = 8",
            "vulkan_android_custom_driver = \"turnip27\"",
            "capture_swap_timing_frames = 600",
            "new_cvar = true",
            "[some_table]",
            "inner = 1"), a.lines);
        Map<String, String> record = new LinkedHashMap<>();
        record.put("frame_wait_sleep_us", "frame_wait_sleep_us = 100");
        record.put("capture_swap_timing_frames", null);
        record.put("user_cvar", "user_cvar = 7");
        record.put("new_cvar", null);
        assertEquals(record, a.restore);
    }

    @Test public void clearingEngineOverridesRestoresTheOriginalLines() {
        ConfigOverrides o = parse("frame_wait_sleep_us = 250", "user_cvar = 8", "new_cvar = true");
        ConfigOverrides.Applied first = Settings.composeEngineConfig(ENGINE_FILE, managed(), o, Collections.emptyMap());
        ConfigOverrides.Applied cleared = Settings.composeEngineConfig(first.lines, managed(),
            ConfigOverrides.empty(), first.restore);
        assertEquals(Settings.mergeManagedKeys(ENGINE_FILE, managed()), cleared.lines);
        assertTrue(cleared.restore.isEmpty());
    }

    @Test public void reapplyingTheSameEngineOverridesIsStable() {
        ConfigOverrides o = parse("user_cvar = 8", "new_cvar = true");
        ConfigOverrides.Applied first = Settings.composeEngineConfig(ENGINE_FILE, managed(), o, Collections.emptyMap());
        ConfigOverrides.Applied second = Settings.composeEngineConfig(first.lines, managed(), o, first.restore);
        assertEquals(first.lines, second.lines);
        assertEquals(first.restore, second.restore);
    }

    @Test public void restoreSurvivesTheSdkRewritingTheFile() {
        // The in-game settings menu rewrites fable_2.toml without comments and in its own order.
        ConfigOverrides o = parse("user_cvar = 8", "new_cvar = true");
        ConfigOverrides.Applied first = Settings.composeEngineConfig(ENGINE_FILE, managed(), o, Collections.emptyMap());
        List<String> rewritten = Arrays.asList("# Auto-generated cvar configuration", "new_cvar = true",
            "frame_wait_sleep_us = 100", "vulkan_android_custom_driver = \"turnip27\"", "user_cvar = 8");
        ConfigOverrides.Applied cleared = Settings.composeEngineConfig(rewritten, managed(),
            ConfigOverrides.empty(), first.restore);
        assertEquals(Arrays.asList("# Auto-generated cvar configuration", "frame_wait_sleep_us = 100",
            "vulkan_android_custom_driver = \"turnip27\"", "user_cvar = 7"), cleared.lines);
    }

    @Test public void engineOverridesNeverTouchTableKeys() {
        ConfigOverrides o = parse("inner = 5");
        ConfigOverrides.Applied a = Settings.composeEngineConfig(ENGINE_FILE, managed(), o, Collections.emptyMap());
        assertTrue(a.lines.contains("inner = 1"));
        assertTrue(a.lines.indexOf("inner = 5") < a.lines.indexOf("[some_table]"));
    }

    // fable2_config.toml

    private static final List<String> GAME_FILE = Arrays.asList(
        "# fable2_config.toml",
        "[patches]",
        "# Keeps base skins.",
        "disable_texture_morphing = true",
        "high_tick_rate = true   # about 2% cost",
        "[perf]",
        "hotfunc_yield_every = 1",
        "[remote]",
        "enabled = true",
        "[other]",
        "enabled = false");

    @Test public void gameOverridesUpdateMatchingKeysInAnySection() {
        ConfigOverrides o = parse("[fable2_config]", "high_tick_rate = false", "hotfunc_yield_every = 0");
        ConfigOverrides.Applied a = ConfigOverrides.applyGameConfig(GAME_FILE, o.gameConfigOverrides,
            Collections.emptyMap());
        List<String> expected = new ArrayList<>(GAME_FILE);
        expected.set(4, "high_tick_rate = false");
        expected.set(6, "hotfunc_yield_every = 0");
        assertEquals(expected, a.lines);
        assertTrue(a.notApplied.isEmpty());
        assertEquals(map("high_tick_rate", "high_tick_rate = true   # about 2% cost",
            "hotfunc_yield_every", "hotfunc_yield_every = 1"), a.restore);
    }

    @Test public void missingAndAmbiguousGameKeysAreNotApplied() {
        ConfigOverrides o = parse("[fable2_config]", "no_such_key = true", "enabled = false");
        ConfigOverrides.Applied a = ConfigOverrides.applyGameConfig(GAME_FILE, o.gameConfigOverrides,
            Collections.emptyMap());
        assertEquals(GAME_FILE, a.lines);
        assertEquals(Arrays.asList("no_such_key (not in fable2_config.toml)",
            "enabled (in more than one section)"), a.notApplied);
        assertTrue(a.restore.isEmpty());
    }

    @Test public void clearingGameOverridesRestoresTheOriginalLines() {
        ConfigOverrides o = parse("[fable2_config]", "high_tick_rate = false");
        ConfigOverrides.Applied first = ConfigOverrides.applyGameConfig(GAME_FILE, o.gameConfigOverrides,
            Collections.emptyMap());
        ConfigOverrides.Applied cleared = ConfigOverrides.applyGameConfig(first.lines, Collections.emptyMap(),
            first.restore);
        assertEquals(GAME_FILE, cleared.lines);
        assertTrue(cleared.restore.isEmpty());
        ConfigOverrides.Applied again = ConfigOverrides.applyGameConfig(first.lines, o.gameConfigOverrides,
            first.restore);
        assertEquals(first.lines, again.lines);
        assertEquals(first.restore, again.restore);
    }

    @Test public void restoreRecordRoundTrips() {
        Map<String, String> record = new LinkedHashMap<>();
        record.put("a", "a = \"x\ty = z\"  # comment");
        record.put("b", null);
        record.put("c", "");
        assertEquals(record, ConfigOverrides.decodeRecord(ConfigOverrides.encodeRecord(record)));
        assertTrue(ConfigOverrides.decodeRecord("").isEmpty());
        assertTrue(ConfigOverrides.decodeRecord(null).isEmpty());
        assertEquals("", ConfigOverrides.encodeRecord(Collections.emptyMap()));
    }

    // Launcher status text

    @Test public void summaryCountsActiveRejectedAndUnappliedOverrides() {
        assertNull(ConfigOverrides.summary(null, Collections.emptyList()));
        assertEquals("1 override active.", ConfigOverrides.summary(parse("a = 1"), Collections.emptyList()));
        String s = ConfigOverrides.summary(parse("a = 1", "b = 2", "bad", "[fable2_config]", "c = 3"),
            Collections.singletonList("c (not in fable2_config.toml)"));
        assertTrue(s, s.startsWith("3 overrides active. 1 line rejected. 1 not applied."));
        assertTrue(s, s.contains("line 3: "));
        assertTrue(s, s.contains("c (not in fable2_config.toml)"));
    }
}
