package org.fable2.recomp;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.view.KeyEvent;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/**
 * Launcher settings. They are kept in SharedPreferences and written into the
 * game's own config (fable_2.toml) before each launch, so the native side
 * needs no Android-specific settings code.
 */
final class Settings {
    static final String PREFS = "fable2";
    // Bundled Turnip driver: assets/gpu_drivers/<BUNDLED_DRIVER>, installed to
    // <internal storage>/gpu_drivers/<BUNDLED_DRIVER>, where the runtime loads it.
    static final String BUNDLED_DRIVER = "turnip27";

    // Development-only keys removed from the config if a test build left them.
    private static final String[] REMOVED = { "capture_swap_timing_frames", "vulkan_android_driver_env" };

    static final int TOUCH_AUTO = 0, TOUCH_ALWAYS = 1, TOUCH_OFF = 2;

    private final SharedPreferences prefs;
    Settings(Context context) { prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE); }

    int resolutionScale() { return prefs.getInt("resolution_scale", 1); }
    boolean msaa() { return prefs.getBoolean("msaa", false); }
    boolean tripleBuffering() { return prefs.getBoolean("triple_buffering", false); }
    boolean skipIntros() { return prefs.getBoolean("skip_intros", false); }
    boolean lowLatency() { return prefs.getBoolean("low_latency", true); }
    boolean fxaa() { return prefs.getBoolean("fxaa", true); }
    boolean fineFoliage() { return prefs.getBoolean("fine_foliage", true); }
    String driver() {
        return DriverPolicy.select(prefs.getString("gpu_driver", null),
            prefs.contains("bundled_driver") ? prefs.getBoolean("bundled_driver", true) : null,
            GpuDetector.renderer());
    }
    void setDriver(String id) { prefs.edit().putString("gpu_driver", id).apply(); }
    boolean logFps() { return prefs.getBoolean("log_fps", false); }
    GpuProfile.Mode mode() { return GpuProfile.Mode.parse(prefs.getString("graphics_profile", "AUTO")); }
    void setMode(GpuProfile.Mode mode) { prefs.edit().putString("graphics_profile", mode.name()).apply(); }
    /** The profile id the next launch writes, for the detected GPU and selected driver. */
    String profileId() { return GpuProfile.resolve(GpuDetector.renderer(), driver(), mode()); }
    int touchMode() { return prefs.getInt("touch_mode", TOUCH_AUTO); }
    float touchOpacity() { return prefs.getFloat("touch_opacity", 0.6f); }
    float touchScale() { return prefs.getFloat("touch_scale", 1f); }
    boolean vibration() { return prefs.getBoolean("touch_vibration", true); }

    void put(String key, int value) { prefs.edit().putInt(key, value).apply(); }
    void put(String key, boolean value) { prefs.edit().putBoolean(key, value).apply(); }
    void put(String key, float value) { prefs.edit().putFloat(key, value).apply(); }

    // Controller remapping: physical Android keycode -> Android keycode SDL
    // receives instead. Stored as "from:to,from:to".
    Map<Integer, Integer> buttonMap() {
        Map<Integer, Integer> map = new HashMap<>();
        for (String pair : prefs.getString("button_map", "").split(",")) {
            String[] p = pair.split(":");
            if (p.length != 2) continue;
            try { map.put(Integer.parseInt(p[0]), Integer.parseInt(p[1])); } catch (NumberFormatException ignored) {}
        }
        return map;
    }
    void setButtonMap(Map<Integer, Integer> map) {
        StringBuilder s = new StringBuilder();
        for (Map.Entry<Integer, Integer> e : map.entrySet()) {
            if (e.getKey().equals(e.getValue())) continue;
            if (s.length() > 0) s.append(',');
            s.append(e.getKey()).append(':').append(e.getValue());
        }
        prefs.edit().putString("button_map", s.toString()).apply();
    }

    // Touch layout: control id -> {x, y, size, hidden} with x/y as screen fractions.
    String touchLayout() { return prefs.getString("touch_layout", ""); }
    void setTouchLayout(String layout) { prefs.edit().putString("touch_layout", layout).apply(); }

    // Lines that fable_2.overrides.toml replaced, so they can be put back. Kept here rather
    // than in the config files because the in-game menu can rewrite fable_2.toml.
    private static final String ENGINE_RESTORE = "overrides_restore_engine", GAME_RESTORE = "overrides_restore_game";

    /**
     * Writes the managed keys into fable_2.toml, keeping every other line, then applies
     * fable_2.overrides.toml to fable_2.toml and fable2_config.toml.
     */
    void writeGameConfig(File storage) throws IOException {
        String selectedDriver = driver();
        String profile = GpuProfile.resolve(GpuDetector.renderer(), selectedDriver, mode());
        LinkedHashMap<String, String> managed = GpuProfile.managed(profile, new GpuProfile.Choices(
            resolutionScale(), msaa(), tripleBuffering(), skipIntros(), lowLatency(), fxaa(), fineFoliage(),
            selectedDriver, logFps()));
        ConfigOverrides overrides = ConfigOverrides.read(new File(storage, ConfigOverrides.FILE_NAME));
        if (overrides == null) overrides = ConfigOverrides.empty();

        File config = new File(storage, "fable_2.toml");
        ConfigOverrides.Applied engine = composeEngineConfig(readLines(config), managed, overrides,
            ConfigOverrides.decodeRecord(prefs.getString(ENGINE_RESTORE, "")));
        writeLines(config, engine.lines);
        saveRecord(ENGINE_RESTORE, engine.restore);

        // The game recreates fable2_config.toml with defaults when it is missing, so only an existing file is edited.
        File gameConfig = new File(storage, "fable2_config.toml");
        if (!gameConfig.isFile()) { saveRecord(GAME_RESTORE, Collections.emptyMap()); return; }
        List<String> gameLines = readLines(gameConfig);
        ConfigOverrides.Applied game = ConfigOverrides.applyGameConfig(gameLines, overrides.gameConfigOverrides,
            ConfigOverrides.decodeRecord(prefs.getString(GAME_RESTORE, "")));
        if (!game.lines.equals(gameLines)) writeLines(gameConfig, game.lines);
        saveRecord(GAME_RESTORE, game.restore);
    }

    /** Status text for the overrides file, or null when there is none. */
    String overridesSummary(File storage) {
        ConfigOverrides overrides;
        try { overrides = ConfigOverrides.read(new File(storage, ConfigOverrides.FILE_NAME)); }
        catch (IOException e) { return "Cannot read " + ConfigOverrides.FILE_NAME + ": " + e.getMessage(); }
        if (overrides == null) return null;
        List<String> notApplied = new ArrayList<>();
        File gameConfig = new File(storage, "fable2_config.toml");
        if (!overrides.gameConfigOverrides.isEmpty()) {
            try {
                notApplied = gameConfig.isFile() ? ConfigOverrides.applyGameConfig(readLines(gameConfig),
                    overrides.gameConfigOverrides, ConfigOverrides.decodeRecord(prefs.getString(GAME_RESTORE, ""))).notApplied
                    : Collections.singletonList("[fable2_config] keys (fable2_config.toml does not exist yet; start the game once)");
            } catch (IOException e) { notApplied = Collections.singletonList("fable2_config.toml: " + e.getMessage()); }
        }
        return ConfigOverrides.summary(overrides, notApplied);
    }

    /** Deletes fable_2.overrides.toml. The replaced values come back on the next writeGameConfig. */
    static boolean clearOverrides(File storage) {
        File file = new File(storage, ConfigOverrides.FILE_NAME);
        return !file.exists() || file.delete();
    }

    /**
     * Builds fable_2.toml: puts back lines an earlier override replaced, merges the managed
     * keys (which also strips REMOVED keys), then applies overrides so they win over both.
     */
    static ConfigOverrides.Applied composeEngineConfig(List<String> lines, Map<String, String> managed,
            ConfigOverrides overrides, Map<String, String> record) {
        List<String> merged = mergeManagedKeys(ConfigOverrides.restore(lines, record, true), managed);
        return ConfigOverrides.applyEngine(merged, overrides.engineOverrides);
    }

    private void saveRecord(String key, Map<String, String> record) {
        String encoded = ConfigOverrides.encodeRecord(record);
        if (!encoded.equals(prefs.getString(key, ""))) prefs.edit().putString(key, encoded).commit();
    }

    private static List<String> readLines(File file) throws IOException {
        List<String> lines = new ArrayList<>();
        if (!file.isFile()) return lines;
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8))) {
            for (String line; (line = r.readLine()) != null; ) lines.add(line);
        }
        return lines;
    }

    private static void writeLines(File file, List<String> lines) throws IOException {
        File partial = new File(file.getParentFile(), file.getName() + ".writing");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(partial), StandardCharsets.UTF_8)) {
            for (String line : lines) w.write(line + "\n");
        }
        if (!partial.renameTo(file)) { partial.delete(); throw new IOException("Cannot write " + file.getName()); }
    }

    /** Applies managed keys to existing config lines, keeping every other line. */
    static List<String> mergeManagedKeys(List<String> lines, Map<String, String> managed) {
        Set<String> removed = new HashSet<>(Arrays.asList(REMOVED));
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
        // New keys go before the first table so they stay top-level.
        int insert = 0;
        while (insert < out.size() && !out.get(insert).trim().startsWith("[")) ++insert;
        List<String> added = new ArrayList<>();
        for (Map.Entry<String, String> e : managed.entrySet())
            if (!written.contains(e.getKey())) added.add(e.getKey() + " = " + e.getValue());
        out.addAll(insert, added);
        return out;
    }

    /** Copies the bundled GPU driver to internal storage when missing or outdated. */
    static void installBundledDriver(Context context) throws IOException {
        AssetManager assets = context.getAssets();
        String source = "gpu_drivers/" + BUNDLED_DRIVER;
        String[] files = assets.list(source);
        if (files == null || files.length == 0) return;
        File dir = new File(context.getFilesDir(), source);
        File stamp = new File(dir, ".installed-version");
        String version = Long.toString(BuildConfig.VERSION_CODE) + "-" + BuildConfig.VERSION_NAME;
        if (stamp.isFile() && version.equals(readText(stamp))) return;
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("Cannot create " + dir);
        byte[] buffer = new byte[1 << 16];
        for (String name : files) {
            File partial = new File(dir, name + ".installing");
            try (InputStream in = assets.open(source + "/" + name); OutputStream out = new FileOutputStream(partial)) {
                for (int n; (n = in.read(buffer)) > 0; ) out.write(buffer, 0, n);
            }
            if (!partial.renameTo(new File(dir, name))) throw new IOException("Cannot install " + name);
        }
        try (Writer w = new OutputStreamWriter(new FileOutputStream(stamp), StandardCharsets.UTF_8)) { w.write(version); }
    }

    static String readText(File f) {
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
            String line = r.readLine();
            return line == null ? "" : line.trim();
        } catch (IOException e) { return ""; }
    }

    // Xbox 360 buttons the game understands, with the Android keycode SDL maps to each.
    static final String[] BUTTON_NAMES = { "A", "B", "X", "Y", "LB", "RB", "Back", "Start", "L3", "R3",
        "D-pad Up", "D-pad Down", "D-pad Left", "D-pad Right" };
    static final int[] BUTTON_CODES = { KeyEvent.KEYCODE_BUTTON_A, KeyEvent.KEYCODE_BUTTON_B,
        KeyEvent.KEYCODE_BUTTON_X, KeyEvent.KEYCODE_BUTTON_Y, KeyEvent.KEYCODE_BUTTON_L1,
        KeyEvent.KEYCODE_BUTTON_R1, KeyEvent.KEYCODE_BUTTON_SELECT, KeyEvent.KEYCODE_BUTTON_START,
        KeyEvent.KEYCODE_BUTTON_THUMBL, KeyEvent.KEYCODE_BUTTON_THUMBR, KeyEvent.KEYCODE_DPAD_UP,
        KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT };

    static String buttonName(int keycode) {
        for (int i = 0; i < BUTTON_CODES.length; ++i) if (BUTTON_CODES[i] == keycode) return BUTTON_NAMES[i];
        String s = KeyEvent.keyCodeToString(keycode);
        return s.startsWith("KEYCODE_") ? s.substring(8) : s;
    }
}
