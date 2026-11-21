#!/usr/bin/env python3
"""Manage the FableDroid Android patch on top of thirdparty/rexglue-sdk."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


DEFAULT_SUBMODULE_REL = Path("thirdparty/rexglue-sdk")
DEFAULT_PATCH_REL = Path("thirdparty/rexglue-sdk-android.patch")


class PatchToolError(RuntimeError):
    """Raised on actionable git or patch state failures."""


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        raise PatchToolError(f"git {' '.join(args)} failed: {detail}")
    return proc


def _run_git_apply(
    extra_args: list[str],
    patch_path: Path,
    cwd: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run `git apply` on patch_path, falling back to LF-normalized stdin on Windows CRLF checkouts."""
    raw = patch_path.read_bytes()
    proc = _run_git(["apply", *extra_args, "-"], cwd=cwd, input_bytes=raw, check=False)
    if proc.returncode != 0 and b"\r\n" in raw:
        lf_bytes = raw.replace(b"\r\n", b"\n")
        proc_lf = _run_git(["apply", *extra_args, "-"], cwd=cwd, input_bytes=lf_bytes, check=False)
        if proc_lf.returncode == 0:
            return proc_lf
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        raise PatchToolError(f"git apply {' '.join(extra_args)} {patch_path} failed: {detail}")
    return proc


def _remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def _rmtree_safe(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onerror=_remove_readonly)


def get_pinned_commit(repo_root: Path, submodule_rel: Path) -> str:
    posix_rel = submodule_rel.as_posix()
    proc = _run_git(["ls-tree", "HEAD", posix_rel], cwd=repo_root)
    line = proc.stdout.decode("utf-8", errors="replace").strip()
    if not line:
        raise PatchToolError(f"submodule '{posix_rel}' not found in HEAD tree")
    parts = line.split()
    if len(parts) < 3 or parts[1] != "commit":
        raise PatchToolError(f"unexpected ls-tree entry for '{posix_rel}': {line}")
    return parts[2]


def get_submodule_head(submodule_dir: Path) -> str:
    if not submodule_dir.is_dir() or not (submodule_dir / ".git").exists():
        raise PatchToolError(
            f"submodule '{submodule_dir}' is not initialized "
            "(run: git submodule update --init thirdparty/rexglue-sdk)"
        )
    proc = _run_git(["rev-parse", "HEAD"], cwd=submodule_dir)
    return proc.stdout.decode("utf-8", errors="replace").strip()


def _parse_porcelain_paths(submodule_dir: Path) -> set[str]:
    proc = _run_git(["status", "--porcelain", "-z", "-uall"], cwd=submodule_dir)
    raw = proc.stdout
    if not raw:
        return set()
    entries = raw.split(b"\0")
    paths: set[str] = set()
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        status_xy = entry[:2]
        path = entry[3:].decode("utf-8", errors="surrogateescape")
        paths.add(path)
        if status_xy[0:1] in (b"R", b"C") or status_xy[1:2] in (b"R", b"C"):
            if i < len(entries) and entries[i]:
                paths.add(entries[i].decode("utf-8", errors="surrogateescape"))
                i += 1
    return paths


