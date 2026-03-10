#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
import shutil


def run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=check)


def repo_root() -> Path:
    result = run("git", "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip())


def load_patterns(root: Path) -> list[str]:
    patterns: list[str] = []
    for name in (".sanitization.default.txt", ".sanitization.local.txt"):
        path = root / name
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in patterns:
        if pattern in seen:
            continue
        seen.add(pattern)
        ordered.append(pattern)
    return ordered


def tracked_files(root: Path) -> list[str]:
    result = run("git", "ls-files", cwd=root)
    return [line for line in result.stdout.splitlines() if line]


def file_contains_pattern(path: Path, pattern: str) -> list[str]:
    matches: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return matches
    for idx, line in enumerate(text.splitlines(), start=1):
        if pattern in line:
            matches.append(f"{path.as_posix()}:{idx}:{line.strip()}")
    return matches


def scan_worktree(root: Path, patterns: list[str]) -> list[str]:
    if not patterns:
        return []
    files = tracked_files(root)
    if not files:
        return []
    matches: list[str] = []
    rg_path = shutil.which("rg")
    if rg_path:
        for pattern in patterns:
            result = subprocess.run(
                [rg_path, "-n", "-F", pattern, *files],
                cwd=str(root),
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    matches.append(f"worktree:{line}")
        return matches

    for pattern in patterns:
        for rel in files:
            path = root / rel
            for line in file_contains_pattern(path, pattern):
                matches.append(f"worktree:{line}")
    return matches


def revs_for_history(root: Path, history_ref: str | None) -> list[str]:
    ref = history_ref or "HEAD"
    result = run("git", "rev-list", ref, cwd=root)
    return [line for line in result.stdout.splitlines() if line]


def scan_history(root: Path, patterns: list[str], history_ref: str | None) -> list[str]:
    if not patterns:
        return []
    matches: list[str] = []
    revs = revs_for_history(root, history_ref)
    git_path = shutil.which("git") or "git"
    for pattern in patterns:
        for rev in revs:
            result = subprocess.run(
                [git_path, "grep", "-n", "-F", pattern, rev],
                cwd=str(root),
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    matches.append(f"history:{line}")
                break
    return matches


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if tracked content or history contains unsanitized private strings.")
    parser.add_argument("--history", default=None, help="History ref to scan, e.g. main or HEAD. Omit to scan only HEAD history.")
    args = parser.parse_args()

    root = repo_root()
    patterns = load_patterns(root)
    if not patterns:
        print("No sanitization patterns configured.")
        return 0

    findings = scan_worktree(root, patterns)
    findings.extend(scan_history(root, patterns, args.history))

    if findings:
        print("Sanitization check failed. Remove or rewrite the following matches before pushing:")
        for item in findings:
            print(item)
        return 1

    print("Sanitization check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
