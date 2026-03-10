#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()


def windows_codex_home() -> Path | None:
    override = os.environ.get("WINDOWS_CODEX_HOME")
    if override:
        return Path(override).expanduser()
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile.startswith("/mnt/"):
        return Path(userprofile) / ".codex"
    if re.match(r"^[A-Za-z]:\\", userprofile):
        drive = userprofile[0].lower()
        rest = userprofile[2:].replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{drive}/{rest}") / ".codex"
    return None


HOME = Path.home()
CODEX_HOME = codex_home()
WINDOWS_CODEX_HOME = windows_codex_home()
CONFIG = CODEX_HOME / "config.toml"
WINDOWS_CONFIG = WINDOWS_CODEX_HOME / "config.toml" if WINDOWS_CODEX_HOME else None
STATE_DB = CODEX_HOME / "state_5.sqlite"
WINDOWS_STATE_DB = WINDOWS_CODEX_HOME / "state_5.sqlite" if WINDOWS_CODEX_HOME else None
REPORT_PATH = CODEX_HOME / "tmp" / "codex-wsl-maintenance-latest.json"
BACKUP_ROOT = Path(os.environ.get("CODEX_WSL_BACKUP_ROOT", str(HOME / "codex-backups")))


def backup_dir() -> Path:
    path = BACKUP_ROOT / f"maintenance-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[\n" + ",\n".join(f"  {render_value(item)}" for item in value) + ",\n]"
    raise TypeError(f"Unsupported TOML value: {type(value)!r}")


def render_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def emit(prefix: list[str], table: dict[str, Any]) -> None:
        scalar = [k for k, v in table.items() if not isinstance(v, dict)]
        child = [k for k, v in table.items() if isinstance(v, dict)]
        if prefix and scalar:
            lines.append(f"[{'.'.join(prefix)}]")
        for key in scalar:
            lines.append(f"{key} = {render_value(table[key])}")
        if scalar:
            lines.append("")
        for key in child:
            emit(prefix + [key], table[key])

    emit([], data)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def translate_value(value: Any) -> Any:
    if isinstance(value, str):
        if re.match(r"^[A-Za-z]:[\\/]", value):
            drive = value[0].lower()
            rest = value[2:].lstrip("/\\").replace("\\", "/")
            return f"/mnt/{drive}/{rest}"
        return value
    if isinstance(value, list):
        return [translate_value(item) for item in value]
    if isinstance(value, dict):
        return {k: translate_value(v) for k, v in value.items()}
    return value


def sqlite_backup(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s_con = sqlite3.connect(str(src))
    d_con = sqlite3.connect(str(dst))
    try:
        s_con.backup(d_con)
    finally:
        d_con.close()
        s_con.close()


def is_codex_running() -> bool:
    result = subprocess.run(["pgrep", "-af", "codex"], capture_output=True, text=True, check=False)
    self_pid = os.getpid()
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1] if len(parts) > 1 else ""
        if pid == self_pid or "codex_wsl_maintain.py" in cmd:
            continue
        return True
    return False


def sync_config(run_backup_dir: Path) -> dict[str, Any]:
    if not WINDOWS_CONFIG or not WINDOWS_CONFIG.exists() or not CONFIG.exists():
        return {"skipped": True, "reason": "missing_config"}
    windows_cfg = load_toml(WINDOWS_CONFIG)
    translated = translate_value(windows_cfg)
    translated.pop("windows", None)
    old_text = CONFIG.read_text(encoding="utf-8")
    new_text = render_toml(translated)
    if new_text == old_text:
        return {"changed": False}
    backup_path = run_backup_dir / "config.toml.before"
    shutil.copy2(CONFIG, backup_path)
    CONFIG.write_text(new_text, encoding="utf-8")
    return {"changed": True, "backup_path": str(backup_path)}


