"""Unit tests for tools/sdk_patch.py using temporary local git repositories."""

from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import sdk_patch  # noqa: E402


def _remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.decode("utf-8", errors="replace").strip()


class SdkPatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test_sdk_patch_"))
        self.origin_sub = self.tmp / "origin-sub"
        self.super_dir = self.tmp / "super"
        self.sub_rel = Path("thirdparty/rexglue-sdk")
        self.sub_dir = self.super_dir / self.sub_rel
        self.patch_path = self.super_dir / "thirdparty/rexglue-sdk-android.patch"

        # 1. Create a tiny upstream submodule repo with two commits.
        self.origin_sub.mkdir(parents=True)
        _git(["init", "-b", "main"], self.origin_sub)
        _git(["config", "user.name", "Test"], self.origin_sub)
        _git(["config", "user.email", "test@example.com"], self.origin_sub)
        (self.origin_sub / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
        (self.origin_sub / "hello.txt").write_text("line1\nline2\n", encoding="utf-8")
        _git(["add", "."], self.origin_sub)
        _git(["commit", "-m", "base"], self.origin_sub)
        self.pinned_commit = _git(["rev-parse", "HEAD"], self.origin_sub)

        (self.origin_sub / "hello.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
        _git(["add", "."], self.origin_sub)
        _git(["commit", "-m", "newer"], self.origin_sub)
        self.newer_commit = _git(["rev-parse", "HEAD"], self.origin_sub)

        # 2. Create a superproject with the submodule pinned at self.pinned_commit.
        self.super_dir.mkdir(parents=True)
        _git(["init", "-b", "main"], self.super_dir)
        _git(["config", "user.name", "Test"], self.super_dir)
        _git(["config", "user.email", "test@example.com"], self.super_dir)
        _git(
            [
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(self.origin_sub),
                self.sub_rel.as_posix(),
            ],
            self.super_dir,
        )
        _git(["checkout", self.pinned_commit], self.sub_dir)
        _git(["add", ".gitmodules", self.sub_rel.as_posix()], self.super_dir)
        _git(["commit", "-m", "pin submodule"], self.super_dir)

        # 3. Produce a valid initial patch (modifying hello.txt + adding newfile.txt).
        (self.sub_dir / "hello.txt").write_text("line1\npatched\n", encoding="utf-8")
        (self.sub_dir / "newfile.txt").write_text("added content\n", encoding="utf-8")
        (self.sub_dir / "scratch.ignored").write_text("must not be exported\n", encoding="utf-8")
        code, _ = self._run_cli("export")
        self.assertEqual(code, 0)
        # Reset submodule to clean state for tests.
        _git(["checkout", "--", "."], self.sub_dir)
        (self.sub_dir / "newfile.txt").unlink()
        (self.sub_dir / "scratch.ignored").unlink()

    def tearDown(self) -> None:
        if self.tmp.exists():
            shutil.rmtree(self.tmp, onerror=_remove_readonly)

    def _run_cli(self, *extra: str) -> tuple[int, str]:
        buf = io.StringIO()
        argv = [
            "--repo-root",
            str(self.super_dir),
            "--submodule",
            self.sub_rel.as_posix(),
            "--patch",
            str(self.patch_path),
            *extra,
        ]
        with redirect_stdout(buf):
            code = sdk_patch.main(argv)
        return code, buf.getvalue().strip()

    def test_status_and_idempotent_apply(self) -> None:
        code, out = self._run_cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("state=clean", out)
        self.assertIn(self.pinned_commit[:12], out)

        code, out = self._run_cli("apply")
        self.assertEqual(code, 0, out)
        self.assertIn("applied", out)
        self.assertEqual((self.sub_dir / "hello.txt").read_text(encoding="utf-8"), "line1\npatched\n")
        self.assertEqual((self.sub_dir / "newfile.txt").read_text(encoding="utf-8"), "added content\n")

        code, out = self._run_cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("state=patched", out)

        # Applying a second time is a no-op with exit code 0.
        code, out = self._run_cli("apply")
        self.assertEqual(code, 0, out)
        self.assertIn("already applied", out)

    def test_apply_refuses_dirty_or_wrong_commit(self) -> None:
        (self.sub_dir / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
        code, out = self._run_cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("state=dirty-other", out)

        code, out = self._run_cli("apply")
        self.assertNotEqual(code, 0)
        self.assertIn("refused", out)
        (self.sub_dir / "unrelated.txt").unlink()

        _git(["checkout", self.newer_commit], self.sub_dir)
        code, out = self._run_cli("status")
        self.assertNotEqual(code, 0)
        self.assertIn("mismatch", out)

        code, out = self._run_cli("apply")
        self.assertNotEqual(code, 0)
        self.assertIn("refused", out)

    def test_export_includes_new_and_binary_excludes_ignored_and_preserves_index(self) -> None:
        code, _ = self._run_cli("apply")
        self.assertEqual(code, 0)
        (self.sub_dir / "extra.txt").write_text("brand new\n", encoding="utf-8")
        (self.sub_dir / "blob.bin").write_bytes(b"\x00\x01\x02\xffbinary")
        (self.sub_dir / "skip.ignored").write_text("ignored\n", encoding="utf-8")

        # Verify real index stays clean before and after export.
        self.assertEqual(_git(["diff", "--cached", "--name-only"], self.sub_dir), "")
        code, out = self._run_cli("export")
        self.assertEqual(code, 0, out)
        self.assertEqual(_git(["diff", "--cached", "--name-only"], self.sub_dir), "")

        patch_text = self.patch_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("extra.txt", patch_text)
        self.assertIn("blob.bin", patch_text)
        self.assertNotIn("skip.ignored", patch_text)

        code, out = self._run_cli("check")
        self.assertEqual(code, 0, out)
        self.assertIn("check: ok", out)

    def test_crlf_patch_applies_and_checks_against_lf_submodule(self) -> None:
        raw = self.patch_path.read_bytes()
        crlf_patch = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        self.patch_path.write_bytes(crlf_patch)

        code, out = self._run_cli("check")
        self.assertEqual(code, 0, out)
        self.assertIn("check: ok", out)

        code, out = self._run_cli("apply")
        self.assertEqual(code, 0, out)
        code, out = self._run_cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("state=patched", out)

    def test_check_fails_on_broken_patch(self) -> None:
        self.patch_path.write_text(
            "diff --git a/nonexistent.txt b/nonexistent.txt\n"
            "--- a/nonexistent.txt\n"
            "+++ b/nonexistent.txt\n"
            "@@ -1 +1 @@\n"
            "-nope\n"
            "+still nope\n",
            encoding="utf-8",
        )
        code, out = self._run_cli("check")
        self.assertNotEqual(code, 0)
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
