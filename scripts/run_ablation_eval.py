"""Run retrieval ablation evaluations sequentially."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_retrieval import EVAL_DIR, evaluate


ABLATIONS: dict[str, list[str] | None] = {
    "hybrid": None,
    "vector": ["vector_chunk"],
    "keyword": ["keyword_chunk"],
    "predicate": ["predicate_chunk"],
    "graph_path": ["graph_path"],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(EVAL_DIR / "run_manifest.json"))
    parser.add_argument("--qa", default=str(EVAL_DIR / "ops_qa_200.jsonl"))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-answer", action="store_true")
    parser.add_argument("--out-dir", default=str(EVAL_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.limit}" if args.limit else "200"
    if not args.include_answer:
        suffix += "_no_answer"

    summary = {}
    for name, routes in ABLATIONS.items():
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"=== {name} started at {started} ===", flush=True)
        output = evaluate(
            Path(args.manifest),
            Path(args.qa),
            k=args.k,
            limit=args.limit,
            routes=routes,
            include_answer=args.include_answer,
        )
        out_path = out_dir / f"ablation_{name}_{suffix}.json"
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        summary[name] = output["metrics"]
        print(json.dumps({name: output["metrics"]}, ensure_ascii=False, indent=2), flush=True)
        print(f"Details: {out_path}", flush=True)

    summary_path = out_dir / f"ablation_summary_{suffix}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Summary: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
