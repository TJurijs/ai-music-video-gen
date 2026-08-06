#!/usr/bin/env bash
set -Eeuo pipefail

export PYTHONUTF8=1
export PYTHONUNBUFFERED=1

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

for command_name in python3 node npm ffmpeg ffprobe; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command '$command_name' is not installed or not on PATH." >&2
    exit 1
  fi
done

python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
  echo "Python 3.11+ is required; found $(python3 --version 2>&1)." >&2
  exit 1
}

node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>20 || (a===20 && b>=9) ? 0 : 1)' || {
  echo "Node 20.9+ is required; found $(node --version 2>&1)." >&2
  exit 1
}

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Creating the backend virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

REQUIREMENTS_FILE="$BACKEND_DIR/requirements.txt"
REQUIREMENTS_MARKER="$VENV_DIR/.requirements.sha256"
REQUIREMENTS_HASH="$($VENV_PYTHON -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$REQUIREMENTS_FILE")"
INSTALLED_REQUIREMENTS_HASH=""
if [[ -f "$REQUIREMENTS_MARKER" ]]; then
  INSTALLED_REQUIREMENTS_HASH="$(tr -d '\r\n' < "$REQUIREMENTS_MARKER")"
fi
if [[ "$REQUIREMENTS_HASH" != "$INSTALLED_REQUIREMENTS_HASH" ]]; then
  echo "Synchronizing backend dependencies..."
  "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$REQUIREMENTS_FILE"
  printf '%s\n' "$REQUIREMENTS_HASH" > "$REQUIREMENTS_MARKER"
fi

PACKAGE_LOCK="$FRONTEND_DIR/package-lock.json"
NODE_MODULES="$FRONTEND_DIR/node_modules"
PACKAGE_MARKER="$NODE_MODULES/.package-lock.sha256"
PACKAGE_HASH="$($VENV_PYTHON -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$PACKAGE_LOCK")"
INSTALLED_PACKAGE_HASH=""
if [[ -f "$PACKAGE_MARKER" ]]; then
  INSTALLED_PACKAGE_HASH="$(tr -d '\r\n' < "$PACKAGE_MARKER")"
fi
if [[ ! -d "$NODE_MODULES" || "$PACKAGE_HASH" != "$INSTALLED_PACKAGE_HASH" ]]; then
  echo "Installing frontend dependencies..."
  (cd "$FRONTEND_DIR" && npm ci)
  printf '%s\n' "$PACKAGE_HASH" > "$PACKAGE_MARKER"
fi

backend_pid=""
frontend_pid=""

cleanup() {
  trap - EXIT INT TERM
  for process_id in "$frontend_pid" "$backend_pid"; do
    if [[ -n "$process_id" ]] && kill -0 "$process_id" 2>/dev/null; then
      kill "$process_id" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting Music Video Studio in this terminal..."
echo "Frontend: http://localhost:3000"
echo "Backend:  http://localhost:8010"
echo "API docs: http://localhost:8010/docs"
echo "Press Ctrl+C to stop both services."

(cd "$BACKEND_DIR" && exec "$VENV_PYTHON" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8010 --reload --timeout-graceful-shutdown 300) &
backend_pid=$!

(cd "$FRONTEND_DIR" && exec npm run dev) &
frontend_pid=$!

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 0.5
done

echo "One service exited; stopping the other service." >&2
cleanup
exit 1
