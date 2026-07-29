## Python 脚本说明

本目录介绍 `scripts/` 下常用 Python 脚本的用途、运行前置条件和典型命令。脚本主要覆盖本地初始化、RAG/GraphRAG 演示、评测数据集生成与检索效果评估。

### 1. 运行前置条件

建议先完成以下准备：

```shell
uv venv --python 3.11 .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements-demo.txt
cp .env.sample .env
```

如果使用 OpenAI Embedding / LLM，请在 `.env` 中配置：

```shell
WHYHOW__EMBEDDING__OPENAI__API_KEY=<your_openai_api_key>
WHYHOW__GENERATIVE__OPENAI__API_KEY=<your_openai_api_key>
```

如果没有配置 OpenAI API Key，本地 demo 会使用确定性的 local provider，以便离线跑通接口、建图和检索流程。

本地 openGauss Lite 示例：

```shell
docker run -d --name whyhow-opengauss \
  -e GS_PASSWORD='Enmo@123' \
  -p 5432:5432 \
  swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/enmotech/opengauss-lite:latest
```

### 2. 初始化数据库

#### `scripts/init_db.py`

用途：

- 创建 users、workspaces、documents、chunks、graphs、nodes、triples、queries 等表。
- 初始化向量字段和图谱相关字段。
- 创建数据库内 cosine distance 函数，用于本地向量相似度排序。

运行：

```shell
.venv/bin/python scripts/init_db.py
```

说明：

- 当前本地 demo 使用 `FLOAT8[]` 存储向量，并通过数据库函数计算余弦距离。
- 如果生产环境接入 openGauss 原生向量扩展或 pgvector，可以替换为索引化向量检索实现。

### 3. 基础 RAG Demo

#### `scripts/run_demo.py`

用途：

- 创建测试用户和工作区。
- 写入示例 Chunk。
- 执行基础 RAG 查询。
- 验证 Chunk 写入、Embedding、Top-K 召回和问答接口是否可用。

运行前需要先启动 API：

