#!/usr/bin/env python3
"""Export Hermes ~/.hermes/state.db sessions to Paxel-compatible JSONL."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path


DEFAULT_DB = Path.home() / ".hermes" / "state.db"
DEFAULT_OUTPUT = Path.home() / ".paxel" / "hermes-state-transcripts"
SKIP_ROLES = {"session_meta"}


def fallback_path() -> Path:
    env = os.environ.get("HERMES_FALLBACK_PATH")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".hermes"


def encode_dir(path: str) -> str:
    return re.sub(r"[/.]", "-", path)


def sanitize_session_id(session_id: str) -> str:
    return re.sub(r"[^\w\-.]", "_", session_id)[:200]


def resolve_original_path(git_repo_root: str | None, cwd: str | None) -> str:
    for raw in (git_repo_root, cwd):
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.exists():
            return str(p.resolve())
    fb = fallback_path()
    if fb.exists():
        return str(fb.resolve())
    return str(Path.home())


def parse_tool_calls(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def assistant_content(text: str | None, tool_calls_raw: str | None) -> list[dict]:
    blocks: list[dict] = []
    if text and text.strip():
        blocks.append({"type": "text", "text": text})
    for tc in parse_tool_calls(tool_calls_raw):
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or "tool"
        args = fn.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {"raw": args}
        blocks.append({"type": "tool_use", "name": name, "input": args})
    return blocks


def message_to_entry(row: dict) -> dict | None:
    role = row["role"]
    if role in SKIP_ROLES:
        return None

    if role == "user":
        text = (row.get("content") or "").strip()
        if not text:
            return None
        return {
            "type": "user",
            "role": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        }

    if role == "assistant":
        blocks = assistant_content(row.get("content"), row.get("tool_calls"))
        if not blocks:
            return None
        return {
            "type": "assistant",
            "role": "assistant",
            "message": {"role": "assistant", "content": blocks},
        }

    if role == "tool":
        content = (row.get("content") or "").strip()
        if not content:
            return None
        block: dict = {"type": "tool_result", "content": content[:4000]}
        if row.get("tool_call_id"):
            block["tool_use_id"] = row["tool_call_id"]
        if row.get("tool_name"):
            block["tool_name"] = row["tool_name"]
        return {
            "type": "user",
            "role": "user",
            "message": {"role": "user", "content": [block]},
        }

    return None


def session_is_exportable(messages: list[dict]) -> bool:
    if len(messages) < 4:
        return False
    users = sum(
        1
        for m in messages
        if m.get("type") == "user"
        and any(b.get("type") == "text" for b in m.get("message", {}).get("content", []))
    )
    assistants = sum(1 for m in messages if m.get("type") == "assistant")
    return users >= 1 and assistants >= 1


def fetch_sessions(
    con: sqlite3.Connection,
    sources: set[str] | None,
    since_ts: float | None,
    limit: int | None,
) -> list[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    clauses = ["1=1"]
    params: list = []
    if sources:
        placeholders = ",".join("?" * len(sources))
        clauses.append(f"s.source IN ({placeholders})")
        params.extend(sorted(sources))
    if since_ts is not None:
        clauses.append("s.started_at >= ?")
        params.append(since_ts)
    sql = f"""
        SELECT s.id, s.source, s.started_at, s.title, s.cwd, s.git_repo_root
        FROM sessions s
        WHERE {' AND '.join(clauses)}
        ORDER BY s.started_at DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(con.execute(sql, params))


def export_session(con: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT role, content, tool_call_id, tool_calls, tool_name, timestamp
        FROM messages
        WHERE session_id = ? AND active = 1
        ORDER BY timestamp ASC, id ASC
        """,
        (session_id,),
    ).fetchall()
    entries = []
    for row in rows:
        entry = message_to_entry(dict(row))
        if entry:
            entries.append(entry)
    return entries


def write_index(proj_dir: Path, entries: list[dict]) -> None:
    """Merge entries into proj_dir/sessions-index.json instead of overwriting it.

    --all-agents stages Hermes, Perplexity and Cursor sessions into the same
    project dirs one after another; overwriting would drop the earlier sources.
    Entries whose .jsonl no longer exists are pruned.
    """
    path = proj_dir / "sessions-index.json"
    merged: dict[str, dict] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text()).get("entries", [])
        except (OSError, ValueError):
            old = []
        for e in old:
            sid = e.get("sessionId")
            if sid and (proj_dir / f"{sid}.jsonl").exists():
                merged[sid] = e
    for e in entries:
        merged[e["sessionId"]] = e
    path.write_text(json.dumps({"version": 1, "entries": list(merged.values())}, indent=2))


def export(
    db_path: Path,
    output: Path,
    sources: set[str] | None,
    since_days: int | None,
    limit: int | None,
    clean: bool,
) -> int:
    if not db_path.is_file():
        print(f"Hermes DB not found: {db_path}")
        return 1

    if clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    since_ts = None
    if since_days is not None:
        import time

        since_ts = time.time() - since_days * 86400

    con = sqlite3.connect(str(db_path))
    sessions = fetch_sessions(con, sources, since_ts, limit)

    by_repo: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    skipped_short = 0
    exported = 0

    for sess in sessions:
        sid = sess["id"]
        entries = export_session(con, sid)
        if not session_is_exportable(entries):
            skipped_short += 1
            continue

        original = resolve_original_path(sess["git_repo_root"], sess["cwd"])
        safe_id = sanitize_session_id(f"{sess['source']}_{sid}")
        by_repo[original].append((safe_id, sid, sess["source"]))

        proj_dir = output / encode_dir(original)
        proj_dir.mkdir(parents=True, exist_ok=True)
        out_file = proj_dir / f"{safe_id}.jsonl"
        out_file.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
            encoding="utf-8",
        )
        exported += 1

    for original, items in by_repo.items():
        proj_dir = output / encode_dir(original)
        write_index(proj_dir, [
            {"sessionId": safe_id, "originalPath": original, "agentSource": source}
            for safe_id, sid, source in items
        ])

    con.close()

    print(f"Exported {exported} Hermes sessions to {output}")
    print(f"Skipped {skipped_short} sessions (too short for Paxel)")
    print(f"Project dirs: {len(by_repo)}")
    return 0 if exported else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to Hermes state.db")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Staging output dir")
    parser.add_argument(
        "--sources",
        default="cli,subagent,whatsapp,cron,tui,telegram",
        help="Comma-separated session sources",
    )
    parser.add_argument("--all-sources", action="store_true", help="Export every source in DB")
    parser.add_argument("--since-days", type=int, help="Only sessions started in last N days")
    parser.add_argument("--limit", type=int, help="Max sessions to export (newest first)")
    parser.add_argument("--no-clean", action="store_true", help="Do not wipe output dir first")
    args = parser.parse_args()

    sources = None if args.all_sources else {s.strip() for s in args.sources.split(",") if s.strip()}
    return export(
        Path(args.db).expanduser(),
        Path(args.output).expanduser(),
        sources,
        args.since_days,
        args.limit,
        clean=not args.no_clean,
    )


if __name__ == "__main__":
    raise SystemExit(main())
