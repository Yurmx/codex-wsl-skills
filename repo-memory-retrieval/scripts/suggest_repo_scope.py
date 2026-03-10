#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path


STOP = {
    "are",
    "the",
    "and",
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
    "where",
    "what",
    "which",
    "does",
    "under",
    "your",
    "reply",
    "exactly",
    "sentence",
    "project",
    "repo",
}

FRONTEND_HINTS = {"frontend", "ui", "react", "component", "components", "page", "pages", "panel", "panels"}
BACKEND_HINTS = {"backend", "api", "route", "routes", "server", "feed", "feeds", "daemon"}


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def tokenize(query: str) -> list[str]:
    tokens = [part.lower() for part in re.split(r"[^a-zA-Z0-9_./-]+", query) if part.strip()]
    cleaned: list[str] = []
    for token in tokens:
        if len(token) < 3 or token in STOP:
            continue
        cleaned.append(token)
    return cleaned[:12]


def candidate_dirs(root: Path, max_depth: int = 2) -> list[Path]:
    out: list[Path] = [root]
    for child in sorted(root.rglob("*")):
        if not child.is_dir():
            continue
        rel = child.relative_to(root)
        if len(rel.parts) > max_depth:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(part in {"node_modules", "dist", "build", ".vite"} for part in rel.parts):
            continue
        out.append(child)
    return out


def rg_matches(root: Path, terms: list[str]) -> list[Path]:
    if not terms:
        return []
    pattern = "|".join(re.escape(term) for term in terms)
    result = subprocess.run(
        ["rg", "-l", "-i", "-m", "1", pattern, str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return []
    out: list[Path] = []
    for line in result.stdout.splitlines():
        path = Path(line.strip())
        if path.exists():
            out.append(path)
    return out[:200]


def file_path_matches(root: Path, terms: list[str]) -> list[Path]:
    if not terms:
        return []
    result = subprocess.run(["rg", "--files", str(root)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    out: list[Path] = []
    for line in result.stdout.splitlines():
        rel = line.strip().lower()
        if not rel:
            continue
        if any(term in rel for term in terms):
            path = Path(line.strip())
            full = path if path.is_absolute() else root / path
            if full.exists():
                out.append(full)
    return out[:200]


def suggest_scopes(root: Path, query: str, limit: int) -> dict:
    terms = tokenize(query)
    candidates: dict[str, dict] = {}

    def ensure_candidate(path: Path) -> dict:
        rel = "."
        if path != root:
            rel = path.relative_to(root).as_posix()
        if rel not in candidates:
            candidates[rel] = {
                "cwd": str(path),
                "relative": rel,
                "score": 0,
                "reasons": [],
                "matched_files": [],
            }
        return candidates[rel]

    for directory in candidate_dirs(root):
        ensure_candidate(directory)

    for rel, item in candidates.items():
        parts = rel.split("/") if rel != "." else [root.name]
        joined = " ".join(parts).lower()
        score = 0
        reasons = []
        for term in terms:
            if term in joined:
                score += 48 if rel != "." else 8
                reasons.append(f"path term match: {term}")
            if "/" in term and rel != "." and rel == term:
                score += 140
                reasons.append(f"exact path match: {term}")
        if rel.startswith("src"):
            if any(term in FRONTEND_HINTS for term in terms):
                score += 28 if rel == "src" else 44
                reasons.append("frontend hint")
            if "frontend" in terms:
                score += 84 if rel == "src" else 112
                reasons.append("explicit frontend")
        if rel.startswith("server"):
            if any(term in BACKEND_HINTS for term in terms):
                score += 28 if rel == "server" else 44
                reasons.append("backend hint")
            if "backend" in terms:
                score += 84 if rel == "server" else 112
                reasons.append("explicit backend")
        if rel.startswith("server") and any(term in FRONTEND_HINTS for term in terms) and not any(
            term in BACKEND_HINTS for term in terms
        ):
            score -= 120
            reasons.append("frontend penalty")
        if rel.startswith("src") and any(term in BACKEND_HINTS for term in terms) and not any(
            term in FRONTEND_HINTS for term in terms
        ):
            score -= 120
            reasons.append("backend penalty")
        item["score"] += score
        item["reasons"].extend(reasons[:3])

    file_hits = rg_matches(root, terms)
    path_hits = file_path_matches(root, terms)
    path_scores: dict[str, int] = defaultdict(int)
    path_files: dict[str, list[str]] = defaultdict(list)

    for file_path in file_hits:
        rel = file_path.relative_to(root)
        parents = [root]
        if len(rel.parts) >= 1:
            parents.append(root / rel.parts[0])
        if len(rel.parts) >= 2:
            parents.append(root / rel.parts[0] / rel.parts[1])
        seen = set()
        for parent in parents:
            rel_parent = "." if parent == root else parent.relative_to(root).as_posix()
            if rel_parent in seen:
                continue
            seen.add(rel_parent)
            path_scores[rel_parent] += 1 if rel_parent == "." else 4
            if len(path_files[rel_parent]) < 3:
                path_files[rel_parent].append(rel.as_posix())

    for file_path in path_hits:
        rel = file_path.relative_to(root)
        parents = [root]
        if len(rel.parts) >= 1:
            parents.append(root / rel.parts[0])
        if len(rel.parts) >= 2:
            parents.append(root / rel.parts[0] / rel.parts[1])
        seen = set()
        for parent in parents:
            rel_parent = "." if parent == root else parent.relative_to(root).as_posix()
            if rel_parent in seen:
                continue
            seen.add(rel_parent)
            path_scores[rel_parent] += 6 if rel_parent == "." else 18
            if len(path_files[rel_parent]) < 3:
                path_files[rel_parent].append(rel.as_posix())

    for rel, score in path_scores.items():
        candidate = ensure_candidate(root if rel == "." else root / rel)
        content_cap = 72
        path_cap = 180
        applied = min(score, content_cap)
        if any(match.startswith(rel + "/") or (rel == "." and "/" in match) for match in path_files[rel]):
            applied = min(score, path_cap)
        candidate["score"] += applied
        if path_files[rel]:
            candidate["reasons"].append(f"content hits: {len(path_files[rel])}")
            candidate["matched_files"] = path_files[rel]

    best_non_root = max((item["score"] for item in candidates.values() if item["relative"] != "."), default=0)
    if "." in candidates and best_non_root > 0:
        candidates["."]["score"] -= 12

    ranked = sorted(candidates.values(), key=lambda item: (-item["score"], len(item["relative"])))
    recommended = ranked[0] if ranked else None
    return {
        "repo_root": str(root),
        "query": query,
        "terms": terms,
        "recommended": recommended,
        "suggestions": [item for item in ranked[:limit] if item["score"] > 0 or item["relative"] == "."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Suggest narrower cwd scopes for Codex tasks.")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cwd = Path(args.cwd).resolve()
    top = run_git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        raise SystemExit("Could not resolve git repo root for this cwd.")
    root = Path(top)
    payload = suggest_scopes(root, args.query, args.limit)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"repo_root: {payload['repo_root']}")
    print(f"query: {payload['query']}")
    if payload["terms"]:
        print(f"terms: {', '.join(payload['terms'])}")
    if payload.get("recommended"):
        print(f"recommended: {payload['recommended']['relative']}")
    for suggestion in payload["suggestions"]:
        print("")
        print(f"- {suggestion['relative']}  score={suggestion['score']}")
        for reason in suggestion["reasons"][:3]:
            print(f"  reason: {reason}")
        for matched in suggestion["matched_files"][:3]:
            print(f"  match: {matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
