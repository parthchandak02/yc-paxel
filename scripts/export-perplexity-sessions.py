#!/usr/bin/env python3
"""Export Perplexity threads to Paxel-compatible JSONL.

Supports:
  - JSON files from Perplexity data export, browser extensions, or bulk exporters
  - Optional live export via `perplexity-pro` CLI (if installed and authenticated)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path


DEFAULT_OUTPUT = Path.home() / ".paxel" / "perplexity-transcripts"
DEFAULT_PROJECT = str(Path.home() / "perplexity-research")


def encode_dir(path: str) -> str:
    return re.sub(r"[/.]", "-", path)


def sanitize_session_id(session_id: str) -> str:
    return re.sub(r"[^\w\-.]", "_", session_id)[:200]


def paxel_user(text: str) -> dict:
    return {
        "type": "user",
        "role": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def paxel_assistant(text: str, sources: list[dict] | None = None) -> dict:
    content: list[dict] = [{"type": "text", "text": text}]
    if sources:
        refs = []
        for src in sources[:12]:
            title = src.get("title") or src.get("name") or "source"
            url = src.get("url") or src.get("link")
            snippet = src.get("snippet") or src.get("text")
            line = f"- {title}"
            if url:
                line += f" ({url})"
            if snippet:
                line += f": {snippet[:200]}"
            refs.append(line)
        if refs:
            content.append(
                {
                    "type": "text",
                    "text": "Sources:\n" + "\n".join(refs),
                }
            )
    return {
        "type": "assistant",
        "role": "assistant",
        "message": {"role": "assistant", "content": content},
    }


def session_is_exportable(messages: list[dict]) -> bool:
    if len(messages) < 4:
        return False
    users = sum(1 for m in messages if m.get("type") == "user")
    assistants = sum(1 for m in messages if m.get("type") == "assistant")
    return users >= 1 and assistants >= 1


def turns_from_query_answer(messages: list) -> list[dict]:
    out: list[dict] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        query = (item.get("query") or item.get("question") or "").strip()
        answer = (item.get("answer") or item.get("response") or "").strip()
        citations = item.get("citations") or item.get("sources") or []
        if query:
            out.append(paxel_user(query))
        if answer:
            out.append(paxel_assistant(answer, citations if isinstance(citations, list) else None))
    return out


def turns_from_role_messages(messages: list) -> list[dict]:
    out: list[dict] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "").lower()
        text = (
            item.get("text")
            or item.get("content")
            or item.get("message")
            or ""
        )
        if isinstance(text, list):
            text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in text
            )
        text = str(text).strip()
        if not text:
            continue
        if role == "user":
            out.append(paxel_user(text))
        elif role in {"assistant", "bot", "model"}:
            sources = item.get("sources") or item.get("citations")
            out.append(
                paxel_assistant(text, sources if isinstance(sources, list) else None)
            )
    return out


def turns_from_entries(entries: list) -> list[dict]:
    out: list[dict] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or item.get("author") or "").lower()
        text = item.get("content") or item.get("text") or item.get("message") or ""
        if isinstance(text, dict):
            text = text.get("text") or json.dumps(text)
        text = str(text).strip()
        if not text:
            continue
        if role in {"user", "human"}:
            out.append(paxel_user(text))
        elif role in {"assistant", "bot", "model", "ai"}:
            out.append(paxel_assistant(text, item.get("sources")))
    return out


def conversation_to_entries(conv: dict) -> tuple[str, list[dict]]:
    title = (
        conv.get("title")
        or conv.get("name")
        or conv.get("thread_title")
        or conv.get("metadata", {}).get("thread_title")
        or "perplexity-thread"
    )
    cid = (
        conv.get("id")
        or conv.get("uuid")
        or conv.get("contextUUID")
        or conv.get("metadata", {}).get("thread_id")
        or sanitize_session_id(str(title))
    )

    messages: list[dict] = []
    if isinstance(conv.get("turns"), list):
        messages = turns_from_role_messages(conv["turns"])
    elif isinstance(conv.get("messages"), list):
        sample = conv["messages"][0] if conv["messages"] else {}
        if isinstance(sample, dict) and ("query" in sample or "answer" in sample):
            messages = turns_from_query_answer(conv["messages"])
        else:
            messages = turns_from_role_messages(conv["messages"])
    elif isinstance(conv.get("entries"), list):
        messages = turns_from_entries(conv["entries"])
    elif isinstance(conv.get("conversations"), list):
        for nested in conv["conversations"]:
            _, nested_msgs = conversation_to_entries(nested)
            messages.extend(nested_msgs)

    return str(cid), messages


def load_conversations(path: Path) -> list[tuple[str, list[dict]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Skip {path}: {exc}")
        return []

    conversations: list[dict] = []
    if isinstance(data, dict):
        if isinstance(data.get("conversations"), list):
            conversations = data["conversations"]
        elif isinstance(data.get("messages"), list) or isinstance(data.get("turns"), list):
            conversations = [data]
        elif isinstance(data.get("entries"), list):
            conversations = [data]
    elif isinstance(data, list):
        conversations = [c for c in data if isinstance(c, dict)]

    out: list[tuple[str, list[dict]]] = []
    for conv in conversations:
        cid, messages = conversation_to_entries(conv)
        if messages:
            out.append((cid, messages))
    return out


def markdown_to_entries(text: str) -> list[dict]:
    """Best-effort parse for perplexity-pro markdown exports."""
    blocks = re.split(r"\n(?=#{1,3}\s)", text)
    out: list[dict] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block.lower().startswith("## user") or block.lower().startswith("### user"):
            body = re.sub(r"^#+\s*user\s*\n?", "", block, flags=re.I).strip()
            if body:
                out.append(paxel_user(body))
        elif block.lower().startswith("## assistant") or block.lower().startswith("### assistant"):
            body = re.sub(r"^#+\s*assistant\s*\n?", "", block, flags=re.I).strip()
            if body:
                out.append(paxel_assistant(body))
        elif not out:
            out.append(paxel_user(block))
        elif out and out[-1]["type"] == "user":
            out.append(paxel_assistant(block))
    return out


def find_perplexity_pro() -> str | None:
    from shutil import which

    return which("perplexity-pro")


def export_via_perplexity_pro(output: Path, library: bool, space: str | None) -> list[Path]:
    cli = find_perplexity_pro()
    if not cli:
        print("perplexity-pro not found in PATH; skipping live export")
        return []

    list_cmd = [cli, "threads", "list", "--json"]
    if library:
        list_cmd.append("--library")
    elif space:
        list_cmd.extend(["--space", space])
    else:
        list_cmd.append("--library")

    try:
        raw = subprocess.check_output(list_cmd, stderr=subprocess.PIPE, text=True)
        threads = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"Could not list Perplexity threads: {exc}")
        return []

    if not isinstance(threads, list):
        print("Unexpected Perplexity thread list format")
        return []

    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="paxel-pplx-") as tmp:
        tmp_path = Path(tmp)
        for thread in threads:
            uuid = (
                thread.get("uuid")
                or thread.get("contextUUID")
                or thread.get("id")
            )
            if not uuid:
                continue
            out_json = tmp_path / f"{sanitize_session_id(uuid)}.json"
            try:
                subprocess.run(
                    [cli, "export", uuid, "-f", "json", "-o", str(out_json)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError:
                out_md = tmp_path / f"{sanitize_session_id(uuid)}.md"
                try:
                    subprocess.run(
                        [cli, "export", uuid, "-f", "md", "-o", str(out_md)],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    written.append(out_md)
                except subprocess.CalledProcessError:
                    continue
            else:
                written.append(out_json)

        if written:
            export_from_input_dir(tmp_path, output, clean=False, project_path=DEFAULT_PROJECT)
    return written


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


def export_from_input_dir(
    input_dir: Path,
    output: Path,
    clean: bool,
    project_path: str,
) -> int:
    if clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    by_repo: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    skipped = 0
    exported = 0

    files = sorted(input_dir.rglob("*"))
    for path in files:
        if not path.is_file():
            continue
        conversations: list[tuple[str, list[dict]]] = []
        if path.suffix.lower() == ".json":
            conversations = load_conversations(path)
        elif path.suffix.lower() in {".md", ".markdown"}:
            try:
                messages = markdown_to_entries(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            if messages:
                conversations = [(path.stem, messages)]
        else:
            continue

        for cid, messages in conversations:
            if not session_is_exportable(messages):
                skipped += 1
                continue
            safe_id = sanitize_session_id(f"perplexity_{cid}")
            by_repo[project_path].append((safe_id, messages))
            proj_dir = output / encode_dir(project_path)
            proj_dir.mkdir(parents=True, exist_ok=True)
            (proj_dir / f"{safe_id}.jsonl").write_text(
                "\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n",
                encoding="utf-8",
            )
            exported += 1

    for original, items in by_repo.items():
        proj_dir = output / encode_dir(original)
        write_index(proj_dir, [
            {"sessionId": safe_id, "originalPath": original, "agentSource": "perplexity"}
            for safe_id, _ in items
        ])

    print(f"Exported {exported} Perplexity sessions to {output}")
    print(f"Skipped {skipped} threads (too short for Paxel)")
    return 0 if exported else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Staging output dir")
    parser.add_argument(
        "--input-dir",
        help="Directory of exported Perplexity JSON/Markdown files",
    )
    parser.add_argument(
        "--project-path",
        default=DEFAULT_PROJECT,
        help="originalPath used in sessions-index.json (default: ~/perplexity-research)",
    )
    parser.add_argument(
        "--perplexity-pro",
        action="store_true",
        help="Live export via perplexity-pro CLI (library threads)",
    )
    parser.add_argument("--space", help="Perplexity space slug (with --perplexity-pro)")
    parser.add_argument("--no-clean", action="store_true", help="Do not wipe output dir first")
    args = parser.parse_args()

    output = Path(args.output).expanduser()
    clean = not args.no_clean

    if args.perplexity_pro:
        export_via_perplexity_pro(output, library=not args.space, space=args.space)
        return 0

    if not args.input_dir:
        print("Provide --input-dir or --perplexity-pro")
        return 1

    return export_from_input_dir(
        Path(args.input_dir).expanduser(),
        output,
        clean=clean,
        project_path=str(Path(args.project_path).expanduser()),
    )


if __name__ == "__main__":
    raise SystemExit(main())
