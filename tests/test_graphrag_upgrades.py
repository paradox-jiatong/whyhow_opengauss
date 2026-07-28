import asyncio

from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.users import BYOOpenAIMetadata
from whyhow_api.services.entity_resolver import EntityResolver
from whyhow_api.services.graph_path_retriever import build_path_candidates
from whyhow_api.services.graphrag_service import Evidence, llm_rerank_evidence
from whyhow_api.services.schema_extractor import SchemaGuidedExtractor
from whyhow_api.services.semantic_chunker import semantic_chunk_text
from whyhow_api.utilities.local_llm import LocalLLMClient


def _llm_client() -> LLMClient:
    return LLMClient(LocalLLMClient(), BYOOpenAIMetadata(language_model_name="local-demo", embedding_name="local-demo"))


def test_schema_guided_extractor_returns_validated_nodes_triples_and_confidence():
    async def run():
        schema = {
            "entities": [{"name": "database"}, {"name": "capability"}],
            "relations": [{"name": "supports", "head": "database", "tail": "capability"}],
            "patterns": [{"head": "database", "relation": "supports", "tail": "capability"}],
        }

        result = await SchemaGuidedExtractor().extract(
            llm_client=_llm_client(),
            schema_body=schema,
            chunk_id="11111111-1111-1111-1111-111111111111",
            chunk_text="openGauss 支持 事务一致性。openGauss 还支持 SQL 查询。",
        )

        assert [(node.name, node.type) for node in result.nodes] == [
            ("openGauss", "database"),
            ("事务一致性", "capability"),
            ("SQL 查询", "capability"),
        ]
        assert [(triple.head, triple.relation, triple.tail) for triple in result.triples] == [
            ("openGauss", "supports", "事务一致性"),
            ("openGauss", "supports", "SQL 查询"),
        ]
        assert all(triple.source_chunk_id == "11111111-1111-1111-1111-111111111111" for triple in result.triples)
        assert all(0 <= triple.confidence <= 1 for triple in result.triples)

    asyncio.run(run())


def test_schema_guided_extractor_handles_ops_cause_and_effect_relations():
    async def run():
        schema = {
            "entities": [{"name": "topic"}, {"name": "cause"}, {"name": "effect"}],
            "relations": [
                {"name": "可能原因", "head": "topic", "tail": "cause"},
                {"name": "导致", "head": "cause", "tail": "effect"},
            ],
        }

        result = await SchemaGuidedExtractor().extract(
            llm_client=_llm_client(),
            schema_body=schema,
            chunk_id="c_ops",
            chunk_text="慢查询 可能原因 缺少索引。缺少索引 导致 全表扫描。",
        )

        assert [(triple.head, triple.relation, triple.tail) for triple in result.triples] == [
            ("慢查询", "可能原因", "缺少索引"),
            ("缺少索引", "导致", "全表扫描"),
        ]

    asyncio.run(run())


def test_entity_resolver_applies_aliases_type_aware_dedup_and_provenance_merge():
    async def run():
        schema = {
            "entities": [
                {"name": "database", "aliases": {"opengauss": ["OpenGauss", "open Gauss"]}},
                {"name": "capability"},
            ],
            "relations": [{"name": "supports", "head": "database", "tail": "capability"}],
        }
        extractor = SchemaGuidedExtractor()
        first = await extractor.extract(_llm_client(), schema, "c1", "openGauss 支持 SQL 查询。")
        second = await extractor.extract(_llm_client(), schema, "c2", "open Gauss 还支持 SQL 查询。")

        triples = EntityResolver(schema).to_triples([first, second])

        assert len(triples) == 1
        triple = triples[0]
        assert triple.head == "opengauss"
        assert triple.tail == "sql 查询"
        assert sorted(triple.head_properties["chunks"]) == ["c1", "c2"]
        assert sorted(triple.relation_properties["chunks"]) == ["c1", "c2"]

    asyncio.run(run())


def test_graph_path_candidate_builder_supports_one_and_two_hop_paths():
    rows = [
        {"first_id": "t1", "first_head": "opengauss", "first_relation": "supports", "middle": "sql 查询", "second_id": None, "second_relation": None, "second_tail": None, "chunks": ["c1"]},
        {"first_id": "t2", "first_head": "opengauss", "first_relation": "belongs_to", "middle": "database", "second_id": "t3", "second_relation": "has_capability", "second_tail": "事务一致性", "chunks": ["c1", "c2"]},
    ]

    candidates = build_path_candidates(rows)

    assert candidates[0].text == "opengauss -> supports -> sql 查询"
    assert candidates[1].text == "opengauss -> belongs_to -> database -> has_capability -> 事务一致性"
    assert candidates[1].payload["hop"] == 2


def test_semantic_chunker_respects_headings_window_and_overlap():
    text = "# openGauss\nopenGauss 支持事务一致性和 SQL 查询。\n\n## WhyHow\nWhyHow 支持 Schema-guided GraphRAG 抽取。"

    chunks = semantic_chunk_text(text, max_chars=36, overlap_chars=8)

    assert len(chunks) >= 2
    assert chunks[0].metadata["section"] == "openGauss"
    assert any("WhyHow" in chunk.text for chunk in chunks)


def test_llm_rerank_accepts_structured_json_and_falls_back_to_rule_order():
    async def run():
        items = [
            Evidence(source="chunk", text="香蕉是一种黄色水果", score=0.9, payload={"id": "c1", "route": "vector_chunk"}),
            Evidence(source="triple", text="opengauss supports SQL 查询", score=0.2, payload={"id": "t1", "route": "graph_path"}),
        ]

        ranked = await llm_rerank_evidence(_llm_client(), question="openGauss 的 SQL 能力是什么？", items=items, top_k=2)

        assert [item.payload["id"] for item in ranked] == ["t1", "c1"]

    asyncio.run(run())
