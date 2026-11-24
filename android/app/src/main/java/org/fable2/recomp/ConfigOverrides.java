package org.fable2.recomp;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Pattern;

/**
 * Tester overrides from fable_2.overrides.toml, applied last when the launcher writes the
 * game's config files. Top-level keys pin SDK cvars in fable_2.toml; keys under
 * [fable2_config] replace matching lines in fable2_config.toml. Only a strict TOML subset
 * is accepted, and every line outside it is rejected with a line-numbered reason.
 *
 * Each apply returns a restore record (key -> the line it replaced, or null for a line it
 * added). The launcher stores it outside the config files, because the SDK can rewrite
 * fable_2.toml from the in-game menu, and passes it back on the next write so values come
 * back once an override is removed. No Android dependencies, so it is covered by JVM tests.
 */
final class ConfigOverrides {
    static final String FILE_NAME = "fable_2.overrides.toml";
    static final String GAME_SECTION = "fable2_config";
    static final int MAX_BYTES = 64 * 1024, MAX_ENTRIES = 200;

    private static final Pattern KEY = Pattern.compile("[a-z0-9_]+");
    private static final Pattern NUMBER = Pattern.compile("[+-]?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?");
    // Printable characters except backslash and the closing quote.
    private static final Pattern STRING = Pattern.compile("\"[^\"\\\\\\x00-\\x1f\\x7f]*\"");
    private static final Pattern GAME_HEADER = Pattern.compile("\\[" + GAME_SECTION + "\\]\\s*(#.*)?");

    final LinkedHashMap<String, String> engineOverrides = new LinkedHashMap<>();
    final LinkedHashMap<String, String> gameConfigOverrides = new LinkedHashMap<>();
    final List<String> rejectedLines = new ArrayList<>();

    private ConfigOverrides() {}

    static ConfigOverrides empty() { return new ConfigOverrides(); }

    int count() { return engineOverrides.size() + gameConfigOverrides.size(); }

    /** Reads the overrides file, or returns null when there is none. */
    static ConfigOverrides read(File file) throws IOException {
        if (!file.isFile()) return null;
        byte[] buffer = new byte[MAX_BYTES + 1];
        int length = 0;
        try (InputStream in = new FileInputStream(file)) {
            for (int n; length < buffer.length && (n = in.read(buffer, length, buffer.length - length)) > 0; ) length += n;
        }
        return parse(Arrays.copyOf(buffer, length));
    }

    static ConfigOverrides parse(byte[] data) {
        ConfigOverrides o = new ConfigOverrides();
        if (data.length > MAX_BYTES) {
            o.rejectedLines.add("file is larger than 64 KB; no overrides applied");
            return o;
        }
        String text;
        try {
            text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(data)).toString();
        } catch (CharacterCodingException e) {
            o.rejectedLines.add("file is not valid UTF-8; no overrides applied");
            return o;
        }
        if (text.startsWith("﻿")) text = text.substring(1);

