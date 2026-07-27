"""Graph builders."""

import asyncio
import inspect
import json
import logging
import time
from abc import ABC, abstractmethod
from json.decoder import JSONDecodeError
from typing import Any, List, Optional, Tuple, Dict
import asyncio, json, time, inspect, logging
from json import JSONDecodeError

import logfire
import spacy
import spacy.cli
from openai.types.chat import ChatCompletion

from whyhow_api.dependencies import LLMClient
from whyhow_api.models.common import (
    Entity,
    OpenAICompletionsConfig,
    OpenAIDirectivesConfig,
    SchemaEntity,
    SchemaRelation,
    SchemaTriplePattern,
    TextWithEntities,
    Triple,
    TriplePattern,
)
from whyhow_api.schemas.base import ErrorDetails
from whyhow_api.schemas.chunks import ChunkDocumentModel
from whyhow_api.schemas.schemas import SchemaEntity, SchemaRelation, TriplePattern
from whyhow_api.utilities.config import (
    create_schema_guided_graph_prompt,
    create_zeroshot_graph_prompt,
)
from whyhow_api.models.common import GeneratedSchema



logger = logging.getLogger(__name__)

def _normalize_schema_shape(data: dict[str, Any]) -> tuple[dict[str, list[Any]], list[str]]:
    """把 LLM 返回的 schema 归一化到要求的形状；返回 (fixed_schema, warnings)。"""
    warnings: list[str] = []
    entities = list(data.get("entities") or [])
    relations = list(data.get("relations") or [])
    patterns  = list(data.get("patterns")  or [])

    rel2pair: dict[str, tuple[str, str]] = {}
    for i, p in enumerate(patterns):
        if not isinstance(p, dict): 
            warnings.append(f"patterns[{i}] is not an object, dropped")
            continue
        h = (p.get("head") or "").strip()
        r = (p.get("relation") or "").strip()
        t = (p.get("tail") or "").strip()
        if h and r and t:
            rel2pair.setdefault(r, (h, t))

    fixed_entities: list[dict[str, Any]] = []
    seen_e = set()
    for i, e in enumerate(entities):
        if not isinstance(e, dict):
            warnings.append(f"entities[{i}] is not an object, dropped"); continue
        name = (e.get("name") or "").strip()
        if not name:
            warnings.append(f"entities[{i}] missing name, dropped"); continue
        if name in seen_e: 
            continue
        seen_e.add(name)
        fixed_entities.append({"name": name, "description": e.get("description")})

    fixed_relations: list[dict[str, Any]] = []
    seen_r = set()
    for i, rel in enumerate(relations):
        if not isinstance(rel, dict):
            warnings.append(f"relations[{i}] is not an object, dropped"); continue
        name = (rel.get("name") or "").strip()
        if not name:
            warnings.append(f"relations[{i}] missing name, dropped"); continue
        head = (rel.get("head") or "").strip()
        tail = (rel.get("tail") or "").strip()
        if not (head and tail):
            if name in rel2pair:
                head, tail = rel2pair[name]
            else:
                warnings.append(f"relation '{name}' missing head/tail, dropped"); 
                continue
        if name in seen_r:
            continue
        seen_r.add(name)
        fixed_relations.append({
            "name": name,
            "head": head,
            "tail": tail,
            "description": rel.get("description"),
        })

    fixed_patterns: list[dict[str, Any]] = []
    seen_p = set()
    valid_entity_names = {e["name"] for e in fixed_entities}
    valid_relation_names = {r["name"] for r in fixed_relations}
    for i, p in enumerate(patterns):
        if not isinstance(p, dict): 
            continue
        h = (p.get("head") or "").strip()
        r = (p.get("relation") or "").strip()
        t = (p.get("tail") or "").strip()
        if not (h and r and t):
            continue
        if (h in valid_entity_names and t in valid_entity_names and r in valid_relation_names):
            key = (h, r, t)
            if key in seen_p: 
                continue
            seen_p.add(key)
            fixed_patterns.append({
                "head": h, "relation": r, "tail": t, "description": p.get("description")
            })
    return {"entities": fixed_entities, "relations": fixed_relations, "patterns": fixed_patterns}, warnings


