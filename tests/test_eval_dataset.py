import json
from pathlib import Path

from whyhow_api.services.eval_metrics import compute_ranking_metrics, percentile


ROOT = Path(__file__).resolve().parents[1]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ops_qa_dataset_has_expected_size_distribution_and_gold_fields():
    rows = _read_jsonl(ROOT / "eval" / "ops_qa_200.jsonl")

    assert len(rows) == 200
    distribution = {}
    for row in rows:
        distribution[row["type"]] = distribution.get(row["type"], 0) + 1
        assert row["question"]
        assert row["answer"]
        assert row["gold_chunk_keys"]
        if row["type"] == "graph_path":
            assert row["gold_paths"]

    assert distribution == {
        "vector": 80,
        "keyword": 40,
        "predicate": 40,
        "graph_path": 40,
    }


def test_gold_graph_references_existing_raw_docs():
    doc_ids = {path.stem for path in (ROOT / "eval" / "docs").glob("*.md")}
    rows = _read_jsonl(ROOT / "eval" / "gold_graph.jsonl")

    assert len(rows) >= 40
    assert {row["doc_id"] for row in rows}.issubset(doc_ids)
    assert all(len(row["triple"]) == 3 for row in rows)


def test_chunks_manifest_records_semantic_chunks_and_covers_gold_keys():
    chunks = _read_jsonl(ROOT / "eval" / "chunks_manifest.jsonl")
    qa_rows = _read_jsonl(ROOT / "eval" / "ops_qa_200.jsonl")
    chunk_keys = {row["chunk_key"] for row in chunks}

    assert len(chunk_keys) == len(chunks)
    assert len(chunks) >= 24
    for row in chunks:
        assert row["chunk_key"]
        assert row["doc_id"]
        assert row["section"]
        assert row["text"]
        assert row["tags"]
        assert "window" in row["metadata"]

    for qa in qa_rows:
        assert set(qa["gold_chunk_keys"]).issubset(chunk_keys)


def test_ranking_metrics_and_percentile_are_stable():
    cases = [
        {"gold": {"a"}, "retrieved": ["x", "a"]},
        {"gold": {"b"}, "retrieved": ["b", "z"]},
        {"gold": {"c"}, "retrieved": ["x", "y"]},
    ]

    metrics = compute_ranking_metrics(cases, k=2)

    assert metrics["hit@2"] == 2 / 3
    assert metrics["recall@2"] == 2 / 3
    assert metrics["mrr@2"] == (1 / 2 + 1) / 3
    assert percentile([10, 20, 30, 40], 95) == 40
