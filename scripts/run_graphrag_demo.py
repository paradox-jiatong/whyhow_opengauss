"""Run a local GraphRAG demo against a running API server."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from uuid import uuid4

BASE_URL = "http://127.0.0.1:8000"
API_KEY = "demo-api-key"


def request(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    workspace_name = f"graphrag-demo-{uuid4().hex[:8]}"
    workspace = request("POST", "/workspaces", {"name": workspace_name})["workspace"]
    workspace_id = workspace["id"]

    request(
        "POST",
        f"/chunks?{urllib.parse.urlencode({'workspace_id': workspace_id})}",
        {
            "chunks_in": [
                {
                    "content": "openGauss 支持 事务一致性。openGauss 还支持 SQL 查询。WhyHow 支持 图谱抽取。",
                    "tags": ["opengauss", "whyhow", "graphrag"],
                },
                {
                    "content": "香蕉是一种黄色水果，和数据库能力没有直接关系。",
                    "tags": ["noise"],
                },
            ]
        },
    )

    schema_body = {
        "entities": [
            {"name": "database", "description": "database or framework"},
            {"name": "capability", "description": "supported capability"},
        ],
        "relations": [
            {"name": "supports", "head": "database", "tail": "capability", "description": "capability support"},
        ],
        "patterns": [
            {"head": "database", "relation": "supports", "tail": "capability", "description": "database supports capability"},
        ],
    }
    schema = request(
        "POST",
        f"/schemas?{urllib.parse.urlencode({'workspace_id': workspace_id, 'name': 'capability-schema'})}",
        schema_body,
    )["schemas"][0]
    schema_id = schema["id"]

    build = request(
        "POST",
        "/graphs/graphrag/build?"
        + urllib.parse.urlencode(
            {
                "workspace_id": workspace_id,
                "schema_id": schema_id,
                "name": f"capability-graph-{uuid4().hex[:8]}",
            }
        ),
    )
    graph_id = build["graph"]["id"]

    result = request(
        "GET",
        f"/graphs/{graph_id}/ask?"
        + urllib.parse.urlencode({"question": "openGauss 支持什么能力？", "top_k": 5, "tags": "opengauss"}),
    )

    output = {
        "workspace_id": workspace_id,
        "schema_id": schema_id,
        "graph_id": graph_id,
        "triples_extracted": build["triples_extracted"],
        "nodes_written": build["nodes_written"],
        "triples_written": build["triples_written"],
        "answer": result["answer"],
        "evidence": result["evidence"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    if build["triples_written"] < 2:
        sys.exit("expected at least two graph triples")
    if not any(item["source"] == "triple" for item in result["evidence"]):
        sys.exit("expected graph triple evidence")
    routes = {route for item in result["evidence"] for route in item.get("routes", [item.get("route")])}
    expected_routes = {"vector_chunk", "keyword_chunk", "predicate_chunk", "graph_path"}
    if not expected_routes.issubset(routes):
        sys.exit(f"expected four rough recall routes, got {sorted(routes)}")


if __name__ == "__main__":
    main()
