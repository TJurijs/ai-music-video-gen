#!/usr/bin/env bash
# ============================================================
# Music Video Studio — macOS / Linux startup
# ============================================================
# Run this from the repo root: `./start.sh`
# Equivalent of start.bat on Windows. Opens TWO terminals (one
# for backend, one for frontend) on macOS via osascript. On
# Linux, falls back to backgrounding both with output combined.
#
# First run will install deps. Make sure you have:
#   - Python 3.11+ (check: python3 --version)
#   - Node 18+      (check: node --version)
#   - ffmpeg        (check: ffmpeg -version)
#   - A .env at repo root or backend/.env with OPENROUTER_API_KEY
# ============================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"

# ── Preflight ────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || { echo "❌ python3 not found. Install Python 3.11+ (macOS: brew install python@3.11)." >&2; exit 1; }
command -v node >/dev/null 2>&1    || { echo "❌ node not found. Install Node 18+ (macOS: brew install node)." >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1  || { echo "⚠️  ffmpeg not found — assembly + last-frame extraction won't work. On macOS: brew install ffmpeg" >&2; }

# Hard requirement: Python 3.10+ for PEP 604 `dict | None` union syntax used
# throughout the backend. macOS often ships Python 3.9 as the default —
# refuse early with an actionable message instead of letting the user hit
# cryptic SyntaxError at import time.
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
  echo "❌ Found Python $PY_VER, but the backend requires Python 3.10+ (uses PEP 604 union syntax)." >&2
  echo "   macOS: brew install python@3.11 && which python3.11" >&2
  echo "   Then re-run with: python3.11 -m venv backend/.venv && ./start.sh" >&2
  exit 1
fi

if [[ ! -f "$REPO_ROOT/.env" && ! -f "$BACKEND_DIR/.env" ]]; then
  echo "⚠️  No .env file found. Copy backend/.env.example to backend/.env and fill in OPENROUTER_API_KEY before running." >&2
fi

# ── Set up Python venv (idempotent) ──────────────────────────
if [[ ! -d "$BACKEND_DIR/.venv" ]]; then
  echo "📦 Creating Python venv at backend/.venv …"
  python3 -m venv "$BACKEND_DIR/.venv"
fi

# ── Build the two startup commands ───────────────────────────
BACKEND_CMD="cd '$BACKEND_DIR' && source .venv/bin/activate && pip install -r requirements.txt --quiet && python -m uvicorn app.main:app --reload --port 8010 --timeout-graceful-shutdown 300"
FRONTEND_CMD="cd '$FRONTEND_DIR' && npm install --silent && npm run dev"

# ── Launch ───────────────────────────────────────────────────
case "$(uname -s)" in
  Darwin)
    # macOS — open two Terminal.app windows so logs are separate
    osascript <<EOF
tell application "Terminal"
  do script "$BACKEND_CMD"
  do script "$FRONTEND_CMD"
  activate
end tell
EOF
    ;;
  Linux)
    # Linux — try to use whatever terminal is available, fall back to bg
    if command -v gnome-terminal >/dev/null 2>&1; then
      gnome-terminal --tab --title="MV Studio Backend" -- bash -c "$BACKEND_CMD; exec bash"
      gnome-terminal --tab --title="MV Studio Frontend" -- bash -c "$FRONTEND_CMD; exec bash"
    elif command -v xterm >/dev/null 2>&1; then
      xterm -T "MV Studio Backend"  -e bash -c "$BACKEND_CMD"  &
      xterm -T "MV Studio Frontend" -e bash -c "$FRONTEND_CMD" &
    else
      echo "▶️  No terminal emulator found — running both in background, output combined."
      bash -c "$BACKEND_CMD" 2>&1 | sed 's/^/[backend ] /' &
      bash -c "$FRONTEND_CMD" 2>&1 | sed 's/^/[frontend] /' &
      wait
    fi
    ;;
  *)
    echo "❌ Unsupported OS: $(uname -s). Run the commands manually:" >&2
    echo "   backend:  $BACKEND_CMD" >&2
    echo "   frontend: $FRONTEND_CMD" >&2
    exit 1
    ;;
esac

cat <<INFO

✅ Both servers starting.

  Backend:   http://localhost:8010
  Frontend:  http://localhost:3000
  API docs:  http://localhost:8010/docs

INFO
