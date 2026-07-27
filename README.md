# yc-paxel

Stage agent transcripts from **Cursor**, **Hermes**, and **Perplexity** into [YC Paxel](https://paxel.ycombinator.com)'s accepted format, then upload for coding-agent analysis reports.

Paxel does not natively read modern Cursor `agent-transcripts`, Hermes `state.db`, or Perplexity web threads. This repo provides small exporters and a single upload wrapper.

## What gets uploaded

Only **your local transcript data** when you run the scripts. This repository contains **no personal sessions, tokens, or path mappings** — only generic tooling.

## Prerequisites

- Python 3.10+
- Docker (or [Colima](https://github.com/abiosoft/colima) on macOS)
- Paxel auth token at `~/.paxel/token` (created on first upload)

Optional:

- **Hermes**: `~/.hermes/state.db`
- **Perplexity live export**: `perplexity-pro` CLI in `PATH`
- **Perplexity offline export**: JSON/Markdown files from Perplexity data export or browser extensions

## Quick start

```bash
git clone https://github.com/parthchandak02/yc-paxel.git
cd yc-paxel

# Cursor sessions only
bash scripts/paxel-upload.sh --cursor --all

# Hermes gateway sessions (WhatsApp, cron, CLI, subagents, etc.)
bash scripts/paxel-upload.sh --hermes-db --all

# Perplexity (requires perplexity-pro CLI)
bash scripts/paxel-upload.sh --perplexity-pro --all

# All three agents into one report
bash scripts/paxel-upload.sh --all-agents --all
```

Report URL appears in the upload log (`results/<id>/v3`).

## Supported agents

| Agent | Source | Script / flag |
|-------|--------|---------------|
| Cursor | `~/.cursor/projects/*/agent-transcripts/*.jsonl` | `--cursor` |
| Hermes | `~/.hermes/state.db` | `--hermes-db` |
| Perplexity | `perplexity-pro` CLI or exported JSON/MD | `--perplexity-pro` or `--perplexity-dir` |

Future agents can follow the same pattern: export → Paxel JSONL + `sessions-index.json` → `fix-format.py` → upload.

## Why staging is required

Paxel expects Claude Code-style layout:

```
$TRANSCRIPT_DIR/
  -Users-you-projects-myapp/
    sessions-index.json
    <session-id>.jsonl
```

Cursor uses `"role"` at the top level; Paxel requires `"type"`. `fix-format.py` adds the missing field.

**Important:** staging dir must live under `$HOME` (not `/tmp`) so Docker can bind-mount it.

## Configuration

### Cursor path mappings

If Cursor project folder names do not decode to real paths, copy the example:

```bash
mkdir -p ~/.config/yc-paxel
cp config/path-mappings.example.json ~/.config/yc-paxel/path-mappings.json
# edit cursor_project_folder_to_path entries
```

Or set `PAXEL_PATH_MAPPINGS=/path/to/path-mappings.json`.

### Hermes fallback path

If a session has no `cwd` or `git_repo_root`, exports fall back to `~/.hermes`. Override with:

```bash
export HERMES_FALLBACK_PATH=~/projects/my-hermes-repo
```

### Perplexity offline export

```bash
# Directory of JSON or Markdown exports
bash scripts/paxel-upload.sh --perplexity-dir ~/Downloads/perplexity-export --all
```

Supported JSON shapes include Perplexity GDPR export (`conversations[].messages[{query,answer}]`), role-based `messages[]`, and `turns[]` from bulk exporters.

## Script reference

| Script | Purpose |
|--------|---------|
| `scripts/paxel-upload.sh` | End-to-end: export/stage → fix → upload |
| `scripts/stage-transcripts.py` | Cursor → Paxel layout |
| `scripts/export-hermes-sessions.py` | Hermes state.db → Paxel JSONL |
| `scripts/export-perplexity-sessions.py` | Perplexity → Paxel JSONL |
| `scripts/fix-format.py` | `role` → `type` conversion |

### Paxel passthrough flags

```bash
bash scripts/paxel-upload.sh --cursor --since 2m --all
bash scripts/paxel-upload.sh --all-agents --no-repo --all
```

## Cursor skill install

Symlink into your Cursor skills directory:

```bash
ln -s "$(pwd)" ~/.cursor/skills/yc-paxel
```

Or clone directly:

```bash
git clone https://github.com/parthchandak02/yc-paxel.git ~/.cursor/skills/yc-paxel
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Docker daemon isn't running | `colima start` or start Docker Desktop |
| No sessions found | Run staging/export first; set `TRANSCRIPT_DIR` to staged dir |
| All sessions "too short" | Re-run `fix-format.py` on staging dir |
| Colima can't read staging dir | Use `~/.paxel/staged-transcripts`, not `/tmp` |
| Missing git metadata | Ensure `originalPath` in `sessions-index.json` points to a git repo |

## Security

Upload sends session narratives, file paths, and git metadata to Paxel for analysis. Source code stays local. Use `--since`, `--no-repo`, or per-agent flags to limit scope.

Never commit `~/.paxel/token`, staged transcripts, or personal `path-mappings.json`.

## License

MIT
