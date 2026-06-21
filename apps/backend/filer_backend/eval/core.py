"""Leave-one-out evaluation for the folder suggestor.

Ground truth is each file's real parent folder. We index once; for each sampled
file we run the suggestor with `exclude_file_ids={file_id}` so the file can't
retrieve itself — no per-sample reindex. Metrics: top-1, top-3, MRR, and a
hierarchical path-prefix credit, reported overall and bucketed by file kind and
folder density (existing vs singleton).
"""

import csv
import json
import random
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from filer_backend.filing.fs import kind_for
from filer_backend.filing.suggester import suggest_folders
from filer_backend.settings import get_settings, settings_for_profile
from filer_backend.storage.db import get_session
from filer_backend.storage.models import File, Folder, InboxFile


def _norm(p: str) -> str:
    return p.rstrip("/")


def _prefix_credit(pred: str, truth: str) -> float:
    """Fraction of the truth path's components matched as a leading prefix."""
    pp, tp = _norm(pred).split("/"), _norm(truth).split("/")
    if not tp:
        return 0.0
    common = 0
    for a, b in zip(pp, tp):
        if a == b:
            common += 1
        else:
            break
    return common / len(tp)


def _sample(files: list[File], n: int, seed: int) -> list[File]:
    rng = random.Random(seed)
    if n <= 0 or n >= len(files):
        ordered = sorted(files, key=lambda f: f.id)
        rng.shuffle(ordered)
        return ordered
    return rng.sample(sorted(files, key=lambda f: f.id), n)


def _to_inbox(f: File) -> InboxFile:
    return InboxFile(
        id=str(f.id),
        batch_id="eval",
        absolute_path=f.absolute_path,
        filename=f.filename,
        extension=f.extension,
        mime_type=f.mime_type,
        size_bytes=f.size_bytes or 0,
        kind=kind_for(f.filename),
        modified_at=f.modified_at,
        status="ready",
        added_at=datetime.now(timezone.utc),
    )


def _aggregate(samples: list[dict]) -> dict:
    def agg(rows: list[dict]) -> dict:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        return {
            "n": n,
            "acc@1": sum(r["hit@1"] for r in rows) / n,
            "acc@3": sum(r["hit@3"] for r in rows) / n,
            "mrr": sum(r["rr"] for r in rows) / n,
            "prefix": sum(r["prefix"] for r in rows) / n,
        }

    by_kind: dict[str, list] = defaultdict(list)
    by_density: dict[str, list] = defaultdict(list)
    for r in samples:
        by_kind[r["kind"]].append(r)
        by_density[r["density"]].append(r)
    return {
        "overall": agg(samples),
        "by_kind": {k: agg(v) for k, v in sorted(by_kind.items())},
        "by_density": {k: agg(v) for k, v in sorted(by_density.items())},
    }


def run_eval(
    label: str,
    n: int = 50,
    seed: int = 42,
    profile: str | None = None,
    root: str | None = None,
    out_dir: str = "evals",
    agent=None,
) -> dict:
    settings = settings_for_profile(profile) if profile else get_settings()

    s = get_session()
    try:
        q = select(File)
        if root:
            q = q.where(File.absolute_path.like(_norm(root) + "/%", escape="\\"))
        files = list(s.execute(q).scalars())
        # Folder density: how many indexed files share each parent folder.
        folder_counts = Counter(str(Path(f.absolute_path).parent) for f in files)
    finally:
        s.close()

    if not files:
        raise SystemExit("no indexed files found; index a tree first")

    chosen = _sample(files, n, seed)
    samples: list[dict] = []
    t0 = time.monotonic()
    for f in chosen:
        truth = str(Path(f.absolute_path).parent)
        preds = [
            _norm(sug.folder_path)
            for sug in suggest_folders(
                _to_inbox(f),
                exclude_file_ids={f.id},
                settings=settings,
                agent=agent,
            )
        ]
        rank = preds.index(_norm(truth)) + 1 if _norm(truth) in preds else 0
        samples.append(
            {
                "file_id": f.id,
                "filename": f.filename,
                "truth": _norm(truth),
                "predicted": preds[:5],
                "kind": kind_for(f.filename),
                # density excludes the file itself: existing folder vs singleton.
                "density": "existing" if folder_counts[truth] > 1 else "singleton",
                "hit@1": 1.0 if preds[:1] == [_norm(truth)] else 0.0,
                "hit@3": 1.0 if _norm(truth) in preds[:3] else 0.0,
                "rr": (1.0 / rank) if rank else 0.0,
                "prefix": _prefix_credit(preds[0], truth) if preds else 0.0,
            }
        )
    elapsed = time.monotonic() - t0

    metrics = _aggregate(samples)
    result = {
        "label": label,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "seed": seed,
            "profile": profile,
            "llm": settings.llm.model_dump(),
            "embedding": settings.embedding.model_dump(),
            "n_requested": n,
            "n_scored": len(samples),
        },
        "elapsed_s": round(elapsed, 2),
        "metrics": metrics,
        "samples": samples,
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}.json").write_text(json.dumps(result, indent=2, default=str))
    _append_summary(out / "summary.csv", result)
    return result


