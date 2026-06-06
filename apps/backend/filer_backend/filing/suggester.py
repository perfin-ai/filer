"""Destination-folder suggestor.

Pipeline: extract + embed the inbox file → hybrid retrieve similar chunks →
aggregate candidate folders → build folder-structure context + history → ask a
provider-agnostic LLM (guided by config hints) for ranked suggestions, which may
include new folders. Falls back to retrieval candidates if the LLM is
unavailable. `exclude_file_ids` hides a file from its own retrieval (eval).
"""

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from filer_backend.embedding import get_embedder
from filer_backend.filing.llm import build_agent
from filer_backend.filing.retrieval import FolderCandidate, candidate_folders, hybrid_search
from filer_backend.indexing.extract import extract_text
from filer_backend.settings import Settings, get_settings
from filer_backend.storage.db import get_session
from filer_backend.storage.models import FilingAction, Folder, InboxFile

log = logging.getLogger(__name__)

SNIPPET_CHARS = 1200


@dataclass
class Suggestion:
    folder_path: str
    confidence: float
    rationale: str
    is_new: bool = False


def folder_context(s, candidate_paths: list[str], limit: int = 150) -> list[str]:
    """Relevant folder paths: candidates + their siblings + children + roots."""
    parents = {str(Path(c).parent) for c in candidate_paths}
    keys = set(candidate_paths) | parents
    related: set[str] = set()
    if keys:
        related |= set(
            s.execute(
                select(Folder.absolute_path).where(Folder.parent_path.in_(keys))
            ).scalars()
        )
    related |= set(
        s.execute(
            select(Folder.absolute_path).where(Folder.parent_path.is_(None))
        ).scalars()
    )
    related |= set(candidate_paths) | parents
    return sorted(related)[:limit]


def folder_history(s, limit: int = 10) -> list[tuple[str, int]]:
    rows = s.execute(select(FilingAction.destination_path)).scalars().all()
    counts = Counter(str(Path(p).parent) for p in rows)
    return counts.most_common(limit)


def _render_prompt(
    inbox_file: InboxFile,
    text: str,
    cands: list[FolderCandidate],
    ctx: list[str],
    history: list[tuple[str, int]],
) -> str:
    lines = [
        "File to file:",
        f"  name: {inbox_file.filename}",
        f"  kind: {inbox_file.kind}   extension: {inbox_file.extension or '-'}",
        f"  size_bytes: {inbox_file.size_bytes}",
        f"  modified_at: {inbox_file.modified_at}",
        "",
        "Content snippet:",
        (text[:SNIPPET_CHARS].strip() or "(no extractable text)"),
        "",
        "Candidate folders (from similarity search, best first):",
    ]
    if cands:
        for c in cands:
            files = ", ".join(c.files[:5])
            lines.append(f"  - {c.folder_path}  (similar files: {files})")
    else:
        lines.append("  (none)")
    if history:
        lines += ["", "Previously used destination folders:"]
        lines += [f"  - {f}  (x{n})" for f, n in history]
    lines += ["", "Existing folder structure (subset):"]
    lines += [f"  {p}" for p in ctx] or ["  (empty index)"]
    lines += ["", "Return ranked destination folder suggestions."]
    return "\n".join(lines)


def _fallback(cands: list[FolderCandidate]) -> list[Suggestion]:
    scale = [0.6, 0.45, 0.35]
    out = []
    for i, c in enumerate(cands[:3]):
        out.append(
            Suggestion(
                folder_path=c.folder_path.rstrip("/"),
                confidence=scale[i] if i < len(scale) else 0.3,
                rationale=f"Similar files are filed in {Path(c.folder_path).name}.",
                is_new=False,
            )
        )
    return out


def suggest_folders(
    inbox_file: InboxFile,
    *,
    exclude_file_ids=(),
    settings: Settings | None = None,
    agent=None,
) -> list[Suggestion]:
    settings = settings or get_settings()
    path = Path(inbox_file.absolute_path)
    try:
        text, _ = extract_text(path)
    except Exception:  # noqa: BLE001
        text = ""

    query = (text or inbox_file.filename)[:4000]
    emb = get_embedder()
    qvec = emb.embed([query])[0]
    hits = hybrid_search(query, qvec, k=20, exclude_file_ids=exclude_file_ids)
    cands = candidate_folders(hits, limit=8)

    s = get_session()
    try:
        ctx = folder_context(s, [c.folder_path for c in cands])
        history = folder_history(s)
    finally:
        s.close()

    prompt = _render_prompt(inbox_file, text, cands, ctx, history)
    if agent is None:
        agent = build_agent(settings)
    try:
        result = agent.run_sync(prompt)
        out = result.output.suggestions
    except Exception:  # noqa: BLE001
        log.exception("LLM suggestion failed; falling back to retrieval")
        out = []

    if not out:
        return _fallback(cands)
    out = sorted(out, key=lambda x: x.confidence, reverse=True)
    return [
        Suggestion(
            folder_path=x.folder_path.rstrip("/"),
            confidence=max(0.0, min(1.0, x.confidence)),
            rationale=x.rationale,
            is_new=x.is_new,
        )
        for x in out
    ]
