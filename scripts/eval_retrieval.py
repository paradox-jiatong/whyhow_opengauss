"""Evaluate GraphRAG retrieval over the ops QA dataset via the local API."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whyhow_api.services.eval_metrics import compute_ranking_metrics, percentile

EVAL_DIR = ROOT / "eval"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def request(method: str, path: str, body: dict | None = None, *, base_url: str, api_key: str) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _triple_text(triple: list[str]) -> str:
    relation_aliases = {"supports": "支持"}
    normalized = [str(item).lower() for item in triple]
    if len(normalized) >= 2:
        normalized[1] = relation_aliases.get(normalized[1], normalized[1])
    return " ".join(normalized)


def _path_text(path: list[str]) -> str:
    relation_aliases = {"supports": "支持"}
    normalized = [str(item).lower() for item in path]
    for idx, item in enumerate(normalized):
        normalized[idx] = relation_aliases.get(item, item)
    return " -> ".join(normalized)


def _gold_items(row: dict, manifest: dict) -> set[str]:
    gold = set()
    for key in row.get("gold_chunk_keys") or []:
        chunk_id = manifest.get("chunk_key_to_id", {}).get(key)
        if chunk_id:
            gold.add(f"chunk:{chunk_id}")
    for triple in row.get("gold_triples") or []:
        gold.add(f"triple_text:{_triple_text(triple)}")
    for path in row.get("gold_paths") or []:
        gold.add(f"path_text:{_path_text(path)}")
    return gold


def _retrieved_items(evidence: list[dict]) -> list[str]:
    out: list[str] = []
    for item in evidence:
        source = item.get("source")
        if source == "chunk" and item.get("id"):
            out.append(f"chunk:{item['id']}")
        if source == "triple":
            out.append(f"triple_text:{_triple_text(str(item.get('text', '')).split())}")
        if source == "path":
            out.append(f"path_text:{_path_text([part.strip() for part in str(item.get('text', '')).split('->')])}")
    return out


def evaluate(manifest_path: Path, qa_path: Path, k: int, limit: int | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = read_jsonl(qa_path)
    if limit:
        rows = rows[:limit]

    cases_by_type: dict[str, list[dict]] = {}
    latencies_ms: list[float] = []
    details = []

    for row in rows:
        params = {
            "question": row["question"],
            "top_k": k,
        }
        tags = (row.get("filters") or {}).get("tags") or []
        if tags:
            params["tags"] = ",".join(tags)

        start = time.perf_counter()
        result = request(
            "GET",
            f"/graphs/{manifest['graph_id']}/ask?" + urllib.parse.urlencode(params),
            base_url=manifest["base_url"],
            api_key=manifest["api_key"],
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        latencies_ms.append(elapsed_ms)

        gold = _gold_items(row, manifest)
        retrieved = _retrieved_items(result.get("evidence") or [])
        case = {"gold": gold, "retrieved": retrieved}
        cases_by_type.setdefault(row["type"], []).append(case)
        details.append({
            "id": row["id"],
            "type": row["type"],
            "latency_ms": round(elapsed_ms, 2),
            "gold": sorted(gold),
            "retrieved": retrieved,
            "answer": result.get("answer"),
        })

    metrics = {"overall": compute_ranking_metrics([case for cases in cases_by_type.values() for case in cases], k=k)}
    for typ, cases in sorted(cases_by_type.items()):
        metrics[typ] = compute_ranking_metrics(cases, k=k)
    metrics["latency_ms"] = {
        "p50": round(percentile(latencies_ms, 50), 2),
        "p95": round(percentile(latencies_ms, 95), 2),
        "count": len(latencies_ms),
    }
    return {"metrics": metrics, "details": details}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(EVAL_DIR / "run_manifest.json"))
    parser.add_argument("--qa", default=str(EVAL_DIR / "ops_qa_200.jsonl"))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=str(EVAL_DIR / "eval_results.json"))
    args = parser.parse_args()

    output = evaluate(Path(args.manifest), Path(args.qa), k=args.k, limit=args.limit)
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["metrics"], ensure_ascii=False, indent=2))
    print(f"Details: {args.out}")


if __name__ == "__main__":
    main()