```shell
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

再运行：

```shell
.venv/bin/python scripts/run_demo.py
```

适用场景：

- 快速验证服务是否连通。
- 验证 `/chunks`、`/queries/rag` 等基础链路。

### 4. GraphRAG Demo

#### `scripts/run_graphrag_demo.py`

用途：

- 创建用户、工作区和 Schema。
- 写入示例文档 Chunk。
- 基于 Schema 从 Chunk 中抽取 nodes / triples。
- 执行实体归一、Alias 映射和来源 Chunk 合并。
- 将节点和三元组写入 openGauss 图谱表。
- 验证四路粗召回：
  - `vector_chunk`
  - `keyword_chunk`
  - `predicate_chunk`
  - `graph_path`
- 验证 1-hop / 2-hop 图路径召回和结构化 LLM rerank。

运行：

```shell
.venv/bin/python scripts/run_graphrag_demo.py
```

适用场景：

- 验证 GraphRAG 主链路是否可运行。
- 检查 Schema-guided 抽取、图谱持久化和混合检索是否正常。

### 5. 生成评测数据集

#### `scripts/generate_ops_eval_dataset.py`

用途：

- 生成数据库运维主题的 Markdown 文档。
- 生成稳定 Chunk manifest。
- 生成图谱三元组标注。
- 生成 200 条 QA 评测样本。

输出文件：

```text
eval/docs/*.md
eval/chunks_manifest.jsonl
eval/gold_graph.jsonl
eval/ops_qa_200.jsonl
```

数据集主题包括：

- 慢查询
- 锁等待
- 备份恢复
- 向量检索
- 执行计划
- 内存配置
- 连接池
- 表维护

运行：

```shell
.venv/bin/python scripts/generate_ops_eval_dataset.py
```

说明：

- `eval/chunks_manifest.jsonl` 记录由原始文档切分得到的 Chunk。
- `eval/ops_qa_200.jsonl` 默认包含 80 条 vector、40 条 keyword、40 条 predicate、40 条 graph-path 问题。

### 6. 加载评测数据

#### `scripts/load_eval_dataset.py`

用途：

- 读取 `eval/docs/*.md` 和 `eval/chunks_manifest.jsonl`。
- 创建评测用户、工作区、Schema 和图谱。
- 写入 Chunk、Embedding、nodes、triples。
- 生成运行时 manifest，用于后续评测脚本定位 graph_id、workspace_id、chunk_id 等运行时 ID。

运行前需要先启动 API：

```shell
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

再运行：

```shell
.venv/bin/python scripts/load_eval_dataset.py
```

输出文件：

```text
eval/run_manifest.json
```

说明：

- `eval/run_manifest.json` 是本地运行产物，包含当前数据库中的运行时 ID，不建议提交到代码仓库。

### 7. 检索评测

#### `scripts/eval_retrieval.py`

用途：

- 读取 `eval/ops_qa_200.jsonl` 和 `eval/run_manifest.json`。
- 调用本地 API 执行 GraphRAG 查询。
- 计算 Hit@K、Recall@K、MRR@K 和 P50/P95 延迟。
- 支持按 routes 参数执行单路召回评测。

常用命令：

```shell
# 只跑 1 条样本，快速 smoke test
.venv/bin/python scripts/eval_retrieval.py --k 5 --limit 1 --no-answer

# 跑完整 200 条样本
.venv/bin/python scripts/eval_retrieval.py --k 5 --no-answer

# 只评估向量召回
.venv/bin/python scripts/eval_retrieval.py --routes vector_chunk --no-answer

# 只评估 BM25 关键词召回
.venv/bin/python scripts/eval_retrieval.py --routes keyword_chunk --no-answer

# 只评估 SQL 谓词过滤召回
.venv/bin/python scripts/eval_retrieval.py --routes predicate_chunk --no-answer

# 只评估图路径召回
.venv/bin/python scripts/eval_retrieval.py --routes graph_path --no-answer
```

输出文件：

```text
eval/eval_results.json
```

评测口径：

- 主任务以答案来源 Chunk 为评测对象，统计 Chunk Hit@K、Recall@K 和 MRR@K。
- GraphRAG 附加能力单独统计 Triple / Path 结构化证据召回。
- `--no-answer` 表示跳过最终答案生成，只评估检索和 rerank，更适合快速复现实验。

### 8. 消融实验

#### `scripts/run_ablation_eval.py`

用途：

- 依次运行五组评测：
  - Hybrid 四路混合召回
  - Vector only
  - BM25 Keyword only
  - Predicate only
  - Graph Path only
- 汇总每组 Hit@K、Recall@K、MRR@K 和延迟。

运行：

```shell
# 跑 20 条样本
.venv/bin/python scripts/run_ablation_eval.py --limit 20

# 跑完整 200 条样本
.venv/bin/python scripts/run_ablation_eval.py
```

输出文件：

```text
eval/ablation_hybrid_200_no_answer.json
eval/ablation_vector_200_no_answer.json
eval/ablation_keyword_200_no_answer.json
eval/ablation_predicate_200_no_answer.json
eval/ablation_graph_path_200_no_answer.json
eval/ablation_summary_200_no_answer.json
```

说明：

- 本地 openGauss Lite 环境中，Graph Path 和 Hybrid 路线会执行 triple 向量召回，可能较慢。
- 如果只想验证代码是否可用，建议先使用 `--limit 1` 或 `--limit 20`。
- 评测输出属于实验产物，默认不建议提交到代码仓库。

### 9. 推荐运行顺序

首次本地复现建议按以下顺序：

```shell
.venv/bin/python scripts/init_db.py
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
.venv/bin/python scripts/run_demo.py
.venv/bin/python scripts/run_graphrag_demo.py
.venv/bin/python scripts/generate_ops_eval_dataset.py
.venv/bin/python scripts/load_eval_dataset.py
.venv/bin/python scripts/eval_retrieval.py --k 5 --limit 1 --no-answer
```

确认 smoke test 通过后，再运行完整评测：

```shell
.venv/bin/python scripts/run_ablation_eval.py
```

### 10. 常见问题

#### 没有 OpenAI API Key 能跑吗？

可以。基础 demo 和 GraphRAG demo 会使用 deterministic local provider 跑通链路。但如果要评估真实模型效果，建议配置 OpenAI API Key。

#### 为什么 Graph Path / Hybrid 比较慢？

本地 demo 使用 `FLOAT8[]` 数组存储向量，并通过数据库函数计算相似度。Graph Path 还会执行 triple 向量召回，所以会比单路 BM25 或 predicate 慢。

#### 评测结果为什么不提交？

评测 JSON 和日志是本地实验产物，和当前数据库 ID、运行环境、API Key 配置有关，不适合作为通用代码提交。

#### `eval/run_manifest.json` 是什么？

它记录评测数据加载后生成的运行时 ID，例如 user_id、workspace_id、graph_id 和 chunk_id 映射。该文件和本地数据库状态绑定，通常不提交。
