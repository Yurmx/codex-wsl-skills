#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()


CODEX_HOME = codex_home()
MEM_HOME = CODEX_HOME / "memories"
REPO_HOME = MEM_HOME / "repos"
INDEX_BUILDER = Path(__file__).resolve().parent / "build_memory_index.py"
GLOBAL_FILES = [
    MEM_HOME / "memory_summary.md",
    MEM_HOME / "MEMORY.md",
]
GLOBAL_CAPSULE = MEM_HOME / "memory_capsule.md"


@dataclass
class Match:
    source: str
    path: str
    score: int
    heading: str
    excerpt: str


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def repo_dirs() -> list[Path]:
    if not REPO_HOME.exists():
        return []
    return sorted([p for p in REPO_HOME.iterdir() if p.is_dir()])


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\.git$", "", value)
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-")


def origin_slug(cwd: Path) -> str:
    url = run_git(cwd, "remote", "get-url", "origin")
    if not url:
        return ""
    tail = url.rstrip("/").split("/")[-1]
    if ":" in tail and "/" not in tail:
        tail = tail.split(":")[-1]
    return slugify(tail)


def top_level_slug(cwd: Path) -> str:
    top = run_git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return ""
    return slugify(Path(top).name)


def fuzzy_repo_match(name: str, options: Iterable[str]) -> str:
    name = slugify(name)
    if not name:
        return ""
    if name in options:
        return name
    for opt in options:
        if opt in name or name in opt:
            return opt
    prefixes = [opt for opt in options if name.startswith(opt) or opt.startswith(name)]
    return prefixes[0] if prefixes else ""