def prune_logs(db_path: Path, keep_null: int) -> dict[str, Any]:
    if not db_path or not db_path.exists():
        return {"skipped": True, "reason": "missing_db"}
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    total = cur.execute("select count(*) from logs").fetchone()[0]
    null_count = cur.execute("select count(*) from logs where thread_id is null").fetchone()[0]
    deletable = max(0, null_count - keep_null)
    if deletable:
        cutoff = cur.execute(
            "select id from logs where thread_id is null order by id desc limit 1 offset ?",
            (keep_null - 1,),
        ).fetchone()
        if cutoff:
            cur.execute("delete from logs where thread_id is null and id < ?", (cutoff[0],))
            con.commit()
    after_total = cur.execute("select count(*) from logs").fetchone()[0]
    after_null = cur.execute("select count(*) from logs where thread_id is null").fetchone()[0]
    con.close()
    return {
        "db_path": str(db_path),
        "before_total": total,
        "before_null": null_count,
        "after_total": after_total,
        "after_null": after_null,
        "deleted": total - after_total,
    }


def close_stale_turns(run_backup_dir: Path) -> dict[str, Any]:
    if not STATE_DB.exists():
        return {"skipped": True, "reason": "missing_db"}
    con = sqlite3.connect(str(STATE_DB))
    rows = con.execute("select id, rollout_path from threads where archived_at is null").fetchall()
    con.close()
    backup_root = run_backup_dir / "stale-turn-closeout"
    repaired = []
    for thread_id, rollout_path in rows:
        path = Path(rollout_path)
        if not path.exists():
            continue
        turn_state: dict[str, dict[str, bool]] = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload", {})
            turn_id = payload.get("turn_id")
            event_type = payload.get("type")
            if not turn_id:
                continue
            if event_type == "task_started":
                turn_state.setdefault(turn_id, {})["started"] = True
            if event_type in ("task_complete", "turn_aborted"):
                turn_state.setdefault(turn_id, {})["closed"] = True
        unresolved = [tid for tid, state in turn_state.items() if state.get("started") and not state.get("closed")]
        if not unresolved:
            continue
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / path.name
        shutil.copy2(path, backup_path)
        with path.open("a", encoding="utf-8") as fh:
            for turn_id in unresolved:
                event = {
                    "timestamp": now_utc(),
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": turn_id,
                        "last_agent_message": "[synthetic closeout: repaired stale unresolved turn during Codex WSL maintenance]",
                    },
                }
                fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        repaired.append({"thread_id": thread_id, "rollout_path": str(path), "backup_path": str(backup_path), "closed_turn_ids": unresolved})
    return {"changed_count": len(repaired), "backup_root": str(backup_root) if repaired else None, "sample": repaired[:10]}


def archive_fork_child_threads(run_backup_dir: Path) -> dict[str, Any]:
    if not STATE_DB.exists():
        return {"skipped": True, "reason": "missing_db"}
    con = sqlite3.connect(str(STATE_DB))
    rows = con.execute("select id, title, archived_at, rollout_path from threads").fetchall()
    threads = {row[0]: {"title": row[1], "archived_at": row[2], "rollout_path": row[3]} for row in rows}
    candidates = []
    for thread_id, meta in threads.items():
        path = Path(meta["rollout_path"])
        if not path.exists():
            continue
        forked_from_id = None
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[:3]:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "session_meta":
                continue
            payload = obj.get("payload", {})
            if payload.get("id") == thread_id:
                forked_from_id = payload.get("forked_from_id")
                break
        if not forked_from_id or forked_from_id not in threads:
            continue
        if meta["title"] != threads[forked_from_id]["title"]:
            continue
        candidates.append({"thread_id": thread_id, "parent_thread_id": forked_from_id, "archived_at": meta["archived_at"]})
    changed = []
    if candidates:
        backup_path = run_backup_dir / "state_5.sqlite.before.fork-child-archive"
        sqlite_backup(STATE_DB, backup_path)
        now_ts = int(time.time())
        for item in candidates:
            con.execute("update threads set archived = 1, archived_at = coalesce(archived_at, ?) where id = ?", (now_ts, item["thread_id"]))
            if item["archived_at"] is None:
                changed.append(item)
        con.commit()
    else:
        backup_path = None
    con.close()
    return {"candidate_count": len(candidates), "changed_count": len(changed), "backup_path": str(backup_path) if backup_path else None, "sample": changed[:20]}


def memories_enabled() -> bool:
    configs = [CONFIG]
    if WINDOWS_CONFIG:
        configs.append(WINDOWS_CONFIG)
    for path in configs:
        if not path.exists():
            continue
        cfg = load_toml(path)
        features = cfg.get("features", {})
        if isinstance(features, dict) and bool(features.get("memories", True)):
            return True
    return False