def run_random_baseline(
    label: str = "RANDOM",
    n: int = 50,
    seed: int = 42,
    root: str | None = None,
    out_dir: str = "evals",
    k: int = 3,
) -> dict:
    """Closed-form baseline: pick `k` distinct folders uniformly at random.

    The "no-AI" floor for the suggestor. Instead of one noisy random draw we
    compute the exact expected metric for each sampled file, which removes
    sampling variance. With N existing folders and k distinct random picks
    (truth always among the N): E[acc@1]=1/N, E[acc@k]=k/N, E[MRR]=H_k/N where
    H_k=sum(1/i). Expected prefix credit averages `_prefix_credit` over all N
    folders, since the top pick is uniform. Same sample, ground truth, density
    buckets and aggregation as `run_eval`, so rows are directly comparable.
    """
    settings = get_settings()

    s = get_session()
    try:
        q = select(File)
        if root:
            q = q.where(File.absolute_path.like(_norm(root) + "/%", escape="\\"))
        files = list(s.execute(q).scalars())
        folder_counts = Counter(str(Path(f.absolute_path).parent) for f in files)
        # Universe of existing folders: every indexed folder + every file's
        # parent (so the ground-truth folder is always reachable).
        universe = {_norm(p) for p in s.execute(select(Folder.absolute_path)).scalars()}
        universe |= {_norm(str(Path(f.absolute_path).parent)) for f in files}
    finally:
        s.close()

    if not files:
        raise SystemExit("no indexed files found; index a tree first")
    folders = sorted(universe)
    N = len(folders)
    if N < k:
        raise SystemExit(f"need at least k={k} folders, found {N}")

    # Position-independent expectations (constant across files).
    h_k = sum(1.0 / i for i in range(1, k + 1))
    e_hit1, e_hitk, e_mrr = 1.0 / N, k / N, h_k / N

    chosen = _sample(files, n, seed)
    t0 = time.monotonic()
    samples: list[dict] = []
    for f in chosen:
        truth = _norm(str(Path(f.absolute_path).parent))
        # Expected prefix credit: top pick is uniform over all folders.
        e_prefix = sum(_prefix_credit(p, truth) for p in folders) / N
        samples.append(
            {
                "file_id": f.id,
                "filename": f.filename,
                "truth": truth,
                "predicted": [],  # expectation over all draws, not one sample
                "kind": kind_for(f.filename),
                "density": "existing" if folder_counts[truth] > 1 else "singleton",
                "hit@1": e_hit1,
                "hit@3": e_hitk,
                "rr": e_mrr,
                "prefix": e_prefix,
            }
        )
    elapsed = time.monotonic() - t0

    metrics = _aggregate(samples)
    result = {
        "label": label,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "seed": seed,
            "profile": None,
            "strategy": f"random-{k} (closed-form expectation)",
            "n_folders": N,
            "llm": {"model": f"random@{k}"},
            "embedding": {"model": "none"},
            "n_requested": n,
            "n_scored": len(samples),
        },
        "elapsed_s": round(elapsed, 2),
        "metrics": metrics,
        "samples": samples,
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}.json").write_text(json.dumps(result, indent=2, default=str))
    _append_summary(out / "summary.csv", result)
    return result


def _append_summary(path: Path, result: dict) -> None:
    o = result["metrics"]["overall"]
    row = {
        "label": result["label"],
        "ran_at": result["ran_at"],
        "profile": result["config"]["profile"] or "",
        "llm_model": result["config"]["llm"]["model"],
        "embedding": result["config"]["embedding"]["model"],
        "n": o.get("n", 0),
        "acc@1": round(o.get("acc@1", 0), 4),
        "acc@3": round(o.get("acc@3", 0), 4),
        "mrr": round(o.get("mrr", 0), 4),
        "prefix": round(o.get("prefix", 0), 4),
        "elapsed_s": result["elapsed_s"],
    }
    exists = path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if not exists:
            w.writeheader()
        w.writerow(row)
