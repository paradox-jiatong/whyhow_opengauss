import asyncio

from whyhow_api.services.graphrag_service import (
    Evidence,
    RetrievalCandidate,
    TimingCollector,
    bm25_score_chunks,
    extract_schema_guided_triples,
    fuse_rough_recall_candidates,
    rerank_evidence,
)


def test_schema_guided_extraction_normalizes_entities_and_uses_schema_patterns():
    async def run():
        schema = {
            "entities": [
                {"name": "database"},
                {"name": "capability"},
            ],
            "relations": [
                {"name": "supports", "head": "database", "tail": "capability"},
            ],
            "patterns": [
                {"head": "database", "relation": "supports", "tail": "capability"},
            ],
        }
        chunk = {
            "id": "11111111-1111-1111-1111-111111111111",
            "content": "openGauss 支持 事务一致性。openGauss 还支持 SQL 查询。",
        }

        triples = await extract_schema_guided_triples(schema, [chunk])

        assert [(t.head, t.relation, t.tail) for t in triples] == [
            ("opengauss", "supports", "事务一致性"),
            ("opengauss", "supports", "sql 查询"),
        ]
        assert all(t.head_type == "database" and t.tail_type == "capability" for t in triples)

    asyncio.run(run())


def test_rerank_evidence_prefers_items_matching_question_terms():
    items = [
        Evidence(source="chunk", text="香蕉是一种黄色水果", score=0.9, payload={}),
        Evidence(source="triple", text="openGauss supports SQL 查询", score=0.2, payload={}),
    ]

    ranked = rerank_evidence("openGauss 的 SQL 能力是什么？", items, top_k=2)

    assert ranked[0].source == "triple"


def test_fuse_rough_recall_candidates_keeps_four_routes_and_deduplicates():
    candidates = [
        RetrievalCandidate(route="vector_chunk", source="chunk", text="openGauss 支持 SQL 查询", score=0.9, payload={"id": "c1"}),
        RetrievalCandidate(route="keyword_chunk", source="chunk", text="openGauss 支持 SQL 查询", score=0.6, payload={"id": "c1"}),
        RetrievalCandidate(route="graph_path", source="triple", text="opengauss supports SQL 查询", score=0.7, payload={"id": "t1"}),
        RetrievalCandidate(route="predicate_chunk", source="chunk", text="openGauss 标签命中", score=0.5, payload={"id": "c2"}),
    ]

    fused = fuse_rough_recall_candidates(candidates)

    assert {route for item in fused for route in item.payload["routes"]} == {
        "vector_chunk",
        "keyword_chunk",
        "graph_path",
        "predicate_chunk",
    }
    assert len([item for item in fused if item.payload["id"] == "c1"]) == 1
    assert fused[0].route == "vector_chunk"


def test_timing_collector_records_stage_and_total_milliseconds():
    timing = TimingCollector()

    with timing.stage("embedding"):
        pass
    result = timing.finish()

    assert "embedding" in result
    assert "total" in result
    assert result["embedding"] >= 0
    assert result["total"] >= result["embedding"]


def test_fuse_rough_recall_candidates_can_filter_enabled_routes():
    candidates = [
        RetrievalCandidate(route="vector_chunk", source="chunk", text="vector hit", score=0.9, payload={"id": "c1"}),
        RetrievalCandidate(route="keyword_chunk", source="chunk", text="keyword hit", score=0.9, payload={"id": "c2"}),
    ]

    fused = fuse_rough_recall_candidates(candidates, enabled_routes={"keyword_chunk"})

    assert [item.payload["id"] for item in fused] == ["c2"]


def test_bm25_keyword_scoring_prefers_discriminative_terms_over_long_generic_text():
    rows = [
        {
            "id": "generic",
            "content": "数据库 查询 系统 运维 " * 30,
            "content_obj": None,
        },
        {
            "id": "specific",
            "content": "shared_buffers 过低会导致缓存命中率下降",
            "content_obj": None,
        },
    ]

    scored = bm25_score_chunks("shared_buffers 设置过低会导致什么", rows)

    assert [item.payload["id"] for item in scored] == ["specific"]
