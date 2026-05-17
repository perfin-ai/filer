# Filer — Implementation Plan

This plan derives from `assets/Tech Stack for Filer App.pdf`. It describes a Mac-native desktop app that indexes a user's local files, suggests destination folders for new files, and (later) answers questions over the index.

## 1. Goals & Non-goals

**Goals**
- Mac-native desktop app named **Filer**.
- Two initial tabs: **Indexing** and **Filing**. A later third tab: **Assistant** (the PDF labels both as "Filing"; this plan treats the third as Assistant).
- 100% local-first: no sign-in, no cloud dependency by default; user files never leave the machine.
- Index a chosen root folder (text extraction → chunking → embeddings) and suggest filing destinations for inbox files.
- Clean upgrade path to a RAG-based assistant over the same indexes.

**Non-goals (MVP)**
- Cross-platform packaging (Windows/Linux) — Mac only first.
- Multi-user sync, accounts, telemetry.
- OCR for images, email parsing, advanced rule engine — deferred.

## 2. Tech Stack

| Layer | Choice |
|---|---|
| Desktop shell | Tauri 2 |
| UI | React + TypeScript (Vite) |
| Backend sidecar | Python FastAPI on `127.0.0.1` |
| Relational store | SQLite (file inventory, metadata, jobs, move history) |
| Keyword search | SQLite FTS5 |
| Vector store | LanceDB (embedded, local path) |
| Embeddings | `sentence-transformers` (e.g. `bge-small` or `nomic-embed-text`) |
| Parsers | PyMuPDF / pypdf, python-docx, native text, pandas/openpyxl |
| Python packaging | [`uv`](https://docs.astral.sh/uv/) — manages the venv, resolves/locks dependencies via `pyproject.toml` + `uv.lock`, and runs the sidecar (`uv run …`). Chosen over pip/poetry/pipenv for its speed, single-binary install, and deterministic lockfile. |
| Later: local LLM | Ollama (optional) |
| Later: cloud LLM | User-supplied API key (optional) |

Rationale: Tauri produces a lightweight native binary while letting us keep the UI in React. A Python sidecar isolates the AI/parsing pipeline from the shell and is the natural home for embeddings and RAG.

## 3. Repository Layout

```
filer/
├── apps/
│   ├── shell/                Tauri + React app
│   │   ├── src/              React UI
│   │   ├── src-tauri/        Rust shell, sidecar launcher, IPC
│   │   └── package.json
│   └── backend/              Python FastAPI sidecar
│       ├── filer_backend/
│       │   ├── api/          FastAPI routes
│       │   ├── indexing/     walker, parser, chunker, embedder
│       │   ├── filing/       suggestion engine, move/undo
│       │   ├── storage/      SQLite + LanceDB adapters
│       │   └── main.py       app entrypoint
│       └── pyproject.toml
├── assets/                   source docs (already present)
└── docs/                     plan.md (this file), future ADRs
```

Local app data lives outside the repo, under `~/Library/Application Support/Filer/`:

```
filer.db          SQLite metadata + FTS5
lancedb/          vector index
thumbnails/
logs/
```

## 4. Data Model

**SQLite tables** (see PDF for full field list)
- `files` — id, absolute_path, filename, extension, mime_type, size_bytes, created_at, modified_at, content_hash, indexed_at, status
- `folders` — id, absolute_path, parent_path, folder_name
- `documents` — file_id, extracted_text, parser_used, extraction_status
- `chunks` — id, file_id, chunk_text, chunk_index, token_count
- `filing_actions` — id, source_path, destination_path, accepted_suggestion, moved_at

**LanceDB rows** — `chunk_id, file_id, path, folder_path, filename, chunk_text, embedding, metadata`.

`content_hash` drives change detection: re-indexing skips files whose hash matches the last indexed value.

## 5. Backend (FastAPI sidecar)

The shell launches the sidecar on a random localhost port and passes it to the UI. Endpoints (initial set):

- `POST /index/start` — body `{ root_path }`; returns `job_id`.
- `GET  /index/jobs/{job_id}` — progress (scanning / extracting / embedding / complete, counts).
- `GET  /index/folders` — previously indexed roots.
- `POST /suggest` — body `{ path }`; returns ranked destination folders with scores + reasons.
- `POST /move` — body `{ source, destination }`; performs the move, records `filing_actions`.
- `POST /move/undo` — undoes the most recent `filing_actions` row.
- `GET  /search?q=…` — hybrid keyword + vector search (used later by Assistant).
- `GET  /health` — for the shell's readiness probe.

Indexing runs as **Celery** tasks in a separate worker process launched alongside the FastAPI sidecar. Celery gives us durable job state, retries, cancellation, and a clean separation between the API (fast, request/response) and the long-running walk/extract/embed pipeline. Configuration:

- **Broker**: Redis embedded as a sidecar binary, or — to avoid shipping Redis — Celery's filesystem/SQLite broker via `kombu` against the same `~/Library/Application Support/Filer/` data dir.
- **Result backend**: SQLite (so job rows survive app restarts and are queryable from the API).
- **Workers**: one worker process with a small concurrency (e.g. 2) so embedding doesn't starve the UI; tasks chained as `walk → extract → chunk → embed` so each stage is independently retryable.
- **Progress**: tasks publish stage + counters via `update_state(meta=…)`; the API exposes them through `GET /index/jobs/{id}` and an SSE stream at `/index/jobs/{id}/events`.
- **Cancellation**: `POST /index/jobs/{id}/cancel` revokes the Celery task; partial progress is preserved in SQLite so a re-run resumes via `content_hash`.

## 6. Indexing Pipeline

For a chosen root:

1. Walk the filesystem (respect `.gitignore`-style skips, hidden files, symlinks).
2. For each file:
   - capture metadata, compute `content_hash`, upsert `files` row.
   - if hash unchanged → skip.
   - dispatch to parser by mime/extension:
     - PDF → PyMuPDF (fallback pypdf)
     - DOCX → python-docx
     - TXT/MD → native read
     - CSV/XLSX → pandas/openpyxl
     - other → metadata-only
   - chunk extracted text (token-aware, ~512 tokens with overlap).
   - embed chunks, upsert into LanceDB; write `documents` + `chunks` rows.
3. Optional later: watch the root with `fsevents` for incremental updates.

## 7. Filing Suggestion Engine

For an inbox file, score each candidate destination folder using a weighted blend:

| Signal | Source |
|---|---|
| Semantic similarity | cosine between file embedding and folder/document embeddings |
| Filename similarity | token overlap, fuzzy match (e.g. "Fidelity", "Eliquis") |
| Metadata similarity | extension, date, mime type |
| Folder history | prior `filing_actions` to the same destination boost the score |
| Rules (later) | user-defined regex/keyword → destination mappings |

Return top-N folders with score, label, and a short human-readable reason (used by the UI's "Why" line).

## 8. UI

**Shell**: tab bar with `Indexing` and `Filing` (later `Assistant`). State managed locally (React Query for backend calls).

**Indexing tab**
- Drop target for a root folder, plus `Choose Folder…` button (native picker).
- List of previously indexed roots with last-indexed time, doc count, re-index button.
- Live progress component for the active job: Scanning → Extracting → Embedding → Complete, with per-stage counters.

**Filing tab**
- Two-pane layout: **Inbox** (a chosen "to file" folder) on the left, **Folder tree** of the indexed library on the right.
- Selecting an inbox file calls `/suggest` and highlights the top folder, showing confidence and reason.
- Actions: `Move here`, drag-and-drop onto a folder, `Undo last move`.

**Assistant tab (later)**
- Chat surface; each answer cites local files with click-to-open.

## 9. Build Order (MVP milestones)

1. Tauri + React + TS shell, empty tabs, dev script that launches the Python sidecar.
2. FastAPI sidecar with `/health`; shell waits on readiness before rendering.
3. Folder picker and drag/drop in the Indexing tab → call `/index/start`.
4. SQLite schema + file walker + metadata-only indexing; progress stream.
5. Text extraction for PDF / TXT / DOCX → `documents` + `chunks`.
6. LanceDB embedding pipeline with `sentence-transformers`.
7. Filing tab: inbox + folder tree, `/suggest` integration.
8. `Move` + `Undo` wired through `filing_actions`.
9. Package as a signed `.app` (Tauri bundler, with sidecar embedded).
10. Assistant tab: hybrid retrieval → rerank → answer with citations.

Each milestone is independently demoable; do not start the next until the previous is usable end-to-end.

## 10. Packaging & Distribution

- Tauri bundles the Rust shell + web assets. The Python sidecar is shipped as a PyInstaller (or `briefcase`) binary placed in `src-tauri/binaries/` and declared in `tauri.conf.json` as an external binary.
- Sign with a Developer ID certificate; notarize for Gatekeeper.
- App data path resolved via Tauri's `appDataDir`; never write inside the bundle.

## 11. Open Questions

- Embedding model default: `bge-small-en-v1.5` vs `nomic-embed-text` — benchmark on a sample corpus before locking in.
- Should the Inbox be a fixed user-configured folder (e.g. `~/Downloads`) or a per-session pick?
- Reindex policy on file rename vs. content edit (content_hash covers edits; renames need a separate path-watch).
- Whether to expose a "rules" UI in MVP or defer entirely to v2.

## 12. Risks

- **Sidecar startup latency** — mitigate with a splash state and a readiness probe.
- **PyInstaller + native deps** (PyMuPDF, lancedb, torch for embeddings) can balloon binary size; consider ONNX-runtime variants of the embedding model to avoid shipping torch.
- **First-index time** on large folders is unbounded; ensure the job is cancellable and resumable.
- **macOS permissions** — the app needs Full Disk Access for folders outside the sandbox; document the first-run flow.
