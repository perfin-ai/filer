# filer

Mac Desktop application for intelligently filing files into the correct folders.

See [`docs/plan.md`](docs/plan.md) for the implementation plan.

## Development

Prerequisites: Node 20+, Rust stable (`rustup`), [`uv`](https://docs.astral.sh/uv/), Xcode command-line tools.

One-time setup:

```sh
cd apps/shell && npm install
cd ../backend && uv sync
```

Run the full dev environment (Python backend + Tauri shell):

```sh
./scripts/dev.sh
```

Or run pieces independently:

```sh
# FastAPI server (handles requests; runs Alembic migrations on startup)
cd apps/backend && uv run uvicorn filer_backend.main:app --host 127.0.0.1 --port 8765 --reload

# Celery worker (consumes indexing jobs; required for /index/start to do anything)
cd apps/backend && uv run celery -A filer_backend.celery_app worker --pool=solo --concurrency=1 --loglevel=INFO

# frontend only (browser, no Tauri shell)
cd apps/shell && npm run dev
```

### Development notebook

A dev-only Jupyter notebook for poking at the low-level objects (configuration, file
extraction/chunking, embeddings, the vector store + retrieval, suggestions, and LLMs):

```sh
# one-time: add the dev dependency group (jupyterlab + ipykernel)
cd apps/backend && uv add --dev jupyterlab ipykernel   # or `uv sync` once it's in pyproject.toml

# launch (runs in the backend uv venv, so `filer_backend` imports resolve)
cd apps/backend && uv run jupyter lab
```

Then open `notebooks/playground.ipynb`. It defaults to an **isolated scratch DB / LanceDB**
(`apps/backend/notebooks/scratch/`) and an **offline LLM (`TestModel`)**, so it touches no real app
data and needs no API keys. Dev-only — it is not part of the shipped app.

Local app data (SQLite DB, Celery broker dirs, future LanceDB / thumbnails / logs) lives at
`~/Library/Application Support/Filer/`.
