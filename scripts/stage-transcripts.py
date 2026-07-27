#!/usr/bin/env python3
"""Stage Cursor agent-transcripts into Paxel's Claude-style layout."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path


def mappings_file() -> Path | None:
    env = os.environ.get("PAXEL_PATH_MAPPINGS")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    for candidate in (
        Path.home() / ".config" / "yc-paxel" / "path-mappings.json",
        Path(__file__).resolve().parent.parent / "config" / "path-mappings.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def load_special_mappings(home: Path) -> dict[str, str]:
    special: dict[str, str] = {}
    path = mappings_file()
    if not path:
        return special
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read path mappings from {path}: {exc}")
        return special
    raw = data.get("cursor_project_folder_to_path", {})
    if not isinstance(raw, dict):
        return special
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            special[key] = str(Path(value).expanduser().resolve())
    return special


def encode_dir(path: str) -> str:
    return re.sub(r"[/.]", "-", path)


def decode_cursor_project(name: str, home: Path, special: dict[str, str]) -> str | None:
    if name in special:
        return special[name]
    if re.fullmatch(r"\d+", name) or name in {"tmp", "mcps", "private-tmp", "Volumes"}:
        return None

    user = os.environ.get("USER") or home.name

    if name.startswith("Users-"):
        rest = name[6:]
        projects_prefix = f"{user}-projects-"
        if rest.startswith(projects_prefix):
            slug = rest[len(projects_prefix) :]
            cand = home / "projects" / slug
            if cand.exists():
                return str(cand.resolve())
        path = "/Users/" + rest.replace("-", "/")
        return str(Path(path).resolve()) if Path(path).exists() else None

    if name.startswith("Volumes-"):
        path = "/Volumes/" + name[8:].replace("-", "/")
        return str(Path(path).resolve()) if Path(path).exists() else None

    return None


def infer_paths(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.findall(r"(?:/Users/[^\s\"\\`]+|/Volumes/[^\s\"\\`]+)", text):
        raw = raw.rstrip(".,;:)]`")
        if any(x in raw for x in ("/agent-transcripts/", "/.cursor/projects/", "/node_modules/")):
            continue
        out.append(raw)
    return out


def git_root(path: str) -> str:
    p = Path(path)
    if p.is_file():
        p = p.parent
    try:
        if p.exists():
            p = p.resolve()
    except OSError:
        pass
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return str(parent.resolve())
    if p.exists() and p.is_dir():
        return str(p.resolve())
    return str(p)


def git_remote(root: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", root, "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def matches_filter(root: str, cursor_project: str, pattern: re.Pattern[str] | None) -> bool:
    if not pattern:
        return True
    haystack = f"{root} {cursor_project}".lower()
    return bool(pattern.search(haystack))


def stage(output: Path, home: Path, filter_re: re.Pattern[str] | None, clean: bool) -> int:
    special = load_special_mappings(home)
    if clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    by_repo: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    cp = home / ".cursor" / "projects"
    if not cp.is_dir():
        print(f"No Cursor projects dir: {cp}")
        return 1

    for proj in cp.iterdir():
        at = proj / "agent-transcripts"
        if not at.is_dir():
            continue
        cwd = decode_cursor_project(proj.name, home, special)
        for f in at.rglob("*.jsonl"):
            if "subagents" in f.parts:
                continue
            resolved_cwd = cwd
            if not resolved_cwd:
                try:
                    chunk = f.read_text(errors="ignore")[:25000]
                    paths = infer_paths(chunk)
                    resolved_cwd = next(
                        (p for p in sorted(set(paths), key=len) if Path(p).exists()),
                        None,
                    )
                except OSError:
                    resolved_cwd = None
            if not resolved_cwd:
                resolved_cwd = str(home)
            root = git_root(resolved_cwd)
            if not matches_filter(root, proj.name, filter_re):
                continue
            by_repo[root].append((f.stem, f))

    total = 0
    for root, sessions in sorted(by_repo.items(), key=lambda x: -len(x[1])):
        if not sessions:
            continue
        proj_dir = output / encode_dir(root)
        proj_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for sid, src in sessions:
            dest = proj_dir / f"{sid}.jsonl"
            if not dest.exists():
                shutil.copy2(src, dest)
            entries.append({"sessionId": sid, "originalPath": root})
            total += 1
        (proj_dir / "sessions-index.json").write_text(
            json.dumps({"version": 1, "entries": entries}, indent=2)
        )
        remote = git_remote(root) if Path(root).exists() else ""
        print(f"  {len(sessions):4d}  {Path(root).name}  {remote or '(no git remote)'}")

    print(f"\nStaged {total} sessions across {len(by_repo)} project dirs at {output}")
    return 0 if total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(Path.home() / ".paxel" / "staged-transcripts"),
        help="Staging directory (default: ~/.paxel/staged-transcripts)",
    )
    parser.add_argument(
        "--filter",
        help="Regex on repo path or Cursor project folder",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete existing staging dir before writing",
    )
    args = parser.parse_args()

    filter_re = re.compile(args.filter, re.I) if args.filter else None
    return stage(Path(args.output).expanduser(), Path.home(), filter_re, clean=not args.no_clean)


if __name__ == "__main__":
    raise SystemExit(main())