class TextEntityExtractor(ABC):
    """Text entity extractor interface."""

    @abstractmethod
    def load(self) -> None:
        """Load the underlying model necessary for entity extraction."""

    @abstractmethod
    def extract_entities(self, text: str) -> TextWithEntities:
        """
        Extract entities from the given text.

        Parameters
        ----------
        text : str
            The input text from which to extract entities.

        Returns
        -------
        TextWithEntities
            A data structure containing the original text and the extracted entities.
        """


class SpacyEntityExtractor(TextEntityExtractor):
    """A text entity extractor based on spaCy's named entity recognition (NER)."""

    def __init__(
        self,
        model_name: str = "en_core_web_sm",
        model_disables: List[str] = ["parser", "lemmatizer"],
    ):
        self.model_name = model_name
        self.model_disables = model_disables
        self.model: spacy.language.Language | None = None

    def download_and_load_spacy_model(self) -> spacy.language.Language:
        """Attempt to load a spaCy model by its name.

        If the model is not found, it tries to download it
        and then loads it. Additional keyword arguments can be passed to specify which pipeline components
        to disable or for other loading options.

        Returns
        -------
        Language
            The loaded spaCy model.
        """
        try:
            return spacy.load(
                name=self.model_name, disable=self.model_disables
            )
        except OSError:
            print(f"{self.model_name} model not found. Downloading...")
            spacy.cli.download(self.model_name)
            return spacy.load(
                name=self.model_name, disable=self.model_disables
            )

    def load(self) -> None:
        """Load the spaCy model specified by model_name."""
        self.model = self.download_and_load_spacy_model()

    def extract_entities(self, text: str) -> TextWithEntities:
        """Extract entities using the loaded spaCy model."""
        if self.model is None:
            self.load()
        doc = self.model(text)  # type: ignore[misc]
        entities = [
            Entity(text=ent.text, label=ent.label_) for ent in doc.ents
        ]
        return TextWithEntities(text=text, entities=entities)
    

