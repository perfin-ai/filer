"""Hybrid retrieval over the LanceDB chunk store.

Combines dense vector search and BM25 full-text search with Reciprocal Rank
Fusion (RRF), then aggregates the fused chunk hits into candidate destination
folders. `exclude_file_ids` hides a file from its own results (used by eval).
"""

from dataclasses import dataclass, field

from filer_backend.storage import vectors

RRF_K = 60


@dataclass
class FolderCandidate:
    folder_path: str
    score: float
    files: list[str] = field(default_factory=list)


def hybrid_search(
    text: str, vector, k: int = 20, exclude_file_ids=()
) -> list[dict]:
    """Return up to k chunk hits, RRF-fused across vector + FTS, each with `_rrf`."""
    vhits = vectors.vector_search(vector, k=k, exclude_file_ids=exclude_file_ids)
    thits = vectors.text_search(text, k=k, exclude_file_ids=exclude_file_ids)

    scores: dict[str, float] = {}
    data: dict[str, dict] = {}
    for hit_list in (vhits, thits):
        for rank, h in enumerate(hit_list):
            cid = h["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            data[cid] = h

    fused = sorted(data.values(), key=lambda h: scores[h["chunk_id"]], reverse=True)
    for h in fused:
        h["_rrf"] = scores[h["chunk_id"]]
    return fused[:k]


def candidate_folders(hits: list[dict], limit: int = 8) -> list[FolderCandidate]:
    """Aggregate fused chunk hits into ranked candidate folders."""
    agg: dict[str, FolderCandidate] = {}
    for h in hits:
        fp = h["folder_path"]
        cand = agg.get(fp)
        if cand is None:
            cand = agg[fp] = FolderCandidate(folder_path=fp, score=0.0)
        cand.score += h.get("_rrf", 0.0)
        if h["filename"] not in cand.files:
            cand.files.append(h["filename"])
    ranked = sorted(agg.values(), key=lambda c: c.score, reverse=True)
    return ranked[:limit]
