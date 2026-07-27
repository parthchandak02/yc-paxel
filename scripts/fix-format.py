#!/usr/bin/env python3
"""Add Paxel-required top-level `type` field to agent-transcript JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def fix_file(path: Path) -> tuple[bool, int]:
    out_lines: list[str] = []
    changed = False
    line_count = 0

    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue

        if isinstance(obj, dict):
            if "type" not in obj and "role" in obj:
                obj["type"] = obj["role"]
                changed = True
            msg = obj.get("message")
            if isinstance(msg, dict) and "role" not in msg and obj.get("type") in ("user", "assistant"):
                msg["role"] = obj["type"]
                changed = True

        out_lines.append(json.dumps(obj, ensure_ascii=False))
        line_count += 1

    if changed:
        path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""))

    return changed, line_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "staging_dir",
        nargs="?",
        default=str(Path.home() / ".paxel" / "staged-transcripts"),
        help="Directory containing staged *.jsonl files",
    )
    args = parser.parse_args()
    root = Path(args.staging_dir).expanduser()

    if not root.is_dir():
        print(f"Missing staging dir: {root}")
        return 1

    fixed = 0
    lines_total = 0
    for f in root.rglob("*.jsonl"):
        changed, n = fix_file(f)
        lines_total += n
        if changed:
            fixed += 1

    print(f"Fixed {fixed} files, {lines_total} lines in {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
