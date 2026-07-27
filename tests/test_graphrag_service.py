import asyncio

from whyhow_api.services.graphrag_service import (
    Evidence,
    extract_schema_guided_triples,
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