class OpenAIBuilder:
    """OpenAI API builder."""

    def __init__(
        self,
        llm_client: LLMClient,
        seed_entity_extractor: type[TextEntityExtractor],
    ):
        self.llm_client = llm_client
        self.seed_entity_extractor = seed_entity_extractor()

    # @backoff.on_exception(
    #     backoff.expo, openai.RateLimitError, max_time=60, max_tries=5
    # )
    @staticmethod
    async def fetch_triples(
        llm_client: LLMClient,
        chunk: ChunkDocumentModel,
        pattern: SchemaTriplePattern,
        completions_config: OpenAICompletionsConfig,
        # **kwargs,
    ) -> List[Triple] | None:
        """Fetch triples based on a schema pattern using the OpenAI API."""
        _content_str = create_schema_guided_graph_prompt(
            text=chunk.content, pattern=pattern
        )

        # Logfire trace of LLM client
        logfire.instrument_openai(llm_client.client)

        response = None
        try:
            if llm_client.metadata.language_model_name:
                completions_config.model = (
                    llm_client.metadata.language_model_name
                )
            response = await llm_client.client.chat.completions.create(
                messages=[{"role": "system", "content": _content_str}],
                **completions_config.model_dump(),
                # **kwargs,
            )
        except Exception as e:
            logger.error(f"Failed to fetch triple: {e}")

        if response is not None:
            message_content = response.choices[0].message.content
        else:
            return None

        res_entities = []
        try:
            response_text = response.choices[0].message.content.strip()

            if response_text.startswith("```") and response_text.endswith(
                "```"
            ):
                response_text = response_text[7:-3].strip()

            res_entities = json.loads(response_text)

        except JSONDecodeError as je:
            logger.error(
                f"Failed to parse message content - {je}: {message_content}"
            )
        except Exception as e:
            logger.error(f"Unexpected error parsing message content: {e}")

        if isinstance(res_entities, list):
            triples = []
            for entities in res_entities:
                head, tail = entities
                triples.append(
                    Triple(
                        head=head,
                        head_type=pattern.head.name,
                        head_properties={"chunks": [chunk.id]},
                        relation=pattern.relation.name,
                        relation_properties={"chunks": [chunk.id]},
                        tail=tail,
                        tail_type=pattern.tail.name,
                        tail_properties={"chunks": [chunk.id]},
                    )
                )
        return triples

    @staticmethod
    def parse_response_into_triples(response: ChatCompletion) -> List[Triple]:
        """Parse OpenAI response.

        Parse the response from the OpenAI API into a list
        of Triple objects.
        """
        try:
            if response is None or response.choices is None:
                return []
            message_content = response.choices[0].message.content
            if message_content is None:
                return []
            res_triples = json.loads(message_content)
            if not isinstance(res_triples, list):
                return []
            return [
                Triple(
                    head=t[0],
                    relation=t[1],
                    tail=t[2],
                )
                for t in (
                    t.strip().split(",")
                    for t in res_triples
                    if isinstance(t, str) and t.count(",") == 2
                )
            ]
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from response: {e}")
            return []
        except Exception as e:
            logger.error(f"Error processing response into triples: {e}")
            return []

    @staticmethod
    async def extract_zeroshot_triples(
        llm_client: LLMClient,
        chunk: ChunkDocumentModel,
        prompt: str,
        completions_config: OpenAICompletionsConfig,
    ) -> List[Triple] | None:
        """Extract triples using zero-shot prompts."""
        # Logfire trace of LLM client
        logfire.instrument_openai(llm_client.client)

        if llm_client.metadata.language_model_name:
            completions_config.model = llm_client.metadata.language_model_name
        extract_triples_response = (
            await llm_client.client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": create_zeroshot_graph_prompt(
                            text=chunk.content, context=prompt
                        ),
                    }
                ],
                **completions_config.model_dump(),
            )
        )
        # logger.info(f"extract_triples_response: {extract_triples_response}")
        return OpenAIBuilder.parse_response_into_triples(
            response=extract_triples_response
        )

    @staticmethod
    async def extract_triples(
        llm_client: LLMClient,
        chunk: ChunkDocumentModel,
        completions_config: OpenAICompletionsConfig,
        patterns: List[SchemaTriplePattern],
    ) -> Optional[list[Triple]]:
        """
        Extract triples.

        Extracts triples from a text chunk using patterns, processing them in parallel.

        Parameters
        ----------
        llm_client : LLMClient
            The OpenAI or Azure OpenAI client instance.
        chunk : ChunkDocumentModel
            The chunk document model for triple extraction.
        completions_config : OpenAICompletionsConfig
            Configuration for OpenAI completion requests.
        patterns : List[SchemaTriplePattern] | None, optional
            A list of schema triple patterns for guided triple
            extraction, defaults to None.

        Returns
        -------
        List[Triple] | None
            A list of extracted triples if successful, or None
            if an error occurs.

        Raises
        ------
        ValueError
            If neither prompts nor patterns are provided, indicating
            that the necessary parameters for extraction are missing.
        """
        try:

            # Logfire trace of LLM client
            logfire.instrument_openai(llm_client.client)

            task_list = []

            if patterns:
                task_source: List[SchemaTriplePattern] = patterns
                extractor = OpenAIBuilder.fetch_triples
            else:
                raise ValueError("Patterns must be provided for extraction")

            # Generate tasks
            for item in task_source:
                task = extractor(
                    llm_client,
                    chunk,
                    item,
                    completions_config,
                )
                task_list.append(task)

            # Gather tasks and aggregate results
            triple_results = await asyncio.gather(*task_list)

            # Ensure triple_results does not contain None
            triples = [
                item
                for sublist in triple_results
                if sublist is not None
                for item in sublist
            ]

            return triples

        except Exception as e:
            logger.error(f"Failed to extract triples: {e}")
            return None


    async def improve_entities_matching(
        self,
        extracted_entities: List[str],
        graph_entities: List[str],
        user_query: str,
        matched_entities: List[str],
        directives: OpenAIDirectivesConfig,
        completions_config: OpenAICompletionsConfig,
    ) -> List[str]:
        """
        Improves the matching of entities using the OpenAI API.

        This function formats a prompt using the provided entities and user query, sends the prompt to the OpenAI API,
        and returns the improved entities from the response.

        Parameters
        ----------
        extracted_entities : List[str]
            A list of entities extracted from the user's query.
        graph_entities : List[str]
            A list of entities in the graph.
        user_query : str
            The user's query.
        matched_entities : List[str]
            A list of entities that were matched in the graph.
        directives : OpenAIDirectivesConfig
            Configuration for OpenAI directives.
        completions_config : OpenAICompletionsConfig
            Configuration for OpenAI completions.

        Returns
        -------
        List[str]
            The improved entities from the response from the OpenAI API.

        Raises
        ------
        json.decoder.JSONDecodeError
            If the response from the OpenAI API cannot be parsed as JSON.
        """
        prompt = directives.improve_matched_entities.format(
            extracted_entities=json.dumps(extracted_entities, indent=2),
            graph_entities=json.dumps(graph_entities, indent=2),
            user_query=user_query,
            matched_entities=json.dumps(matched_entities, indent=2),
        )

        if self.llm_client.metadata.language_model_name:
            completions_config.model = (
                self.llm_client.metadata.language_model_name
            )
        response = await self.llm_client.client.chat.completions.create(
            messages=[{"role": "system", "content": prompt}],
            **completions_config.model_dump(),
        )

        formatted_response = response.choices[0].message.content.strip()

        formatted_response = formatted_response.replace(
            "```json\n", ""
        ).replace("\n```", "")

        try:
            improved_entities = json.loads(formatted_response)
        except json.decoder.JSONDecodeError:
            print("Failed to parse formatted response as JSON")
            improved_entities = []

        return improved_entities

    async def improve_relations_matching(
        self,
        extracted_relations: List[str],
        graph_relations: List[str],
        user_query: str,
        matched_relations: List[str],
        directives: OpenAIDirectivesConfig,
        completions_config: OpenAICompletionsConfig,
    ) -> List[str]:
        """
        Improves the matching of relations using the OpenAI API.

        This function formats a prompt using the provided relations and user query, sends the prompt to the OpenAI API,
        and returns the improved relations from the response.

        Parameters
        ----------
        extracted_relations : List[str]
            A list of relations extracted from the user's query.
        graph_relations : List[str]
            A list of relations in the graph.
        user_query : str
            The user's query.
        matched_relations : List[str]
            A list of relations that were matched in the graph.
        directives : OpenAIDirectivesConfig
            Configuration for OpenAI directives.
        completions_config : OpenAICompletionsConfig
            Configuration for OpenAI completions.

        Returns
        -------
        List[str]
            The improved relations from the response from the OpenAI API.

        Raises
        ------
        json.decoder.JSONDecodeError
            If the response from the OpenAI API cannot be parsed as JSON.
        """
        prompt = directives.improve_matched_relations.format(
            extracted_relations=json.dumps(extracted_relations, indent=2),
            graph_relations=json.dumps(graph_relations, indent=2),
            user_query=user_query,
            matched_relations=json.dumps(matched_relations, indent=2),
        )

        if self.llm_client.metadata.language_model_name:
            completions_config.model = (
                self.llm_client.metadata.language_model_name
            )
        response = await self.llm_client.client.chat.completions.create(
            messages=[{"role": "system", "content": prompt}],
            **completions_config.model_dump(),
        )

        formatted_response = response.choices[0].message.content.strip()

        formatted_response = formatted_response.replace(
            "```json\n", ""
        ).replace("\n```", "")

        try:
            improved_relations = json.loads(formatted_response)
        except json.decoder.JSONDecodeError:
            print("Failed to parse formatted response as JSON")
            improved_relations = []

        return improved_relations
    
    @staticmethod
    async def generate_schema(
        llm_client, questions: List[str]
    ) -> Tuple[GeneratedSchema, list[ErrorDetails]]:
        """
        从问题生成图谱 schema（加强版）：
        - 明确的 system+user 消息
        - response_format 强制 JSON
        - tools（函数调用）兜底
        - 超时与重试
        """
        service_start_time = time.time()
        errors: list[ErrorDetails] = []

        contract_text = """
        你必须只返回一个 JSON 对象，结构严格为：
        {
        "entities": [
            {"name": "company", "description": "一句抽象定义"},
            {"name": "employee", "description": "一句抽象定义"}
        ],
        "relations": [
            {"name": "employs", "description": "一句抽象定义（动词短语）"}
        ],
        "patterns": [
            {"head": "company", "relation": "employs", "tail": "employee", "description": "一句说明"}
        ]
        }

        规则：
        - 实体与关系名全部小写、用空格分词（不得包含下划线或驼峰）。
        - description 必填，且一句话抽象定义。
        - patterns 的 head/tail 必须引用到上面 entities/relations 已出现过的 name。
        - 不要返回额外文字、不要 markdown 代码块。
        """

        tools = [{
            "type": "function",
            "function": {
                "name": "emit_schema",
                "description": "Emit a knowledge graph schema as a single JSON object.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entities": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["name", "description"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "description": {"type": "string"}
                                }
                            }
                        },
                        "relations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["name", "description"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "description": {"type": "string"}
                                }
                            }
                        },
                        "patterns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["head", "relation", "tail", "description"],
                                "properties": {
                                    "head": {"type": "string"},
                                    "relation": {"type": "string"},
                                    "tail": {"type": "string"},
                                    "description": {"type": "string"}
                                }
                            }
                        }
                    },
                    "required": ["entities", "relations", "patterns"]
                }
            }
        }]

        async def call_once(prompt: str) -> dict | None:
            """
            单次调用，强制 JSON；若返回 tool_calls 则解析函数参数；
            失败时返回 None。
            """
            msgs = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert KG schema designer. "
                        "Always return a single JSON object that matches the contract."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt + "\n\n" + contract_text,
                },
            ]
            try:
                resp = await llm_client.client.chat.completions.create(
                    model=(llm_client.metadata.language_model_name or "gpt-4o-mini"),
                    messages=msgs,
                    temperature=0.1,
                    max_tokens=1200,
                    response_format={"type": "json_object"},
                    tools=tools,
                    tool_choice="auto",
                    timeout=60,
                )
            except Exception as e:
                logger.error(f"LLM request failed: {e}")
                return None

            choice = resp.choices[0].message

            try:
                if getattr(choice, "tool_calls", None):
                    args = choice.tool_calls[0].function.arguments
                    return json.loads(args)
            except Exception as e:
                logger.warning(f"Failed to parse tool_calls: {e}")

            content = (choice.content or "").strip()
            if not content:
                return None
            if content.startswith("```"):
                content = content.strip("` \n")
                if content.lower().startswith("json"):
                    content = content[4:].lstrip()
            try:
                return json.loads(content)
            except JSONDecodeError as je:
                logger.error(f"JSON decode error: {je}: {content[:300]}")
                return None

        async def call_with_retry(prompt: str) -> dict | None:
            data = await call_once(prompt)
            if data is not None:
                return data
            short_prompt = "只根据下面问题生成契约规定的 JSON（不要任何额外文本）：\n" + prompt
            return await call_once(short_prompt)

        async def per_question(idx: int, q: str) -> dict | None:
            logger.info(f'Extracting schema for question: "{q}"')
            others = questions[:idx] + questions[idx + 1 :]
            prompt = (
                f"Primary question: {q}\n"
                f"Secondary questions (context only): {others}\n"
                "抽象设计：尽量给出通用实体与动词关系。"
            )
            return await call_with_retry(prompt)

        try:
            json_schemas = await asyncio.gather(
                *[per_question(i, q) for i, q in enumerate(questions)]
            )
            json_schemas = [s for s in json_schemas if s]

            if not json_schemas:
                raise RuntimeError("No schema returned from LLM.")

            merged: Dict[str, list] = {"entities": [], "relations": [], "patterns": []}
            seen_e, seen_r, seen_p = set(), set(), set()

            for s in json_schemas:
                for e in s.get("entities", []):
                    n = (e.get("name") or "").strip()
                    if n and n not in seen_e:
                        merged["entities"].append({"name": n, "description": e.get("description", "")})
                        seen_e.add(n)
                for r in s.get("relations", []):
                    n = (r.get("name") or "").strip()
                    if n and n not in seen_r:
                        merged["relations"].append({"name": n, "description": r.get("description", "")})
                        seen_r.add(n)

            valid_e = set(seen_e)
            valid_r = set(seen_r)

            for s in json_schemas:
                for p in s.get("patterns", []):
                    head = (p.get("head") or "").strip()
                    rel  = (p.get("relation") or "").strip()
                    tail = (p.get("tail") or "").strip()
                    if head in valid_e and rel in valid_r and tail in valid_e:
                        key = (head, rel, tail)
                        if key not in seen_p:
                            merged["patterns"].append({
                                "head": head, "relation": rel, "tail": tail,
                                "description": p.get("description", "")
                            })
                            seen_p.add(key)

            try:
                schema_obj = GeneratedSchema(**merged)
            except Exception as e:
                logger.info(f"Unable to parse generated schema - {e}: {merged}")
                raise

            logger.info("Schema generated in %.3fs", time.time() - service_start_time)
            return schema_obj, errors

        except Exception as e:
            logger.error(f"Failed to generate schema: {e}")
            errors.append(ErrorDetails(
                message="Failed to generate schema. Please try again or reformat your questions.",
                level="critical",
            ))
            return GeneratedSchema(entities=[], relations=[], patterns=[]), errors
