# Codex WSL Skills

Public Codex skills for anyone running Codex heavily on WSL across multiple repos and worktrees.

This repo includes:

- `repo-memory-retrieval`: repo-scoped memory lookup plus scope-first task routing so project context stays bounded instead of spilling across unrelated threads
- `codex-wsl-maintenance`: Codex Desktop WSL maintenance and recovery tooling for local-state churn, stale thread state, path drift, and startup overhead
- optional clean-launch template for affected WSL setups

This is a practical workaround toolkit, not a claim that the underlying Codex app issues are fully solved.

## Included skills

### `repo-memory-retrieval`

Use when you want file-based, repo-scoped memory instead of app-managed memory jobs.

What it does:

- resolves the current repo or worktree back to the right memory namespace
- suggests a narrower working directory for bounded tasks before heavy Codex runs
- generates compact `memory_capsule.md` files and prefers capsule-scale retrieval first
- prefers structured memory indexes over broad `MEMORY.md` scans
- keeps normal compact retrieval compaction-aware by default for empty or short queries
- falls back to global memory only when explicitly requested

What we learned using it:

- for bounded tasks, narrower `cwd` selection is the biggest direct Codex performance win
- the supervisor path is useful for continuity-heavy work, but not the default answer for simple subsystem tasks
- repo-memory retrieval should cooperate with Codex auto-compaction, not fight it

### `codex-wsl-maintenance`

Use when Codex Desktop on WSL starts showing local-state or runtime churn.

What it covers:

- Windows/WSL `config.toml` parity
- stale unresolved turn closeout
- same-title fork-child thread archiving
- SQLite log pruning
- rollout JSON audit
- optional vacuum during closed-app maintenance

## Recommended automation

The safest thing to automate is a recurring `codex-wsl-maintenance` audit, not forceful repair.

Recommended cadence:

- every 6 hours
- on a dedicated maintenance worktree/thread
- `safe` audit behavior only

Suggested automation prompt:

```text
Use $codex-wsl-maintenance to read the latest maintenance status first, then run a safe Codex WSL maintenance audit. Summarize any local-state drift, stale thread findings, launcher issues, startup-risk signals, or other actionable Codex WSL health problems. If there are no actionable findings, say so briefly and do not suggest forceful repair unless clearly needed.
```

Keep repo-memory retrieval on-demand. It works best when invoked at the time of need, not as a background context-loading automation.

## Current guidance

- for bounded backend/frontend/component tasks, narrow the working scope before running heavy Codex work
- for normal recall, use compact repo-memory retrieval and let it stay small
- for long-lived threads under context pressure, rely on the compaction-aware compact path instead of broad memory dumps
- use the supervisor pattern only when continuity matters more than raw token efficiency

## Install

Clone or copy the skill folders into your Codex skills directory:

```bash
mkdir -p "$CODEX_HOME/skills"
cp -R repo-memory-retrieval "$CODEX_HOME/skills/"
cp -R codex-wsl-maintenance "$CODEX_HOME/skills/"
```

If `CODEX_HOME` is not set, Codex usually uses `~/.codex`.

## Launcher template

`codex-wsl-maintenance/assets/codex-wsl-open-clean.cmd.template` is optional.

It is a template, not a drop-in launcher. You must fill in:

- your WSL distro name
- your Codex AppID

Use it only as a cold-start entrypoint, not as an in-session repair tool.

## Safety

Before using `codex-wsl-maintenance`:

- back up your local Codex state under `$CODEX_HOME`
- start with `safe` or `prelaunch` mode before using `force`
- review launcher template values before using them on your machine
- understand that the maintenance script edits local Codex state files; it is not just a read-only diagnostic

This repo is an unofficial workaround toolkit for affected setups, not official OpenAI software.

## Release hygiene

This repo includes a sanitization checker:

```bash
python3 scripts/check_sanitization.py --history main
```

What it does:

- scans tracked files for unsanitized local paths / machine identifiers
- scans reachable history for the same patterns
- fails fast before a public push if the repo still contains private local strings

Repo-local enforcement:

- `.githooks/pre-push` runs the checker automatically
- set it once locally with:

```bash
git config core.hooksPath .githooks
```

Machine-specific patterns should go in a local, ignored file:

- `.sanitization.local.txt`

That lets you block your own usernames, private repo names, and local path fragments without pushing those patterns into the public repo.

## Notes

- These skills are sanitized for public sharing and remove machine-specific paths and private repo identifiers.
- The maintenance script is designed around local Codex state under `$CODEX_HOME`.
- If app-managed memories have already caused stale thread/job churn on your setup, keep them disabled and prefer `repo-memory-retrieval`.
- `repo-memory-retrieval` now supports capsule-first retrieval, scope-first task routing, and compaction-aware compact output so memory stays useful even as repo memory grows.
