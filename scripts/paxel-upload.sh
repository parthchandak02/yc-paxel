#!/usr/bin/env bash
# Stage agent transcripts (Cursor, Hermes, Perplexity), fix format, run YC Paxel upload.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="${PAXEL_STAGE_DIR:-$HOME/.paxel/staged-transcripts}"

CURSOR=0
HERMES_DB=0
PERPLEXITY=0
ALL_AGENTS=0
HERMES_DB_SOURCES=""
PERPLEXITY_INPUT_DIR=""
PERPLEXITY_PRO=0
PAXEL_ARGS=()
CLEAN=1
FILTER=""

usage() {
  cat <<'EOF'
Usage: paxel-upload.sh [options] [-- paxel-args...]

Agent sources (pick one or more):
  --cursor           Stage Cursor agent-transcripts (default if no agent flag given)
  --hermes-db        Export Hermes ~/.hermes/state.db sessions
  --perplexity       Export Perplexity threads
  --perplexity-pro   Use perplexity-pro CLI for live Perplexity export
  --all-agents       Cursor + Hermes DB + Perplexity (perplexity-pro if available)

Options:
  --all              Pass --all to Paxel (analyze every staged project)
  --filter REGEX     Stage Cursor sessions matching repo/project regex
  --stage-dir DIR    Staging output (default: ~/.paxel/staged-transcripts)
  --hermes-db-sources LIST  Comma-separated Hermes sources
  --perplexity-dir DIR      Directory of exported Perplexity JSON/Markdown files
  --no-clean         Keep existing staged files (merge incremental exports)
  --since DURATION   Pass --since to Paxel (e.g. 2m, 30d)
  --no-repo          Pass --no-repo to Paxel
  -h, --help         Show this help

Examples:
  paxel-upload.sh --cursor --all
  paxel-upload.sh --hermes-db --all
  paxel-upload.sh --perplexity-pro --all
  paxel-upload.sh --all-agents --all
  paxel-upload.sh --perplexity-dir ~/Downloads/perplexity-export --all
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cursor) CURSOR=1 ;;
    --hermes-db) HERMES_DB=1 ;;
    --perplexity) PERPLEXITY=1 ;;
    --perplexity-pro) PERPLEXITY=1; PERPLEXITY_PRO=1 ;;
    --all-agents) ALL_AGENTS=1 ;;
    --all) PAXEL_ARGS+=("--all") ;;
    --filter) FILTER="$2"; shift ;;
    --stage-dir) STAGE="$2"; shift ;;
    --hermes-db-sources) HERMES_DB_SOURCES="$2"; shift ;;
    --perplexity-dir) PERPLEXITY_INPUT_DIR="$2"; shift ;;
    --no-clean) CLEAN=0 ;;
    --since) PAXEL_ARGS+=("--since" "$2"); shift ;;
    --no-repo) PAXEL_ARGS+=("--no-repo") ;;
    -h|--help) usage; exit 0 ;;
    --) shift; PAXEL_ARGS+=("$@"); break ;;
    *) PAXEL_ARGS+=("$1") ;;
  esac
  shift
done

if [[ $ALL_AGENTS -eq 1 ]]; then
  CURSOR=1
  HERMES_DB=1
  PERPLEXITY=1
  PERPLEXITY_PRO=1
fi

if [[ $CURSOR -eq 0 && $HERMES_DB -eq 0 && $PERPLEXITY -eq 0 ]]; then
  CURSOR=1
fi

mkdir -p "$HOME/.paxel/logs"
LOG="$HOME/.paxel/logs/paxel-$(date +%Y%m%d-%H%M%S).log"

echo "==> Checking Docker..."
if ! docker info >/dev/null 2>&1; then
  if command -v colima >/dev/null 2>&1; then
    echo "Docker daemon not running. Starting Colima..."
    colima start
  else
    echo "ERROR: Docker is required for Paxel upload. Install Docker or Colima." >&2
    exit 1
  fi
fi

if [[ $HERMES_DB -eq 1 ]]; then
  DB_ARGS=(--output "$STAGE")
  [[ $CLEAN -eq 0 ]] && DB_ARGS+=(--no-clean)
  [[ -n "$HERMES_DB_SOURCES" ]] && DB_ARGS+=(--sources "$HERMES_DB_SOURCES")
  echo "==> Exporting Hermes state.db -> $STAGE"
  python3 "$SCRIPT_DIR/export-hermes-sessions.py" "${DB_ARGS[@]}"
  CLEAN=0
fi

if [[ $PERPLEXITY -eq 1 ]]; then
  PPLX_ARGS=(--output "$STAGE")
  [[ $CLEAN -eq 0 ]] && PPLX_ARGS+=(--no-clean)
  if [[ $PERPLEXITY_PRO -eq 1 ]]; then
    PPLX_ARGS+=(--perplexity-pro)
  elif [[ -n "$PERPLEXITY_INPUT_DIR" ]]; then
    PPLX_ARGS+=(--input-dir "$PERPLEXITY_INPUT_DIR")
  else
    echo "ERROR: --perplexity requires --perplexity-pro or --perplexity-dir" >&2
    exit 1
  fi
  echo "==> Exporting Perplexity threads -> $STAGE"
  python3 "$SCRIPT_DIR/export-perplexity-sessions.py" "${PPLX_ARGS[@]}"
  CLEAN=0
fi

if [[ $CURSOR -eq 1 ]]; then
  STAGE_ARGS=(--output "$STAGE")
  [[ $CLEAN -eq 0 ]] && STAGE_ARGS+=(--no-clean)
  [[ -n "$FILTER" ]] && STAGE_ARGS+=(--filter "$FILTER")
  echo "==> Staging Cursor agent-transcripts -> $STAGE"
  python3 "$SCRIPT_DIR/stage-transcripts.py" "${STAGE_ARGS[@]}"
fi

echo "==> Fixing JSONL format for Paxel (role -> type)"
python3 "$SCRIPT_DIR/fix-format.py" "$STAGE"

case "$STAGE" in
  /tmp/*) echo "ERROR: STAGE must be under \$HOME for Docker bind mounts (not /tmp): $STAGE" >&2; exit 1 ;;
esac

export TRANSCRIPT_DIR="$STAGE"
echo "==> Running Paxel upload (log: $LOG)"
echo "    TRANSCRIPT_DIR=$TRANSCRIPT_DIR"
curl -fsSL https://paxel.ycombinator.com/upload.sh | bash -s -- "${PAXEL_ARGS[@]}" 2>&1 | tee "$LOG"

echo ""
echo "Done. Log: $LOG"
grep -E 'Upload:|results/|Report|email when' "$LOG" | tail -5 || true