def resolve_repo(cwd: Path, explicit_repo: str | None) -> tuple[str, list[str]]:
    options = [p.name for p in repo_dirs()]
    checked: list[str] = []
    if explicit_repo:
        candidate = slugify(explicit_repo)
        checked.append(candidate)
        if candidate in options:
            return candidate, checked
        fuzzy = fuzzy_repo_match(candidate, options)
        if fuzzy:
            return fuzzy, checked
    for candidate in (origin_slug(cwd), top_level_slug(cwd), slugify(cwd.name)):
        if not candidate:
            continue
        checked.append(candidate)
        if candidate in options:
            return candidate, checked
        fuzzy = fuzzy_repo_match(candidate, options)
        if fuzzy:
            return fuzzy, checked
    return "", checked


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def ensure_index(repo: str | None = None, include_global: bool = False) -> None:
    cmd = [sys.executable, str(INDEX_BUILDER)]
    if repo:
        repo_dir = REPO_HOME / repo
        index_path = repo_dir / "memory_index.json"
        capsule_path = repo_dir / "memory_capsule.md"
        memory_path = repo_dir / "MEMORY.md"
        summary_path = repo_dir / "memory_summary.md"
        stale = (
            not index_path.exists()
            or not capsule_path.exists()
            or any(
                p.exists()
                and p.stat().st_mtime
                > min(
                    index_path.stat().st_mtime if index_path.exists() else 0,
                    capsule_path.stat().st_mtime if capsule_path.exists() else 0,
                )
                for p in (memory_path, summary_path)
            )
        )
        if stale:
            cmd += ["--repo", repo]
    if include_global:
        index_path = MEM_HOME / "memory_index.json"
        capsule_path = GLOBAL_CAPSULE
        memory_path = MEM_HOME / "MEMORY.md"
        summary_path = MEM_HOME / "memory_summary.md"
        stale = (
            not index_path.exists()
            or not capsule_path.exists()
            or any(
                p.exists()
                and p.stat().st_mtime
                > min(
                    index_path.stat().st_mtime if index_path.exists() else 0,
                    capsule_path.stat().st_mtime if capsule_path.exists() else 0,
                )
                for p in (memory_path, summary_path)
            )
        )
        if stale:
            cmd += ["--global"]
    if len(cmd) > 2:
        subprocess.run(cmd, check=False, capture_output=True, text=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def score_text(heading: str, body: str, terms: list[str]) -> int:
    if not terms:
        return 0
    heading_low = heading.lower()
    body_low = body.lower()
    score = 0
    for term in terms:
        if term in heading_low:
            score += 12
        score += body_low.count(term) * 3
    phrase = " ".join(terms)
    if phrase and phrase in body_low:
        score += 15
    return score


def summary_match(path: Path, terms: list[str], source: str) -> list[Match]:
    text = read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    if not terms:
        excerpt = "\n".join(lines[:20]).strip()
        return [Match(source, str(path), 10, path.name, excerpt)]
    matches: list[Match] = []
    for idx, line in enumerate(lines):
        excerpt_lines = lines[idx : min(len(lines), idx + 5)]
        excerpt = "\n".join(excerpt_lines).strip()
        score = score_text(path.name, excerpt, terms)
        if score <= 0:
            continue
        matches.append(Match(source, str(path), 20 + score, path.name, excerpt))
    return matches


def clean_block_body(heading: str, body: str) -> str:
    lines = body.splitlines()
    heading_clean = heading.lstrip("#").strip()
    while lines and lines[0].lstrip("#").strip() == heading_clean:
        lines = lines[1:]
    return "\n".join(lines).strip()


def capsule_matches(path: Path, terms: list[str], source: str) -> list[Match]:
    text = read_text(path)
    if not text:
        return []
    sections = memory_blocks(path)
    matches: list[Match] = []
    for heading, body in sections:
        if heading.startswith("# Repo Memory Capsule") or heading.startswith("# Global Memory Capsule"):
            continue
        cleaned = clean_block_body(heading, body)
        if not cleaned:
            continue
        excerpt_lines = cleaned.splitlines()[:10]
        excerpt = "\n".join(excerpt_lines).strip()
        if not terms:
            score = 35 if heading.startswith("## Current Anchor") else 30
        else:
            score = score_text(heading, cleaned, terms)
            if score <= 0:
                continue
        matches.append(Match(source, str(path), 40 + score, heading, excerpt))
    return matches


def memory_blocks(path: Path) -> list[tuple[str, str]]:
    text = read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    current_heading = path.name
    current: list[str] = []
    for line in lines:
        if line.startswith("#"):
            if current:
                blocks.append((current_heading, "\n".join(current).strip()))
                current = []
            current_heading = line.strip()
        current.append(line)
    if current:
        blocks.append((current_heading, "\n".join(current).strip()))
    return blocks


def block_matches(path: Path, terms: list[str], source: str) -> list[Match]:
    matches: list[Match] = []
    for heading, body in memory_blocks(path):
        if not terms:
            continue
        cleaned = clean_block_body(heading, body)
        if not cleaned:
            continue
        score = score_text(heading, cleaned, terms)
        if score <= 0:
            continue
        excerpt_lines = cleaned.splitlines()[:14]
        matches.append(Match(source, str(path), 50 + score, heading, "\n".join(excerpt_lines).strip()))
    return matches


def tokenize(query: str) -> list[str]:
    stop = {
        "with",
        "from",
        "that",
        "this",
        "have",
        "into",
        "then",
        "than",
        "when",
        "only",
        "after",
        "before",
        "across",
        "about",
        "still",
    }
    out = []
    for token in re.findall(r"[a-zA-Z0-9_.-]+", query):
        token = token.lower()
        if len(token) < 3 or token in stop:
            continue
        out.append(token)
    return out


def index_matches(index_path: Path, terms: list[str], source: str) -> list[Match]:
    if not index_path.exists():
        return []
    data = read_json(index_path)
    matches: list[Match] = []
    task_groups = data.get("task_groups", [])
    for group in task_groups:
        title = group.get("title", "")
        scope = group.get("scope", "")
        keywords = " ".join(group.get("keywords", []))
        learnings = " ".join(group.get("learnings", [])[:6])
        body = "\n".join(
            part for part in [scope, keywords, learnings] if part
        )
        if terms:
            score = score_text(title, body, terms)
            if group.get("keywords"):
                keyword_hits = sum(1 for term in terms if term in " ".join(group.get("keywords", [])).lower())
                score += keyword_hits * 10
            if score <= 0:
                continue
        else:
            score = 20
        excerpt_parts = []
        if scope:
            excerpt_parts.append(f"scope: {scope}")
        if group.get("keywords"):
            excerpt_parts.append("keywords: " + ", ".join(group["keywords"][:12]))
        if group.get("learnings"):
            excerpt_parts.extend(f"- {item}" for item in group["learnings"][:4])
        matches.append(
            Match(
                source=source,
                path=str(index_path),
                score=60 + score,
                heading=title,
                excerpt="\n".join(excerpt_parts).strip(),
            )
        )
    return matches


def clamp_excerpt(text: str, max_lines: int, max_chars: int) -> str:
    lines = text.splitlines()
    clipped = "\n".join(lines[:max_lines]).strip()
    if len(clipped) <= max_chars:
        return clipped
    return clipped[: max_chars - 1].rstrip() + "…"


def trim_match_for_compaction(item: Match) -> Match:
    lines = [line.strip() for line in item.excerpt.splitlines() if line.strip()]
    excerpt = "\n".join(lines[:2]).strip()
    return Match(item.source, item.path, item.score, item.heading, excerpt)


def prune_compaction_matches(matches: list[Match], include_global: bool) -> list[Match]:
    buckets: dict[str, list[Match]] = {"repo": [], "global": []}
    for item in matches:
        if item.source.startswith("repo_"):
            buckets["repo"].append(trim_match_for_compaction(item))
        elif include_global and item.source.startswith("global_"):
            buckets["global"].append(trim_match_for_compaction(item))

    out: list[Match] = []
    if buckets["repo"]:
        out.append(sorted(buckets["repo"], key=lambda m: (-m.score, m.heading))[0])
    if include_global and buckets["global"]:
        out.append(sorted(buckets["global"], key=lambda m: (-m.score, m.heading))[0])
    return out


def collect_matches(
    repos: list[str],
    terms: list[str],
    include_global: bool,
    mode: str,
    max_matches: int,
    max_excerpt_lines: int,
    max_excerpt_chars: int,
) -> dict[str, object]:
    matches: list[Match] = []
    for repo in repos:
        ensure_index(repo=repo)
        repo_root = REPO_HOME / repo if repo else None
        repo_files: list[Path] = []
        if repo_root and repo_root.exists():
            capsule_path = repo_root / "memory_capsule.md"
            if mode in ("compact", "compaction", "standard"):
                matches.extend(capsule_matches(capsule_path, terms, "repo_capsule"))
            matches.extend(index_matches(repo_root / "memory_index.json", terms, "repo_index"))
            repo_files = [repo_root / "memory_summary.md", repo_root / "MEMORY.md"]
            if mode != "compact":
                matches.extend(summary_match(repo_files[0], terms, "repo_summary"))
            if mode == "deep" and not any(match.source == "repo_index" for match in matches):
                matches.extend(block_matches(repo_files[1], terms, "repo_memory"))

    if include_global:
        ensure_index(include_global=True)
        if mode in ("compact", "compaction", "standard"):
            matches.extend(capsule_matches(GLOBAL_CAPSULE, terms, "global_capsule"))
        matches.extend(index_matches(MEM_HOME / "memory_index.json", terms, "global_index"))
        if mode != "compact":
            matches.extend(summary_match(GLOBAL_FILES[0], terms, "global_summary"))
        if mode == "deep" and not any(match.source == "global_index" for match in matches):
            matches.extend(block_matches(GLOBAL_FILES[1], terms, "global_memory"))

    dedup: dict[tuple[str, str, str], Match] = {}
    for match in matches:
        key = (match.source, match.path, match.heading)
        prev = dedup.get(key)
        if prev is None or match.score > prev.score:
            dedup[key] = match
    matches = list(dedup.values())
    if mode == "compact":
        source_rank = {
            "repo_capsule": 0,
            "global_capsule": 1,
            "repo_index": 2,
            "global_index": 3,
            "repo_summary": 4,
            "global_summary": 5,
            "repo_memory": 6,
            "global_memory": 7,
        }
    elif mode == "compaction":
        source_rank = {
            "repo_index": 0,
            "repo_capsule": 1,
            "global_index": 2,
            "global_capsule": 3,
            "repo_summary": 4,
            "global_summary": 5,
            "repo_memory": 6,
            "global_memory": 7,
        }
    else:
        source_rank = {
            "repo_index": 0,
            "global_index": 1,
            "repo_capsule": 2,
            "global_capsule": 3,
            "repo_summary": 4,
            "global_summary": 5,
            "repo_memory": 6,
            "global_memory": 7,
        }
    matches.sort(key=lambda m: (source_rank.get(m.source, 99), -m.score, m.heading))
    if mode == "compaction":
        matches = prune_compaction_matches(matches, include_global)
    trimmed = []
    for item in matches[:max_matches]:
        trimmed.append(
            {
                **item.__dict__,
                "excerpt": clamp_excerpt(item.excerpt, max_excerpt_lines, max_excerpt_chars),
            }
        )
    return {
        "repos": repos,
        "mode": mode,
        "matches": trimmed,
    }


def compact_heading(item: dict[str, object]) -> str:
    source = item["source"]
    heading = item["heading"]
    if source == "repo_capsule":
        return f"repo memory: {heading.lstrip('# ').strip()}"
    if source == "global_capsule":
        return f"global memory: {heading.lstrip('# ').strip()}"
    if source == "repo_index":
        return f"repo index: {heading}"
    if source == "global_index":
        return f"global index: {heading}"
    return heading


def resolve_effective_mode(requested_mode: str, terms: list[str], include_global: bool, all_repos: bool) -> str:
    if requested_mode != "compact":
        return requested_mode
    if include_global or all_repos:
        return "compact"
    if not terms or len(terms) <= 8:
        return "compaction"
    return "compact"


def print_text(result: dict[str, object], checked: list[str], cwd: Path) -> None:
    repos = result["repos"]
    matches = result["matches"]
    if not matches:
        print(f"repo: {', '.join(repos) if repos else '[unresolved]'}")
        print("No matching memory found.")
        return
    if result["mode"] in {"compact", "compaction"}:
        print(f"repo: {', '.join(repos) if repos else '[unresolved]'}")
        for item in matches:
            print("")
            print(f"{compact_heading(item)}")
            print(item["excerpt"])
        return
    print(f"cwd: {cwd}")
    print(f"repos: {', '.join(repos) if repos else '[unresolved]'}")
    print(f"checked: {', '.join(checked) if checked else '[none]'}")
    print("")
    for item in matches:
        print(f"[{item['source']}] {item['heading']} ({item['path']}) score={item['score']}")
        print(item["excerpt"])
        print("")


def main() -> int:
    parser = argparse.ArgumentParser(description="Repo-aware lookup for local Codex memory files.")
    parser.add_argument("--cwd", default=".", help="Working directory used to resolve the repo namespace.")
    parser.add_argument("--repo", action="append", help="Explicit repo memory namespace to use. Repeat for multiple repos.")
    parser.add_argument("--query", default="", help="Optional search query for narrowing results.")
    parser.add_argument("--include-global", action="store_true", help="Also search global memory files.")
    parser.add_argument("--all-repos", action="store_true", help="Search all repo memory namespaces.")
    parser.add_argument("--mode", choices=["compact", "compaction", "standard", "deep"], default="compact", help="compact: slim capsule/index lookup; compaction: minimum durable anchor set for long-lived threads; standard: capsule, index, and summary; deep: allow full memory block fallback.")
    parser.add_argument("--max-matches", type=int, default=4, help="Maximum number of matches to return.")
    parser.add_argument("--max-excerpt-lines", type=int, default=4, help="Maximum lines per excerpt.")
    parser.add_argument("--max-excerpt-chars", type=int, default=260, help="Maximum characters per excerpt.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()
    terms = tokenize(args.query)
    effective_mode = resolve_effective_mode(args.mode, terms, args.include_global, args.all_repos)
    if effective_mode in {"compact", "compaction"}:
        if not args.query:
            if args.max_matches == 4:
                args.max_matches = 1 if effective_mode == "compaction" else 2
            if args.max_excerpt_lines == 4:
                args.max_excerpt_lines = 2 if effective_mode == "compaction" else 3
            if args.max_excerpt_chars == 260:
                args.max_excerpt_chars = 140 if effective_mode == "compaction" else 180
        else:
            if args.max_matches == 4:
                args.max_matches = 2 if effective_mode == "compaction" else 3
            if args.max_excerpt_chars == 260:
                args.max_excerpt_chars = 170 if effective_mode == "compaction" else 220

    cwd = Path(args.cwd).resolve()
    checked: list[str] = []
    if args.all_repos:
        repos = [p.name for p in repo_dirs()]
    elif args.repo:
        repos = []
        for explicit in args.repo:
            repo, repo_checked = resolve_repo(cwd, explicit)
            checked.extend(repo_checked)
            if repo and repo not in repos:
                repos.append(repo)
    else:
        repo, repo_checked = resolve_repo(cwd, None)
        checked.extend(repo_checked)
        repos = [repo] if repo else []
    result = collect_matches(
        repos,
        terms,
        args.include_global,
        effective_mode,
        args.max_matches,
        args.max_excerpt_lines,
        args.max_excerpt_chars,
    )
    result["requested_mode"] = args.mode
    result["effective_mode"] = effective_mode
    result["checked"] = checked
    result["cwd"] = str(cwd)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_text(result, checked, cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
