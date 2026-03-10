---
name: repo-memory-retrieval
description: Use when a thread needs project-specific memory without re-enabling Codex app memories, especially across multiple repos or worktrees. Resolves the current repo/worktree to the right memory namespace, retrieves only relevant repo-scoped memory first, and falls back to global memory only for cross-project/platform issues.
---

# Repo Memory Retrieval

Use this skill to retrieve local file-based memory with minimal context bloat.

## When to use

- The user is working across multiple repos or worktrees at once.
- The task needs prior project context, but app-level `memories` should stay disabled.
- The task involves a repo-specific history, prior rollout, known guardrails, or stable operating conventions.
- The issue may be cross-project (`Codex`, `WSL`, launcher, config parity), in which case repo memory may be insufficient and global memory should be consulted after repo memory.

## Workflow

1. Resolve the active repo namespace from the current `cwd`.
2. If the task is bounded to a subsystem, suggest a narrower `cwd` first.
3. Query repo-local memory first.
4. Read only the returned summary lines / excerpts that are relevant.
5. Fall back to global memory only if the repo lookup is empty or the issue is clearly cross-project.
6. Stay in `compact` mode by default.
7. Let `compact` auto-collapse to a compaction-safe shape for empty or short single-repo queries.
8. Only escalate to `standard` or `deep` if the compact capsule is insufficient.

## Commands

Current repo summary only:

```bash
python3 scripts/repo_memory_lookup.py --cwd "$PWD"
```

Suggest a narrower working scope before a heavy repo task:

```bash
python3 scripts/suggest_repo_scope.py --cwd "$PWD" --query "your task here"
```

Use the suggested narrower `cwd` when the task is bounded to one subsystem. Stay at repo root only when the task truly spans multiple subsystems.

Repo-scoped query:

```bash
python3 scripts/repo_memory_lookup.py --cwd "$PWD" --query "task boundary launch integrity"
```

Compaction-aware query for long-lived threads:

```bash
python3 scripts/repo_memory_lookup.py --cwd "$PWD" --query "task boundary launch integrity" --mode compaction
```

Repo query with global fallback:

```bash
python3 scripts/repo_memory_lookup.py --cwd "$PWD" --query "Codex WSL startup CPU stale thread" --include-global
```

Standard mode when compact output is too terse:

```bash
python3 scripts/repo_memory_lookup.py --cwd "$PWD" --query "task boundary launch integrity" --mode standard
```

Deep mode only when the specific task group details are required:

```bash
python3 scripts/repo_memory_lookup.py --cwd "$PWD" --query "governance benchmark runtime" --mode deep
```

Machine-readable output:

```bash
python3 scripts/repo_memory_lookup.py --cwd "$PWD" --query "governance benchmark runtime" --json
```

## Retrieval rules

- Prefer `repos/<repo>/memory_summary.md` before `repos/<repo>/MEMORY.md`.
- Prefer `repos/<repo>/memory_capsule.md` before broader summary or full memory blocks.
- Prefer `compact` for normal use; it now auto-shifts toward a compaction-safe shape for empty or short queries.
- Prefer `compaction` mode when you only need a durable anchor for a long-running thread.
- Use `repos/<repo>/MEMORY.md` only for the specific matching task group or learning block.
- Use global `$CODEX_HOME/memories/memory_summary.md` and `$CODEX_HOME/memories/MEMORY.md` only for:
  - Codex Desktop / WSL / launcher / config issues
  - cross-repo workflows
  - memory-management tasks themselves
- Do not bulk-load full memory files unless the user explicitly wants a broad memory audit.
- Keep `--max-matches` and excerpt budgets small unless the user clearly needs wider recall.
- For bounded repo tasks, suggest a narrower `cwd` before running heavy Codex work. Root-level runs can be materially more expensive than subdirectory-scoped runs.

## Namespace resolution

The lookup script resolves the repo in this order:

1. explicit `--repo`
2. git remote slug from `origin`
3. git top-level basename
4. cwd basename / fuzzy match against `$CODEX_HOME/memories/repos/*`

This is what lets feature worktrees resolve back to the canonical repo memory folder instead of creating duplicate namespaces.

## Cross-project usage

If you are coordinating multiple repos in one turn:

- run the lookup separately for each repo/worktree
- keep each repo’s memory separate in your reasoning
- only merge them at the final explanation layer

Do not treat global memory as a substitute for repo memory. Global memory is the fallback layer, not the default layer.
