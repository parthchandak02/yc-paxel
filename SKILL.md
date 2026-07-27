---
name: yc-paxel
description: Stages Cursor, Hermes, and Perplexity agent transcripts into YC Paxel's accepted format, runs upload, and retrieves coding-agent analysis reports. Use when the user mentions Paxel, YC Paxel, coding agent reports, uploading agent sessions, transcript conversion, or wants a per-repo or comprehensive session report across multiple AI agents.
---

# YC Paxel — Multi-Agent Transcript Pipeline

Convert agent transcripts from **Cursor**, **Hermes**, and **Perplexity** into Paxel-compatible layout, upload, and generate reports.

Repo: https://github.com/parthchandak02/yc-paxel

## Background

Paxel **does not natively read**:

- Cursor `~/.cursor/projects/*/agent-transcripts/`
- Hermes `~/.hermes/state.db`
- Perplexity web threads

It expects Claude Code layout with `sessions-index.json` per project and JSONL lines using top-level `"type": "user"|"assistant"`.

**Auth:** Token at `~/.paxel/token` (created on first upload).

**Docker:** Required for Paxel upload. Colima works on macOS.

## Quick start

```bash
bash scripts/paxel-upload.sh --cursor --all
bash scripts/paxel-upload.sh --hermes-db --all
bash scripts/paxel-upload.sh --perplexity-pro --all
bash scripts/paxel-upload.sh --all-agents --all
```

## Agent workflow

When user asks for a Paxel report:

1. **Preflight**
   - `docker info` or `colima start`
   - `test -f ~/.paxel/token`

2. **Export / stage** (pick agents)
   - Cursor: `scripts/stage-transcripts.py`
   - Hermes: `scripts/export-hermes-sessions.py` or `--hermes-db`
   - Perplexity: `scripts/export-perplexity-sessions.py` or `--perplexity-pro`

3. **Fix format** — `scripts/fix-format.py` (role → type)

4. **Upload**
   - `export TRANSCRIPT_DIR=<staging-dir>` (must be under `$HOME`)
   - `curl -fsSL https://paxel.ycombinator.com/upload.sh | bash -s -- --all`

5. **Report URL** — grep log for `results/` or `Upload:`

## Configuration

- **Path mappings:** `~/.config/yc-paxel/path-mappings.json` (see `config/path-mappings.example.json`)
- **Hermes fallback:** `HERMES_FALLBACK_PATH` env var
- **Staging dir:** `PAXEL_STAGE_DIR` or `--stage-dir` (default `~/.paxel/staged-transcripts`)

## Format fix (required for Cursor)

**Before:** `{"role":"user",...}`  
**After:** `{"role":"user","type":"user","message":{"role":"user",...}}`

Without `"type"`, Paxel classifies sessions as too short.

## Script reference

| Script | Purpose |
|--------|---------|
| `scripts/paxel-upload.sh` | End-to-end wrapper |
| `scripts/stage-transcripts.py` | Cursor → Paxel |
| `scripts/export-hermes-sessions.py` | Hermes state.db → Paxel |
| `scripts/export-perplexity-sessions.py` | Perplexity → Paxel |
| `scripts/fix-format.py` | role → type |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Docker not running | `colima start` |
| No sessions found | Stage/export first; check `TRANSCRIPT_DIR` |
| All sessions too short | Run `fix-format.py` |
| Staging in /tmp fails | Use `~/.paxel/staged-transcripts` |

## Security

Upload sends session content and paths to Paxel. Never commit tokens or staged transcripts. Use `--since`, `--no-repo`, or single-agent flags to limit scope.
