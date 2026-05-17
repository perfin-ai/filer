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
# backend only
cd apps/backend && uv run uvicorn filer_backend.main:app --host 127.0.0.1 --port 8765 --reload

# frontend only (browser, no Tauri shell)
cd apps/shell && npm run dev
```
