## openGauss向量数据库对接知识工程最佳实践

WhyHow 与openGauss向量数据库适配，包括简单知识文档导入、对接大模型完成RAG端到端验证。

本项目旨在实现：
- 灵活的数据模型: 将 WhyHow 的知识图谱数据模型对接 openGauss，充分利用其关系型和图数据库的双重能力。
- 端到端 RAG: 结合主流的嵌入模型和大型语言模型（LLMs），实现从文档摄取、知识抽取、图谱构建到智能问答的完整 RAG 工作流。

### 下载与部署
#### 1. 所需环境
- 2vCPUs | 4GiB | s7.large.2 CentOS 7.6 64bit
- Docker / Docker Compose
- Python 3.11（强烈建议用conda创建虚拟环境）
- openGauss 3.x（docker部署）
- OpenAI API Key（用于 Embedding/LLM）

#### 2. 下载代码
```shell
git clone https://gitcode.com/paradox/whyhow_opengauss.git
cd knowledge-graph-studio
pip install -r requirements.txt
```
#### 3. openGauss 部署
1. 拉取docker镜像

```shell
docker pull enmotech/opengauss:latest
```

Tips：建议服务器上面挂个代理，或者本地拉取（我本地有代理）导入服务器

2. 创建opengauss容器并启动

```shell
docker run -d --name opengauss \
  -e GS_PASSWORD='Enmo@123' \
  -e GAUSSHOME=/usr/local/opengauss \
  -e LD_LIBRARY_PATH=/usr/local/opengauss/lib \
  -e PATH=/usr/local/opengauss/bin:$PATH \
  -p 5432:5432 enmotech/opengauss:3.1.0
```

后续开机只需要启动就好

```shell
docker start opengauss
```

3. 创建表，这里用的gsql

```shell
export GAUSSHOME=/usr/local/opengauss
export LD_LIBRARY_PATH=$GAUSSHOME/lib:$LD_LIBRARY_PATH
export PATH=$GAUSSHOME/bin:$PATH
gsql -d postgres -U gaussdb -W Enmo@123
```

#### 4. 配置环境变量 

```shell
cp .env.sample .env

WHYHOW__EMBEDDING__OPENAI__API_KEY=<你的openai api key>
WHYHOW__GENERATIVE__OPENAI__API_KEY=<你的openai api key>

WHYHOW__OPENGAUSS__HOST=<数据库的host>
WHYHOW__OPENGAUSS__PORT=<数据库docker映射出来的端口>5432
WHYHOW__OPENGAUSS__DATABASE=<数据库名称>
WHYHOW__OPENGAUSS__USER=<数据库用户名>
WHYHOW__OPENGAUSS__PASSWORD=<数据库密码>
WHYHOW__OPENGAUSS__ECHO_SQL=<是否打印 SQL 语句>

# e.g.
# WHYHOW__OPENGAUSS__HOST=127.0.0.1
# WHYHOW__OPENGAUSS__PORT=5432
# WHYHOW__OPENGAUSS__DATABASE=postgres
# WHYHOW__OPENGAUSS__USER=gaussdb
# WHYHOW__OPENGAUSS__PASSWORD=Enmo@123
# WHYHOW__OPENGAUSS__ECHO_SQL=true
```

#### 5. 创建数据表（见适配文档）

#### 6. 启动API

当所有的配置完成，就可以启动API服务器开始服务：

```shell
uvicorn whyhow_api.main:app --host 0.0.0.0 --port 8000 --reload
```


### Quickstart

#### 1. 健康检查
```shell
curl -s -H "x-api-key: $KEY" "http://127.0.0.1:8000/db" | jq
```
#### 2. 创建工作区

```shell
cat <<JSON | curl -s -X POST \
  -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  --data-binary @- "http://127.0.0.1:8000/workspaces" | jq
{
  "name": "PG-demo",
  "description": "demo ws"
}
JSON

export WS_ID=xxx
```
#### 3. 创建chunks

```shell
cat >/tmp/chunks_more.json <<'JSON'
{
  "chunks_in": [
    {
      "content": "openGauss 是企业级开源数据库，兼容 PostgreSQL，具备高可用与高性能。",
      "tags": ["db","opengauss"],
      "user_metadata": { "lang": "zh" }
    },
    {
      "content": "WhyHow 支持文档分块、图谱抽取与 RAG 检索问答，便于企业知识应用。",
      "tags": ["whyhow","rag"],
      "user_metadata": { "lang": "zh" }
    }
  ]
}
JSON

curl -s -X POST \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/chunks_more.json \
  "http://127.0.0.1:8000/chunks?workspace_id=$WS_ID" | jq
  ```
  
  #### 4. RAG查询
  
  ```shell
  curl -s -G -H "x-api-key: $KEY" \
  --data-urlencode "workspace_id=$WS_ID" \
  --data-urlencode "text=openGauss 的优势是什么？" \
  --data-urlencode "top_k=5" \
  "http://127.0.0.1:8000/queries/rag" \
| jq '{answer, retrieved:(.top_chunks // [])}'
  ```