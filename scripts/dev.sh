#!/usr/bin/env bash
# Launches the Filer dev environment:
#   - Python FastAPI backend (uvicorn)
#   - Celery worker for indexing tasks
#   - Tauri shell (Vite + Rust)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f "$HOME/.cargo/env" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
fi

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="${FILER_BACKEND_PORT:-8765}"

PIDS=()

cleanup() {
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

echo "[dev] starting Celery worker"
(
  cd "$ROOT/apps/backend"
  exec uv run celery -A filer_backend.celery_app worker \
    --loglevel=INFO --pool=solo --concurrency=1
) &
PIDS+=($!)

echo "[dev] starting Python backend on ${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "$ROOT/apps/backend"
  exec uv run uvicorn filer_backend.main:app \
    --host "$BACKEND_HOST" --port "$BACKEND_PORT" --reload
) &
PIDS+=($!)

echo "[dev] starting Tauri shell"
cd "$ROOT/apps/shell"
VITE_FILER_BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}" \
  npm run tauri dev