def cancel_memory_jobs_in_db(db_path: Path | None, scope: str) -> dict[str, Any]:
    if not db_path or not db_path.exists():
        return {"scope": scope, "skipped": True, "reason": "missing_db"}
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    now_ts = int(time.time())
    rows = cur.execute("select kind, job_key, status, worker_id from jobs where kind like 'memory_%' and status in ('running','queued','retrying')").fetchall()
    cancelled = []
    for kind, job_key, status, worker_id in rows:
        cur.execute(
            """
            update jobs
            set status = 'error',
                worker_id = null,
                ownership_token = null,
                finished_at = ?,
                lease_until = null,
                retry_at = null,
                last_error = ?
            where kind = ? and job_key = ?
            """,
            (now_ts, "cancelled locally because features.memories=false for Codex WSL stability", kind, job_key),
        )
        cancelled.append({"kind": kind, "job_key": job_key, "previous_status": status, "worker_id": worker_id})
    con.commit()
    con.close()
    return {"scope": scope, "db_path": str(db_path), "changed_count": len(cancelled), "sample": cancelled[:20]}


def audit_json() -> dict[str, Any]:
    if not STATE_DB.exists():
        return {"skipped": True, "reason": "missing_db"}
    con = sqlite3.connect(str(STATE_DB))
    paths = [Path(row[0]) for row in con.execute("select rollout_path from threads where archived_at is null").fetchall()]
    con.close()
    invalid_json = []
    for path in paths:
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            try:
                json.loads(line)
            except Exception:
                invalid_json.append({"path": str(path), "line": i})
                break
    return {"invalid_json_count": len(invalid_json), "invalid_json_files": invalid_json[:20]}


def vacuum_db(db_path: Path | None) -> dict[str, Any]:
    if not db_path or not db_path.exists():
        return {"skipped": True, "reason": "missing_db"}
    size_before = db_path.stat().st_size
    con = sqlite3.connect(str(db_path))
    con.execute("vacuum")
    con.close()
    return {"db_path": str(db_path), "size_before": size_before, "size_after": db_path.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description="Maintain Codex Desktop WSL local state.")
    parser.add_argument("--mode", choices=["safe", "background", "prelaunch", "force"], default="safe")
    args = parser.parse_args()

    run_backup_dir = backup_dir()
    codex_running = is_codex_running()
    allow_writes = args.mode == "force" or not codex_running

    if args.mode == "prelaunch" and codex_running:
        report = {"generated_at": now_utc(), "backup_dir": str(run_backup_dir), "codex_running": True, "mode": args.mode, "error": "codex_running"}
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 2

    report: dict[str, Any] = {"generated_at": now_utc(), "backup_dir": str(run_backup_dir), "codex_running": codex_running, "mode": args.mode}
    if allow_writes:
        report["config_sync"] = sync_config(run_backup_dir)
        report["stale_turn_closeout"] = close_stale_turns(run_backup_dir)
        report["fork_child_archive"] = archive_fork_child_threads(run_backup_dir)
    else:
        report["config_sync"] = {"skipped": True, "reason": "codex_running"}
        report["stale_turn_closeout"] = {"skipped": True, "reason": "codex_running"}
        report["fork_child_archive"] = {"skipped": True, "reason": "codex_running"}

    report["log_prune"] = {
        "wsl": prune_logs(STATE_DB, 20000),
        "windows": prune_logs(WINDOWS_STATE_DB, 10000),
    }
    if not memories_enabled():
        report["memory_job_cancel"] = {
            "wsl": cancel_memory_jobs_in_db(STATE_DB, "wsl"),
            "windows": cancel_memory_jobs_in_db(WINDOWS_STATE_DB, "windows"),
        }
    else:
        report["memory_job_cancel"] = {"skipped": True, "reason": "features.memories_enabled"}
    report["audit"] = audit_json()
    if args.mode in ("prelaunch", "force"):
        report["db_vacuum"] = {"wsl": vacuum_db(STATE_DB), "windows": vacuum_db(WINDOWS_STATE_DB)}
    else:
        report["db_vacuum"] = {"skipped": True, "reason": "requires_prelaunch_or_force"}

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
