"""Load the ops evaluation dataset into the local API."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVAL_DIR = ROOT / "eval"
BASE_URL = "http://127.0.0.1:8000"
API_KEY = "demo-api-key"


def request(method: str, path: str, body: dict | None = None, *, base_url: str, api_key: str) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _doc_metadata(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    module = next((line.split("：", 1)[1].strip() for line in text.splitlines() if line.startswith("模块：")), "ops")
    tag = next((line.split("：", 1)[1].strip() for line in text.splitlines() if line.startswith("标签：")), path.stem)
    return text, module, tag


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _schema_body() -> dict:
    return {
        "entities": [
            {"name": "topic", "description": "database ops topic"},
            {"name": "capability", "description": "supported capability"},
            {"name": "cause", "description": "problem cause"},
            {"name": "effect", "description": "problem effect"},
        ],
        "relations": [
            {"name": "supports", "head": "topic", "tail": "capability", "description": "topic supports capability"},
            {"name": "可能原因", "head": "topic", "tail": "cause", "description": "topic possible cause"},
            {"name": "导致", "head": "cause", "tail": "effect", "description": "cause leads to effect"},
        ],
        "patterns": [
            {"head": "topic", "relation": "supports", "tail": "capability", "description": "topic supports capability"},
        ],
    }


def load_dataset(base_url: str, api_key: str, manifest_path: Path) -> dict:
    workspace_name = f"ops-eval-{uuid4().hex[:8]}"
    workspace = request("POST", "/workspaces", {"name": workspace_name}, base_url=base_url, api_key=api_key)["workspace"]
    workspace_id = workspace["id"]

    chunk_manifest: dict[str, str] = {}
    docs = []
    chunks_by_doc: dict[str, list[dict]] = {}
    for row in _read_jsonl(EVAL_DIR / "chunks_manifest.jsonl"):
        chunks_by_doc.setdefault(row["doc_id"], []).append(row)

    for doc_id, manifest_rows in sorted(chunks_by_doc.items()):
        chunks_in = []
        chunk_keys = []
        for row in manifest_rows:
            chunk_key = row["chunk_key"]
            chunk_keys.append(chunk_key)
            chunks_in.append({
                "content": row["text"],
                "tags": row["tags"],
                "user_metadata": {"doc_id": doc_id, "module": row["module"], "section": row["section"]},
            })
        result = request(
            "POST",
            f"/chunks?{urllib.parse.urlencode({'workspace_id': workspace_id})}",
            {"chunks_in": chunks_in},
            base_url=base_url,
            api_key=api_key,
        )
        for chunk_key, row in zip(chunk_keys, result["chunks"]):
            chunk_manifest[chunk_key] = row.get("_id") or row.get("id")
        docs.append({"doc_id": doc_id, "chunks": chunk_keys})

    schema = request(
        "POST",
        f"/schemas?{urllib.parse.urlencode({'workspace_id': workspace_id, 'name': 'ops-eval-schema'})}",
        _schema_body(),
        base_url=base_url,
        api_key=api_key,
    )["schemas"][0]
    graph = request(
        "POST",
        "/graphs/graphrag/build?"
        + urllib.parse.urlencode({"workspace_id": workspace_id, "schema_id": schema["id"], "name": f"ops-eval-graph-{uuid4().hex[:8]}", "chunk_limit": 1000}),
        base_url=base_url,
        api_key=api_key,
    )

    manifest = {
        "base_url": base_url,
        "api_key": api_key,
        "workspace_id": workspace_id,
        "schema_id": schema["id"],
        "graph_id": graph["graph"]["id"],
        "chunk_key_to_id": chunk_manifest,
        "docs": docs,
        "graph_build": {
            "chunks_scanned": graph["chunks_scanned"],
            "triples_extracted": graph["triples_extracted"],
            "nodes_written": graph["nodes_written"],
            "triples_written": graph["triples_written"],
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--api-key", default=API_KEY)
    parser.add_argument("--manifest", default=str(EVAL_DIR / "run_manifest.json"))
    args = parser.parse_args()

    manifest = load_dataset(args.base_url, args.api_key, Path(args.manifest))
    print(json.dumps(manifest["graph_build"], ensure_ascii=False, indent=2))
    print(f"Manifest: {args.manifest}")


if __name__ == "__main__":
    main()
