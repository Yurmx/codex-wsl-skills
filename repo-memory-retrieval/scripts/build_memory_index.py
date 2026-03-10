#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()


MEM_HOME = codex_home() / "memories"
REPO_HOME = MEM_HOME / "repos"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []


def parse_keywords(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if "`" in stripped:
            parts = stripped.split("`")
            items.extend([part.strip() for i, part in enumerate(parts) if i % 2 == 1 and part.strip()])
        elif stripped.startswith("-"):
            items.extend([part.strip() for part in stripped[1:].split(",") if part.strip()])
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def parse_bullets(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def section_text(lines: list[str]) -> str:
    return "\n".join(line.rstrip() for line in lines if line.strip()).strip()


def parse_task_groups(memory_path: Path, heading_prefix: str) -> list[dict]:
    lines = read_lines(memory_path)
    groups: list[dict] = []
    current: dict | None = None
    current_section: str | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        current["keywords"] = parse_keywords(current.pop("_keywords_lines"))
        current["rollout_summary_files"] = parse_bullets(current.pop("_rollout_lines"))
        learnings = parse_bullets(current.pop("_learnings_lines"))
        if not learnings:
            learnings = [line.strip() for line in current.pop("_body_lines") if line.strip().startswith("- ")]
        else:
            current.pop("_body_lines")
        current["learnings"] = learnings
        current["summary"] = " | ".join(learnings[:4]) if learnings else section_text(current.get("_summary_lines", []))
        current.pop("_summary_lines", None)
        groups.append(current)
        current = None

    for line in lines:
        if line.startswith(heading_prefix):
            flush()
            current = {
                "title": line.split(":", 1)[1].strip(),
                "scope": "",
                "_keywords_lines": [],
                "_rollout_lines": [],
                "_learnings_lines": [],
                "_body_lines": [],
                "_summary_lines": [],
                "source_path": str(memory_path),
            }
            current_section = None
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("scope:"):
            current["scope"] = stripped.split(":", 1)[1].strip()
            continue
        if stripped in ("### keywords", "- keywords:"):
            current_section = "keywords"
            continue
        if stripped in ("### rollout_summary_files", "- rollout_summary_files:"):
            current_section = "rollout"
            continue
        if stripped in ("### learnings", "- learnings:"):
            current_section = "learnings"
            continue
        if stripped.startswith("#"):
            current_section = None
        if current_section == "keywords":
            current["_keywords_lines"].append(line)
        elif current_section == "rollout":
            current["_rollout_lines"].append(line)
        elif current_section == "learnings":
            current["_learnings_lines"].append(line)
        else:
            current["_body_lines"].append(line)
            if len(current["_summary_lines"]) < 12:
                current["_summary_lines"].append(line)

    flush()
    return groups


def parse_main_working_themes(memory_path: Path) -> list[str]:
    lines = read_lines(memory_path)
    themes: list[str] = []
    active = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Main Working Themes"):
            active = True
            continue
        if active and stripped.startswith("## "):
            break
        if active and stripped.startswith("- "):
            themes.append(stripped[2:].strip())
    return themes


def build_repo_index(repo_dir: Path) -> dict:
    memory_path = repo_dir / "MEMORY.md"
    summary_path = repo_dir / "memory_summary.md"
    return {
        "namespace": "repo",
        "repo": repo_dir.name,
        "generated_at": now_utc(),
        "memory_path": str(memory_path),
        "memory_summary_path": str(summary_path),
        "main_working_themes": parse_main_working_themes(memory_path),
        "task_groups": parse_task_groups(memory_path, "## Task Group:"),
    }


def build_global_index() -> dict:
    memory_path = MEM_HOME / "MEMORY.md"
    summary_path = MEM_HOME / "memory_summary.md"
    return {
        "namespace": "global",
        "generated_at": now_utc(),
        "memory_path": str(memory_path),
        "memory_summary_path": str(summary_path),
        "task_groups": parse_task_groups(memory_path, "# Task Group:"),
    }


def write_index(index_path: Path, payload: dict) -> None:
    index_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_capsule_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    namespace = payload.get("namespace", "repo")
    if namespace == "repo":
        lines.append(f"# Repo Memory Capsule: {payload.get('repo', 'unknown')}")
        lines.append("")
        lines.append("## Current Anchor")
        summary_path = Path(payload.get("memory_summary_path", ""))
        if summary_path.exists():
            summary_lines = [
                line.strip()
                for line in summary_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            ]
            for line in summary_lines[:6]:
                lines.append(line)
        lines.append("")
        themes = payload.get("main_working_themes", [])
        if themes:
            lines.append("## Main Working Themes")
            for theme in themes[:6]:
                lines.append(f"- {theme}")
            lines.append("")
    else:
        lines.append("# Global Memory Capsule")
        lines.append("")

    lines.append("## Priority Task Groups")
    for group in payload.get("task_groups", [])[:8]:
        lines.append(f"### {group.get('title', 'Untitled')}")
        scope = group.get("scope", "").strip()
        if scope:
            lines.append(f"- scope: {scope}")
        keywords = group.get("keywords", [])
        if keywords:
            lines.append("- keywords: " + ", ".join(keywords[:8]))
        learnings = group.get("learnings", [])
        if learnings:
            for item in learnings[:3]:
                lines.append(f"- {item}")
        elif group.get("summary"):
            lines.append(f"- {group['summary']}")
        lines.append("")
    return lines


def write_capsule(capsule_path: Path, payload: dict) -> None:
    capsule_path.write_text("\n".join(build_capsule_lines(payload)).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build structured indexes for local Codex memory files.")
    parser.add_argument("--repo", action="append", help="Repo namespace to index. Repeat for multiple repos.")
    parser.add_argument("--all-repos", action="store_true", help="Index all repo namespaces under ~/.codex/memories/repos.")
    parser.add_argument("--global", dest="include_global", action="store_true", help="Also build the global memory index.")
    args = parser.parse_args()

    outputs: list[str] = []

    repos: list[Path] = []
    if args.all_repos and REPO_HOME.exists():
        repos.extend(sorted([p for p in REPO_HOME.iterdir() if p.is_dir()]))
    elif args.repo:
        for repo in args.repo:
            candidate = REPO_HOME / repo
            if candidate.is_dir():
                repos.append(candidate)

    seen = set()
    for repo_dir in repos:
        if repo_dir in seen:
            continue
        seen.add(repo_dir)
        payload = build_repo_index(repo_dir)
        index_path = repo_dir / "memory_index.json"
        write_index(index_path, payload)
        write_capsule(repo_dir / "memory_capsule.md", payload)
        outputs.append(str(index_path))
        outputs.append(str(repo_dir / "memory_capsule.md"))

    if args.include_global:
        global_path = MEM_HOME / "memory_index.json"
        payload = build_global_index()
        write_index(global_path, payload)
        write_capsule(MEM_HOME / "memory_capsule.md", payload)
        outputs.append(str(global_path))
        outputs.append(str(MEM_HOME / "memory_capsule.md"))

    print(json.dumps({"written": outputs}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
