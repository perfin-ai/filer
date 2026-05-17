#!/usr/bin/env bash
# Launches the Filer dev environment: Python backend + Tauri shell (Vite + Rust).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f "$HOME/.cargo/env" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
fi

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="${FILER_BACKEND_PORT:-8765}"

echo "[dev] starting Python backend on ${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "$ROOT/apps/backend"
  uv run uvicorn filer_backend.main:app \
    --host "$BACKEND_HOST" --port "$BACKEND_PORT" --reload
) &
BACKEND_PID=$!

cleanup() {
  echo "[dev] stopping backend (pid $BACKEND_PID)"
  kill "$BACKEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[dev] starting Tauri shell"
cd "$ROOT/apps/shell"
VITE_FILER_BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}" npm run tauri dev
