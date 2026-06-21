"""CLI for the suggestor evaluation harness.

    uv run python -m filer_backend.eval run --label EXP_BASE --n 100 --seed 42
    uv run python -m filer_backend.eval run --label EXP_OPENAI --profile openai
    uv run python -m filer_backend.eval compare
"""

import argparse
import csv
import json
from pathlib import Path

from filer_backend.eval.core import run_eval, run_random_baseline


def _cmd_run(args: argparse.Namespace) -> None:
    res = run_eval(
        label=args.label,
        n=args.n,
        seed=args.seed,
        profile=args.profile,
        root=args.root,
        out_dir=args.out_dir,
    )
    m = res["metrics"]["overall"]
    print(f"\n[{res['label']}] scored {m['n']} files in {res['elapsed_s']}s")
    print(
        f"  acc@1={m['acc@1']:.3f}  acc@3={m['acc@3']:.3f}  "
        f"mrr={m['mrr']:.3f}  prefix={m['prefix']:.3f}"
    )
    for dim in ("by_kind", "by_density"):
        print(f"  {dim}:")
        for k, v in res["metrics"][dim].items():
            print(f"    {k:12s} n={v['n']:<4} acc@1={v.get('acc@1', 0):.3f} "
                  f"acc@3={v.get('acc@3', 0):.3f} mrr={v.get('mrr', 0):.3f}")
    print(f"  -> {Path(args.out_dir) / (res['label'] + '.json')}")


def _cmd_random(args: argparse.Namespace) -> None:
    res = run_random_baseline(
        label=args.label,
        n=args.n,
        seed=args.seed,
        root=args.root,
        out_dir=args.out_dir,
        k=args.k,
    )
    m = res["metrics"]["overall"]
    print(
        f"\n[{res['label']}] {res['config']['strategy']} over "
        f"{res['config']['n_folders']} folders, scored {m['n']} files"
    )
    print(
        f"  acc@1={m['acc@1']:.3f}  acc@3={m['acc@3']:.3f}  "
        f"mrr={m['mrr']:.3f}  prefix={m['prefix']:.3f}"
    )
    print(f"  -> {Path(args.out_dir) / (res['label'] + '.json')}")


def _cmd_compare(args: argparse.Namespace) -> None:
    path = Path(args.out_dir) / "summary.csv"
    if not path.exists():
        print("no summary.csv yet")
        return
    with path.open() as f:
        rows = list(csv.DictReader(f))
    cols = ["label", "llm_model", "embedding", "n", "acc@1", "acc@3", "mrr", "prefix"]
    print("  ".join(c.ljust(14) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(14) for c in cols))


def main() -> None:
    p = argparse.ArgumentParser(prog="filer_backend.eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run an evaluation")
    r.add_argument("--label", required=True)
    r.add_argument("--n", type=int, default=50)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--profile", default=None, help="LLM profile from config")
    r.add_argument("--root", default=None, help="limit to files under this path")
    r.add_argument("--out-dir", default="evals")
    r.set_defaults(func=_cmd_run)

    rb = sub.add_parser("random", help="random-folder baseline (closed-form)")
    rb.add_argument("--label", default="RANDOM")
    rb.add_argument("--n", type=int, default=50)
    rb.add_argument("--seed", type=int, default=42)
    rb.add_argument("--k", type=int, default=3, help="folders picked at random")
    rb.add_argument("--root", default=None, help="limit to files under this path")
    rb.add_argument("--out-dir", default="evals")
    rb.set_defaults(func=_cmd_random)

    c = sub.add_parser("compare", help="print the summary leaderboard")
    c.add_argument("--out-dir", default="evals")
    c.set_defaults(func=_cmd_compare)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