        Map<String, Integer> engineLines = new HashMap<>(), gameLines = new HashMap<>();
        String unknownSection = null;
        boolean inGame = false, gameHeaderSeen = false;
        String[] lines = text.split("\n", -1);
        for (int i = 0; i < lines.length; ++i) {
            String where = "line " + (i + 1) + ": ";
            String line = lines[i].endsWith("\r") ? lines[i].substring(0, lines[i].length() - 1) : lines[i];
            String trimmed = line.trim();
            if (trimmed.isEmpty() || trimmed.startsWith("#")) continue;
            if (trimmed.startsWith("[")) {
                if (GAME_HEADER.matcher(trimmed).matches()) {
                    if (gameHeaderSeen) o.rejectedLines.add(where + "[" + GAME_SECTION + "] appears more than once");
                    inGame = gameHeaderSeen = true;
                    unknownSection = null;
                } else {
                    unknownSection = shorten(trimmed.split("#", 2)[0].trim());
                    o.rejectedLines.add(where + "unknown section " + unknownSection
                        + "; only [" + GAME_SECTION + "] is allowed");
                }
                continue;
            }
            int eq = trimmed.indexOf('=');
            if (eq < 0) { o.rejectedLines.add(where + "expected key = value"); continue; }
            String key = trimmed.substring(0, eq).trim();
            if (!KEY.matcher(key).matches()) {
                o.rejectedLines.add(where + "invalid key (use lowercase letters, digits and _)");
                continue;
            }
            if (unknownSection != null) {
                o.rejectedLines.add(where + shorten(key) + " is in unknown section " + unknownSection);
                continue;
            }
            String value = scalar(trimmed.substring(eq + 1));
            if (value == null) {
                o.rejectedLines.add(where + "invalid value for " + shorten(key)
                    + " (use a number, true, false or a double-quoted string without backslashes)");
                continue;
            }
            Map<String, Integer> seen = inGame ? gameLines : engineLines;
            Integer first = seen.get(key);
            if (first != null) {
                o.rejectedLines.add(where + "duplicate key " + shorten(key) + " (first set on line " + first + ")");
                continue;
            }
            if (o.count() >= MAX_ENTRIES) {
                o.rejectedLines.add(where + "more than " + MAX_ENTRIES + " overrides; ignored");
                continue;
            }
            seen.put(key, i + 1);
            (inGame ? o.gameConfigOverrides : o.engineOverrides).put(key, value);
        }
        return o;
    }

    /** Returns the scalar at the start of text (an optional comment may follow), or null. */
    private static String scalar(String text) {
        String t = text.trim();
        String value, rest;
        if (t.startsWith("\"")) {
            int close = t.indexOf('"', 1);
            if (close < 0) return null;
            value = t.substring(0, close + 1);
            rest = t.substring(close + 1).trim();
            if (!rest.isEmpty() && !rest.startsWith("#")) return null;
            return STRING.matcher(value).matches() ? value : null;
        }
        int hash = t.indexOf('#');
        value = (hash < 0 ? t : t.substring(0, hash)).trim();
        if (value.equals("true") || value.equals("false") || NUMBER.matcher(value).matches()) return value;
        return null;
    }

    private static String shorten(String s) { return s.length() <= 40 ? s : s.substring(0, 40) + "…"; }

    /** Lines after applying overrides to one config file. */
    static final class Applied {
        final List<String> lines;
        /** Key -> the line an override replaced, or null for a line it added. */
        final LinkedHashMap<String, String> restore;
        final List<String> notApplied;

        Applied(List<String> lines, LinkedHashMap<String, String> restore, List<String> notApplied) {
            this.lines = lines;
            this.restore = restore;
            this.notApplied = notApplied;
        }
    }

    /** Index of each line's key and section, for one file. */
    private static final class Lines {
        final List<String> lines;
        final List<String> keys = new ArrayList<>();
        final List<String> sections = new ArrayList<>();

        Lines(List<String> lines) {
            this.lines = new ArrayList<>(lines);
            String section = null;
            for (String line : this.lines) {
                String trimmed = line.trim();
                if (trimmed.startsWith("[")) section = trimmed;
                int eq = trimmed.indexOf('=');
                keys.add(eq > 0 && !trimmed.startsWith("#") && !trimmed.startsWith("[")
                    ? trimmed.substring(0, eq).trim() : null);
                sections.add(section);
            }
        }

        /** Line indexes holding the key; topLevel limits the search to lines before any table. */
        List<Integer> find(String key, boolean topLevel) {
            List<Integer> found = new ArrayList<>();
            for (int i = 0; i < lines.size(); ++i)
                if (key.equals(keys.get(i)) && (!topLevel || sections.get(i) == null)) found.add(i);
            return found;
        }

        int firstTable() {
            int i = 0;
            while (i < lines.size() && sections.get(i) == null) ++i;
            return i;
        }
    }

    /**
     * Puts back the lines named in a restore record. A null original removes the line;
     * for fable_2.toml an original whose line has gone is added back as a top-level key.
     */
    static List<String> restore(List<String> lines, Map<String, String> record, boolean topLevel) {
        List<String> out = new ArrayList<>(lines);
        for (Map.Entry<String, String> e : record.entrySet()) {
            Lines index = new Lines(out);
            List<Integer> found = index.find(e.getKey(), topLevel);
            if (e.getValue() == null) {
                for (int i = found.size() - 1; i >= 0; --i) out.remove((int) found.get(i));
            } else if (!found.isEmpty()) {
                out.set(found.get(0), e.getValue());
            } else if (topLevel) {
                out.add(index.firstTable(), e.getValue());
            }
        }
        return out;
    }

    /** Applies top-level overrides to fable_2.toml lines; keys not present are added before the first table. */
    static Applied applyEngine(List<String> lines, Map<String, String> overrides) {
        List<String> out = new ArrayList<>(lines);
        LinkedHashMap<String, String> restore = new LinkedHashMap<>();
        List<String> added = new ArrayList<>();
        Lines index = new Lines(out);
        for (Map.Entry<String, String> e : overrides.entrySet()) {
            String line = e.getKey() + " = " + e.getValue();
            List<Integer> found = index.find(e.getKey(), true);
            if (found.isEmpty()) { restore.put(e.getKey(), null); added.add(line); continue; }
            restore.put(e.getKey(), out.get(found.get(0)));
            for (int i : found) out.set(i, line);
        }
        out.addAll(index.firstTable(), added);
        return new Applied(out, restore, new ArrayList<>());
    }

    /**
     * Restores the previous record, then applies [fable2_config] overrides by replacing the
     * matching key line. Keys that are missing, or present in more than one section, are
     * left alone and reported, because the right place for them is unknown.
     */
    static Applied applyGameConfig(List<String> lines, Map<String, String> overrides, Map<String, String> record) {
        List<String> out = restore(lines, record, false);
        LinkedHashMap<String, String> restore = new LinkedHashMap<>();
        List<String> notApplied = new ArrayList<>();
        Lines index = new Lines(out);
        for (Map.Entry<String, String> e : overrides.entrySet()) {
            List<Integer> found = index.find(e.getKey(), false);
            if (found.isEmpty()) { notApplied.add(e.getKey() + " (not in fable2_config.toml)"); continue; }
            if (found.size() > 1) { notApplied.add(e.getKey() + " (in more than one section)"); continue; }
            restore.put(e.getKey(), out.get(found.get(0)));
            out.set(found.get(0), e.getKey() + " = " + e.getValue());
        }
        return new Applied(out, restore, notApplied);
    }

    /** One entry per line: key, a tab, then "-" for an added line or "+" and the original line. */
    static String encodeRecord(Map<String, String> record) {
        StringBuilder s = new StringBuilder();
        for (Map.Entry<String, String> e : record.entrySet()) {
            if (s.length() > 0) s.append('\n');
            s.append(e.getKey()).append('\t').append(e.getValue() == null ? "-" : "+" + e.getValue());
        }
        return s.toString();
    }

    static LinkedHashMap<String, String> decodeRecord(String encoded) {
        LinkedHashMap<String, String> record = new LinkedHashMap<>();
        if (encoded == null || encoded.isEmpty()) return record;
        for (String entry : encoded.split("\n", -1)) {
            int tab = entry.indexOf('\t');
            if (tab <= 0 || tab + 1 >= entry.length()) continue;
            String key = entry.substring(0, tab);
            char kind = entry.charAt(tab + 1);
            if (!KEY.matcher(key).matches() || (kind != '+' && kind != '-')) continue;
            record.put(key, kind == '-' ? null : entry.substring(tab + 2));
        }
        return record;
    }

    /** Launcher status text, or null when there is no overrides file. */
    static String summary(ConfigOverrides o, List<String> notApplied) {
        if (o == null) return null;
        StringBuilder s = new StringBuilder();
        s.append(plural(o.count(), "override")).append(" active.");
        if (!o.rejectedLines.isEmpty()) s.append(' ').append(plural(o.rejectedLines.size(), "line")).append(" rejected.");
        if (!notApplied.isEmpty()) s.append(' ').append(notApplied.size()).append(" not applied.");
        List<String> details = new ArrayList<>(o.rejectedLines);
        details.addAll(notApplied);
        for (int i = 0; i < Math.min(5, details.size()); ++i) s.append("\n• ").append(details.get(i));
        if (details.size() > 5) s.append("\n• …and ").append(details.size() - 5).append(" more");
        return s.toString();
    }

    private static String plural(int n, String noun) { return n + " " + noun + (n == 1 ? "" : "s"); }
}
