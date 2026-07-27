"""Run a minimal local RAG demo against a running API server."""

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
        headers={
            "x-api-key": API_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    workspace_name = f"demo-rag-{uuid4().hex[:8]}"
    workspace = request("POST", "/workspaces", {"name": workspace_name})["workspace"]
    workspace_id = workspace["id"]

    chunks = request(
        "POST",
        f"/chunks?{urllib.parse.urlencode({'workspace_id': workspace_id})}",
        {
            "chunks_in": [
                {
                    "content": "openGauss 是企业级开源关系型数据库，支持事务一致性、SQL 查询和多维结构化过滤。",
                    "tags": ["opengauss", "database"],
                    "user_metadata": {"lang": "zh"},
                },
                {
                    "content": "WhyHow 支持文档分块、图谱抽取与 RAG 检索问答，适合企业知识工程实践。",
                    "tags": ["whyhow", "rag"],
                    "user_metadata": {"lang": "zh"},
                },
                {
                    "content": "香蕉是一种黄色水果，和数据库检索没有直接关系。",
                    "tags": ["noise"],
                    "user_metadata": {"lang": "zh"},
                },
            ]
        },
    )

    query = urllib.parse.urlencode(
        {
            "workspace_id": workspace_id,
            "text": "openGauss 的优势是什么？",
            "top_k": 2,
        }
    )
    result = request("GET", f"/queries/rag?{query}")

    print(json.dumps({
        "workspace_id": workspace_id,
        "chunks_created": chunks["count"],
        "answer": result["answer"],
        "top_chunks": result["top_chunks"],
    }, ensure_ascii=False, indent=2))

    if chunks["count"] != 3 or not result["top_chunks"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
