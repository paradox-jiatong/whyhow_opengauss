import asyncio

from whyhow_api.utilities.local_llm import LocalLLMClient
from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.users import BYOOpenAIMetadata
from whyhow_api.utilities.common import embed_texts


def test_local_embeddings_rank_related_text_higher():
    async def run():
        client = LocalLLMClient()

        response = await client.embeddings.create(
            input=[
                "openGauss supports enterprise relational database workloads",
                "bananas are yellow fruit",
                "openGauss provides SQL and transaction capabilities",
            ],
            model="local",
        )

        vectors = [item.embedding for item in response.data]

        def dot(a, b):
            return sum(x * y for x, y in zip(a, b))

        assert dot(vectors[0], vectors[2]) > dot(vectors[0], vectors[1])

    asyncio.run(run())


def test_embed_texts_uses_configured_embedding_dimensions():
    async def run():
        llm_client = LLMClient(
            LocalLLMClient(),
            BYOOpenAIMetadata(embedding_name="local", embedding_dimensions=512),
        )

        vectors = await embed_texts(llm_client, ["openGauss supports vector search"])

        assert len(vectors[0]) == 512

    asyncio.run(run())


def test_local_chat_returns_context_grounded_answer():
    async def run():
        client = LocalLLMClient()

        response = await client.chat.completions.create(
            model="local",
            messages=[
                {"role": "system", "content": "Only answer from context."},
                {"role": "user", "content": "问题：openGauss 的优势是什么？\n\n可用上下文：\n- [0.9] openGauss 支持事务一致性和 SQL 查询。"},
            ],
        )

        assert "openGauss" in response.choices[0].message.content

    asyncio.run(run())
