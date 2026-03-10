---
name: codex-wsl-maintenance
description: Use when diagnosing or maintaining Codex Desktop on WSL, especially startup CPU spikes, stale thread state, path drift, malformed rollout state, or when the user wants a clean Codex cold-start flow.
---

# Codex WSL Maintenance

Use this skill for Codex Desktop local-state and WSL runtime maintenance.

## Scope

This public version focuses on the reusable parts:

- Windows/WSL `config.toml` parity
- stale memory-job cancellation when app memories are intentionally disabled
- stale unresolved turn closeout
- same-title fork-child thread archiving
- SQLite log pruning
- optional DB vacuum during closed-app maintenance
- rollout JSON audit

It avoids machine-specific assumptions by deriving paths from:

- `$CODEX_HOME`
- `$USERPROFILE` inside WSL
- explicit environment overrides when needed

## Fast path

Closed-app maintenance:

```bash
python3 scripts/codex_wsl_maintain.py --mode prelaunch
```

Live-session safe maintenance:

```bash
python3 scripts/codex_wsl_maintain.py --mode safe
```

Forced maintenance after your launcher has already stopped Codex:

```bash
python3 scripts/codex_wsl_maintain.py --mode force
```

## Public launcher pattern

A Windows launcher template is included at:

```text
assets/codex-wsl-open-clean.cmd.template
```

It is intentionally a template, not a hardcoded machine copy. Fill in:

- WSL distro name
- Codex AppID

Use it as a cold-start entrypoint only, not as an in-session repair tool.

## Operating rules

- Do not rely on the window close button to fully clear backend state.
- For a deterministic cold start, stop Windows `Codex.exe`, stop WSL `codex`, run maintenance, then launch Codex.
- Keep app-managed memories disabled if they have already caused stale thread/job churn; this skill supports that operating mode.

## Evidence

The maintenance script writes a JSON report under:

```text
$CODEX_HOME/tmp/codex-wsl-maintenance-latest.json
```