def _parse_patch_paths(submodule_dir: Path, patch_path: Path) -> set[str]:
    proc = _run_git_apply(["--numstat", "-z"], patch_path, submodule_dir)
    raw = proc.stdout
    paths: set[str] = set()
    tokens = raw.split(b"\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        fields = tok.split(b"\t", 2)
        if len(fields) < 3:
            continue
        p = fields[2].decode("utf-8", errors="surrogateescape")
        if p == "" and i + 1 < len(tokens):
            old_p = tokens[i].decode("utf-8", errors="surrogateescape")
            new_p = tokens[i + 1].decode("utf-8", errors="surrogateescape")
            i += 2
            if old_p:
                paths.add(old_p)
            if new_p:
                paths.add(new_p)
        elif p:
            paths.add(p)
    return paths


def _classify_tree(submodule_dir: Path, patch_path: Path) -> str:
    dirty = _parse_porcelain_paths(submodule_dir)
    if not dirty:
        return "clean"
    if patch_path.is_file():
        rev = _run_git_apply(
            ["--reverse", "--check", "--whitespace=nowarn"],
            patch_path,
            submodule_dir,
            check=False,
        )
        if rev.returncode == 0:
            patch_paths = _parse_patch_paths(submodule_dir, patch_path)
            if dirty.issubset(patch_paths):
                return "patched"
    return "dirty-other"


def _write_working_tree_hash(repo_dir: Path, base_commit: str) -> str:
    with tempfile.TemporaryDirectory(prefix="sdk_patch_idx_") as tmp:
        tmp_index = Path(tmp) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
        _run_git(["read-tree", base_commit], cwd=repo_dir, env=env)
        _run_git(["add", "-A"], cwd=repo_dir, env=env)
        proc = _run_git(["write-tree"], cwd=repo_dir, env=env)
        return proc.stdout.decode("utf-8", errors="replace").strip()


def _export_diff_bytes(repo_dir: Path, base_commit: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="sdk_patch_exp_") as tmp:
        tmp_index = Path(tmp) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
        _run_git(["read-tree", base_commit], cwd=repo_dir, env=env)
        _run_git(["add", "-A"], cwd=repo_dir, env=env)
        proc = _run_git(
            [
                "-c",
                "diff.noprefix=false",
                "-c",
                "core.quotePath=false",
                "diff",
                "--cached",
                "--binary",
                "--no-ext-diff",
                "--no-color",
                base_commit,
            ],
            cwd=repo_dir,
            env=env,
        )
        return proc.stdout


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def cmd_status(repo_root: Path, submodule_rel: Path, patch_path: Path) -> tuple[int, str]:
    pinned = get_pinned_commit(repo_root, submodule_rel)
    submodule_dir = repo_root / submodule_rel
    head = get_submodule_head(submodule_dir)
    state = _classify_tree(submodule_dir, patch_path)
    match = "match" if head == pinned else "mismatch"
    msg = f"status: pinned={pinned[:12]} head={head[:12]} ({match}) state={state}"
    return (0 if head == pinned else 1), msg


def cmd_apply(repo_root: Path, submodule_rel: Path, patch_path: Path) -> tuple[int, str]:
    if not patch_path.is_file():
        raise PatchToolError(f"patch file not found: {patch_path}")
    pinned = get_pinned_commit(repo_root, submodule_rel)
    submodule_dir = repo_root / submodule_rel
    head = get_submodule_head(submodule_dir)
    if head != pinned:
        return (
            1,
            f"apply: refused (submodule HEAD {head[:12]} != pinned {pinned[:12]})",
        )
    state = _classify_tree(submodule_dir, patch_path)
    if state == "patched":
        return 0, f"apply: already applied at {pinned[:12]}"
    if state != "clean":
        return (
            1,
            f"apply: refused (submodule working tree has unrelated changes: {state})",
        )
    _run_git_apply(["--whitespace=nowarn"], patch_path, submodule_dir)
    return 0, f"apply: applied {patch_path.name} to {pinned[:12]}"


def cmd_export(repo_root: Path, submodule_rel: Path, patch_path: Path) -> tuple[int, str]:
    pinned = get_pinned_commit(repo_root, submodule_rel)
    submodule_dir = repo_root / submodule_rel
    head = get_submodule_head(submodule_dir)
    if head != pinned:
        return (
            1,
            f"export: refused (submodule HEAD {head[:12]} != pinned {pinned[:12]})",
        )
    data = _export_diff_bytes(submodule_dir, pinned)
    _atomic_write_bytes(patch_path, data)
    return 0, f"export: wrote {len(data)} bytes to {patch_path.name} (base {pinned[:12]})"


def cmd_check(repo_root: Path, submodule_rel: Path, patch_path: Path) -> tuple[int, str]:
    if not patch_path.is_file():
        raise PatchToolError(f"patch file not found: {patch_path}")
    pinned = get_pinned_commit(repo_root, submodule_rel)
    submodule_dir = repo_root / submodule_rel
    # Ensure submodule object database is present.
    get_submodule_head(submodule_dir)

    tmp_root = Path(tempfile.mkdtemp(prefix="sdk_patch_check_"))
    wt1 = tmp_root / "wt1"
    wt2 = tmp_root / "wt2"
    exported = tmp_root / "exported.patch"
    created_worktrees: list[Path] = []
    try:
        for wt in (wt1, wt2):
            _run_git(["worktree", "add", "--detach", str(wt), pinned], cwd=submodule_dir)
            created_worktrees.append(wt)

        _run_git_apply(["--whitespace=nowarn"], patch_path, wt1)
        tree1 = _write_working_tree_hash(wt1, pinned)

        exported_bytes = _export_diff_bytes(wt1, pinned)
        _atomic_write_bytes(exported, exported_bytes)

        _run_git_apply(["--whitespace=nowarn"], exported, wt2)
        tree2 = _write_working_tree_hash(wt2, pinned)

        if tree1 != tree2:
            return (
                1,
                f"check: FAILED (tree mismatch: original={tree1[:12]} roundtrip={tree2[:12]})",
            )
        return 0, f"check: ok (pinned={pinned[:12]} tree={tree1[:12]})"
    finally:
        for wt in reversed(created_worktrees):
            _run_git(["worktree", "remove", "--force", str(wt)], cwd=submodule_dir, check=False)
        _run_git(["worktree", "prune"], cwd=submodule_dir, check=False)
        _rmtree_safe(tmp_root)


def build_parser() -> argparse.ArgumentParser:
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Apply, export, inspect, and round-trip check the SDK Android patch."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=default_root,
        help="Superproject root directory (default: repo root)",
    )
    parser.add_argument(
        "--submodule",
        type=Path,
        default=DEFAULT_SUBMODULE_REL,
        help="Submodule path relative to --repo-root (default: thirdparty/rexglue-sdk)",
    )
    parser.add_argument(
        "--patch",
        type=Path,
        default=None,
        help="Patch file path (default: <repo-root>/thirdparty/rexglue-sdk-android.patch)",
    )
    parser.add_argument(
        "command",
        choices=("status", "apply", "export", "check"),
        help="Action to perform",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    patch_path = (
        args.patch.resolve()
        if args.patch is not None
        else (repo_root / DEFAULT_PATCH_REL).resolve()
    )
    handlers = {
        "status": cmd_status,
        "apply": cmd_apply,
        "export": cmd_export,
        "check": cmd_check,
    }
    try:
        code, message = handlers[args.command](repo_root, args.submodule, patch_path)
    except PatchToolError as exc:
        print(f"{args.command}: error ({exc})")
        return 1
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
