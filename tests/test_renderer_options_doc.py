"""Keeps docs/RENDERER_OPTIONS.md in step with the code that defines each option."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/RENDERER_OPTIONS.md"
SETTINGS = ROOT / "android/app/src/main/java/org/fable2/recomp/Settings.java"
PATCH = ROOT / "thirdparty/rexglue-sdk-android.patch"
CONFIG_HEADER = ROOT / "src/core/fable2_config.h"
APP_HEADER = ROOT / "src/core/fable_2_app.h"

FILES = {"fable_2.toml", "fable2_config.toml"}
RISK_CLASSES = {
    "cpu-wait",
    "shader-codegen",
    "memory-upload",
    "render-target",
    "presentation",
    "game-patch",
    "diagnostic",
}
RESTART_VALUES = {"yes", "no", "n/a"}
COLUMNS = ("name", "file", "default", "android", "reference", "risk", "restart", "effect")


def read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def settings_keys(text):
    """Return {key: tuned value or None} for every key Settings.java writes or removes."""
    block = re.search(r"TUNED = \{(.*?)\n\s*\};", text, re.S)
    tuned = dict(re.findall(r'\{"(\w+)",\s*"([^"]*)"\}', block.group(1))) if block else {}
    keys = dict(tuned)
    for key in re.findall(r'managed\.put\("(\w+)"', text):
        keys.setdefault(key, None)
    removed = re.search(r"REMOVED = \{(.*?)\}", text, re.S)
    for key in re.findall(r'"(\w+)"', removed.group(1)) if removed else []:
        keys.setdefault(key, None)
    return keys


def patch_cvars(text):
    """Return {name: [defaults]} for each cvar defined on an added patch line."""
    added = "\n".join(line[1:] for line in text.split("\n") if line.startswith("+") and not line.startswith("+++"))
    found = {}
    for match in re.finditer(r'^\s*REXCVAR_DEFINE_\w+\(\s*(\w+)\s*,\s*("(?:[^"\\]|\\.)*"|[^,]+),', added, re.M):
        found.setdefault(match.group(1), []).append(match.group(2).strip())
    return found


def config_keys(text):
    """Return the Values members declared in the [patches] and [perf] sections."""
    start = text.index("// [patches] -")
    end = text.index("};", start)
    return set(re.findall(r"^\s*(?:bool|int32_t|float|std::string)\s+(\w+)\s*(?:=|\{)", text[start:end], re.M))


def seeded_cvars(text):
    names = set(re.findall(r'SetFlagByName\("(\w+)"', text))
    names |= {name for name, _ in re.findall(r'std::pair\{"(\w+)",\s*"([^"]*)"\}', text)}
    return names


def doc_rows(text):
    """Return {name: row} for table rows whose first cell is a backticked option."""
    rows = {}
    duplicates = []
    for line in text.split("\n"):
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) != len(COLUMNS):
            raise ValueError(f"row has {len(cells)} cells, expected {len(COLUMNS)}: {line[:70]}")
        row = dict(zip(COLUMNS, cells))
        name = row["name"].strip("`")
        if name in rows:
            duplicates.append(name)
        rows[name] = row
    if duplicates:
        raise ValueError(f"duplicate rows: {sorted(duplicates)}")
    return rows


def row_problems(row):
    problems = []
    if row["file"].strip("`") not in FILES:
        problems.append("file")
    if row["risk"].strip("`") not in RISK_CLASSES:
        problems.append("risk")
    if row["restart"] not in RESTART_VALUES:
        problems.append("restart")
    for column in COLUMNS:
        if not row[column]:
            problems.append(f"empty {column}")
    if row["reference"].lower().startswith("unknown") and not re.match(r"(?i)unknown[:(] *\S", row["reference"]):
        problems.append("unknown reference has no reason")
    return problems


def cell_value(cell):
    """Return the leading backticked value, without TOML or C++ string quotes."""
    match = re.match(r"`([^`]*)`", cell)
    return match.group(1).strip('"') if match else None


class ParserTests(unittest.TestCase):
    """Each parser must catch the kind of new option a later change could add."""

    def test_settings_keys_cover_tuned_computed_and_removed(self):
        text = """
        private static final String[][] TUNED = {
            {"tuned_key", "7"},
        };
        private static final String[] REMOVED = { "removed_key" };
        managed.put("computed_key", value);
        """
        self.assertEqual(settings_keys(text), {"tuned_key": "7", "computed_key": None, "removed_key": None})

    def test_patch_cvars_read_only_added_definitions(self):
        text = "\n".join(
            [
                "+++ b/file.cpp",
                '+REXCVAR_DEFINE_BOOL(added_one, true, "GPU",',
                '+                    "description");',
                '+  REXCVAR_DEFINE_STRING(added_two, "a,b", "GPU", "description");',
                ' REXCVAR_DEFINE_BOOL(context_only, true, "GPU", "description");',
                '-REXCVAR_DEFINE_BOOL(removed_one, true, "GPU", "description");',
                '+// REXCVAR_DEFINE_BOOL(commented, true, "GPU", "description");',
            ]
        )
        self.assertEqual(patch_cvars(text), {"added_one": ["true"], "added_two": ['"a,b"']})

    def test_config_keys_stop_at_section_boundaries(self):
        text = """
        struct Values {
          bool input_key = true;
          // [patches] - toggles
          bool patch_key = false;
        #if defined(__ANDROID__)
          bool platform_key = true;
        #else
          bool platform_key = false;
        #endif
          // [perf] - tuning
          int32_t perf_key = 1;
        };
        bool after_struct = true;
        """
        self.assertEqual(config_keys(text), {"patch_key", "platform_key", "perf_key"})

    def test_seeded_cvars_cover_direct_and_loop_seeds(self):
        text = """
        rex::cvar::SetFlagByName("direct_seed", "1");
        for (auto [name, value] : {std::pair{"loop_seed", "true"}}) {}
        """
        self.assertEqual(seeded_cvars(text), {"direct_seed", "loop_seed"})

    def test_doc_rows_reject_a_short_row(self):
        with self.assertRaisesRegex(ValueError, "expected 8"):
            doc_rows("| `key` | fable_2.toml | `1` |")

    def test_doc_rows_reject_duplicates(self):
        row = "| `key` | `fable_2.toml` | `1` | `1` | `1` | `diagnostic` | no | Not measured. |"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            doc_rows(f"{row}\n{row}")

    def test_row_problems_reject_invalid_columns_and_bare_unknown(self):
        good = dict(zip(COLUMNS, ("`k`", "`fable_2.toml`", "`1`", "`1`", "`1`", "`diagnostic`", "no", "Not measured.")))
        self.assertEqual(row_problems(good), [])
        self.assertEqual(row_problems({**good, "file": "`other.toml`"}), ["file"])
        self.assertEqual(row_problems({**good, "risk": "`graphics`"}), ["risk"])
        self.assertEqual(row_problems({**good, "restart": "maybe"}), ["restart"])
        self.assertEqual(row_problems({**good, "reference": "unknown"}), ["unknown reference has no reason"])
        self.assertEqual(row_problems({**good, "reference": "unknown: no old path exists"}), [])


class RendererOptionsDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = doc_rows(read(DOC))
        cls.settings = settings_keys(read(SETTINGS))
        cls.patch = patch_cvars(read(PATCH))

    def assert_documented(self, keys):
        self.assertTrue(keys)
        self.assertEqual(sorted(set(keys) - set(self.rows)), [])

    def test_every_settings_key_is_documented(self):
        self.assert_documented(self.settings)

    def test_every_patch_cvar_is_documented(self):
        self.assert_documented(self.patch)

    def test_every_seeded_cvar_is_documented(self):
        self.assert_documented(seeded_cvars(read(APP_HEADER)))

    def test_every_patches_and_perf_key_is_documented(self):
        self.assert_documented(config_keys(read(CONFIG_HEADER)))

    def test_every_row_is_complete(self):
        problems = {name: row_problems(row) for name, row in self.rows.items() if row_problems(row)}
        self.assertEqual(problems, {})

    def test_config_keys_are_owned_by_the_right_file(self):
        keys = config_keys(read(CONFIG_HEADER))
        wrong = [k for k in keys if self.rows[k]["file"].strip("`") != "fable2_config.toml"]
        wrong += [k for k in self.settings if self.rows[k]["file"].strip("`") != "fable_2.toml"]
        self.assertEqual(wrong, [])

    def test_tuned_values_match_settings(self):
        wrong = {
            key: (value, self.rows[key]["android"])
            for key, value in self.settings.items()
            if value is not None and cell_value(self.rows[key]["android"]) != value
        }
        self.assertEqual(wrong, {})

    def test_patch_defaults_match_the_patch(self):
        wrong = {}
        for key, defaults in self.patch.items():
            documented = cell_value(self.rows[key]["default"])
            if documented not in {value.strip('"') for value in defaults}:
                wrong[key] = (defaults, self.rows[key]["default"])
        self.assertEqual(wrong, {})


if __name__ == "__main__":
    unittest.main()
