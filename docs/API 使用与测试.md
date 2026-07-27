## API 使用与测试

### **/users**：查看/轮换 API Key

```mysql
INSERT INTO users (id, email, username, firstname, lastname, api_key, providers, active);
VALUES ('123e4567-e89b-12d3-a456-426614174000', 'test@example.com', 'testuser', 'Test', 'User', 'cOjLLn804m2nEG09qqtaShNbT732LOPq42q8PB6i', '[]', TRUE);
```

1. **读取 API Key（若尚未生成会为空）**

```shell
curl -s -H "x-api-key:cOjLLn804m2nEGO9qqtaShNbT732LOPq42q8PB6i" http://127.0.0.1:8000/users/api_key | jq
```

测试结果：![image-20250830190633902](image/users1.png)

2. **生成/轮换 API Key**

```shell
curl -s -X POST -H "x-api-key:cOjLLn804m2nEGO9qqtaShNbT732LOPq42q8PB6i" http://127.0.0.1:8000/users/rotate_api_key | jq

export KEY="uJWVnYlz8VAxv2XN6u2iQm7EIwJ4uTCJuUBhDA8Q"
export USER_UUID="12345678-1234-1234-1234-1234567890ab"
```

测试结果：![](image/users2.png)



### **/workspaces**：增删改查工作区

```mysql
INSERT INTO workspaces (id, name, created_by)
VALUES ('00000000-0000-0000-0000-000000000001', 'PG-demo', '12345678-1234-1234-1234-1234567890ab');
```

1. 列出workspace

```shell
# 列表
curl -s -H "x-api-key: $KEY" http://127.0.0.1:8000/workspaces | jq
```

![image-20250831163450868](image/workspaces1.png)

2. 新建一个workspace

```shell
curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"PG-demo","description":"demo ws","user_id":"'"$USER_UUID"'"}' \
  http://127.0.0.1:8000/workspaces | jq
```

![image-20250831164705416](image/workspaces2.png)

3. 按照id查询workspace

```shell
export WS_ID="e39d8628-7d82-4894-9df9-89d475b4d93a"

curl -s -H "x-api-key: $KEY" http://127.0.0.1:8000/workspaces/$WS_ID | jq
```

![image-20250831164954767](image/workspaces3.png)

4. 更新workspace

```shell
curl -s -X PATCH -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"PG-demo-2"}' http://127.0.0.1:8000/workspaces/$WS_ID | jq
```

![image-20250831165611712](image/workspaces4.png)

5. 删除workspace

```shell
curl -s -X DELETE -H "x-api-key: $KEY" http://127.0.0.1:8000/workspaces/$WS_ID
```

![image-20250831171054984](image/workspaces5.png)



### **/schemas**：定义实体/关系抽取规则

1. 列出Schemas

```bash
curl -s -H "x-api-key: $KEY" "http://127.0.0.1:8000/schemas?workspace_id=$WS_ID" | jq
```

![image-20250831172155432](image/schemas1.png)

2. 新建Schemas

```shell
curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  "http://127.0.0.1:8000/schemas?workspace_id=$WS_ID&name=Person" \
  -d '{
        "entities":[{"type":"Person","properties":["name","age"]}],
        "relations":[{"from":"Person","name":"knows","to":"Person"}]
      }' | jq
```

![image-20250831193952222](image/schemas2.png)

3. 读取Schemas

```shell
export SCHEMA_ID="fdc3e9fc-11ee-4240-b518-6506f4fa1692"

curl -s -H "x-api-key: $KEY" "http://127.0.0.1:8000/schemas/$SCHEMA_ID" | jq
```

![image-20250831194208781](image/schemas3.png)

4. 更新Schemas名称

```shell
curl -s -X PUT -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  "http://127.0.0.1:8000/schemas/$SCHEMA_ID?name=PersonV2" -d '{}' | jq
```

![image-20250831194233212](image/schemas4.png)

5. 更新Schemas body

```shell
curl -s -X PUT -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  "http://127.0.0.1:8000/schemas/$SCHEMA_ID" \
  -d '{"relations":[{"from":"Person","name":"works_at","to":"Company"}]}' | jq
```

![image-20250831194305834](image/schemas5.png)

6. 删除Schemas

```shell
curl -s -X DELETE -H "x-api-key: $KEY" "http://127.0.0.1:8000/schemas/$SCHEMA_ID" | jq
```

![image-20250831194324068](image/schemas6.png)

7. LLM 生成

```shell
curl -s -X POST -H "Content-Type: application/json" \
  "http://127.0.0.1:8000/schemas/generate" \
  -d '{"questions":["请为公司-员工关系设计一个图谱schema","人和人之间如何表示社交关系？"]}' | jq
```

![image-20250902161320095](image/schemas7.png)

![image-20250902161335502](image/schemas8.png)



### **/documents**：文档处理

1. 列出documents

```shell
curl -s -H "x-api-key: $KEY" "http://127.0.0.1:8000/documents?limit=10&order=-1" | jq
```

![image-20250902170737596](image/documents1.png)

2. 创建documents

```shell
curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"workspace_id":"'$WS_ID'","title":"doc-1","source":"inline","meta":{"desc":"demo"}}' \
  http://127.0.0.1:8000/documents | jq
```

![image-20250902170836448](image/documents2.png)

3. 更新documents

```shell
export DOC_ID="7580d3f1-2711-4cef-b60e-c25dfdb9424c"

curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"status_value":"processed","errors":[]}' \
  "http://127.0.0.1:8000/documents/$DOC_ID/state" | jq
```

![image-20250906212755586](image/documents3.png)

4. 绑定/解绑documents

```shell
# 需要注意的是由于前面测试了删除workspace，所以前面设置的WS-ID已经不存在了
export WS_ID="00000000-0000-0000-0000-000000000001"

curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  "http://127.0.0.1:8000/documents/assign?workspace_id=$WS_ID" \
  -d '{"document_ids":["'$DOC_ID'"]}' | jq

curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  "http://127.0.0.1:8000/documents/unassign?workspace_id=$WS_ID" \
  -d '{"document_ids":["'$DOC_ID'"]}' | jq
```

![image-20250902172747595](image/documents4.png)



### **/chunks**：切块、嵌入

1. 列出chunks

```shell
# 全部
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/chunks?skip=0&limit=10&populate=true" | jq

# 按 document 过滤
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/chunks?document_id=$DOC_ID" | jq
```

![image-20250902173909800](image/chunks1.png)

![image-20250902173923213](image/chunks2.png)

2. 创建chunks

```shell
curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{
        "chunks_in":[
          {"content":"this is a text chunk","tags":["a","b"],"user_metadata":{"score":0.9}},
          {"content":{"k1":"v1","k2":2},"tags":["x"],"user_metadata":{"note":"obj"}}
        ]
      }' \
  "http://127.0.0.1:8000/chunks?workspace_id=$WS_ID" | jq
```

![image-20250906222040127](image/chunks3.png)

![image-20250906222054692](image/chunks4.png)

![image-20250906222132188](image/chunks5.png)

3. 上传文件自动切块（csv/json/pdf/txt）

```shell
# txt
printf 'Hello\nThis is a txt chunk.\n' |
curl -s -X POST -H "x-api-key: $KEY" \
  -F "file=@-;filename=sample.txt" \
  "http://127.0.0.1:8000/chunks/upload?workspace_id=$WS_ID&document_id=$DOC_ID&extension=txt" | jq
```

![image-20250906213354449](image/chunks6.png)

```shell
# CSV
cat <<'CSV' |
id,name,score
1,Alice,0.9
2,Bob,0.8
CSV
curl -s -X POST -H "x-api-key: $KEY" \
  -F "file=@/tmp/sample.csv;type=text/csv" \
  "http://127.0.0.1:8000/chunks/upload?workspace_id=$WS_ID&document_id=$DOC_ID&extension=csv" | jq
```

![image-20250906222221525](image/chunks7.png)

![image-20250906222244618](image/chunks8.png)

```shell
# JSON
cat > /tmp/data.json <<'JSON'
[
  {"id": 1, "name": "alpha", "score": 0.91},
  {"id": 2, "name": "beta",  "score": 0.83}
]
JSON

curl -s -X POST -H "x-api-key: $KEY" \
  -F "file=@/tmp/data.json;type=application/json" \
  "http://127.0.0.1:8000/chunks/upload?workspace_id=$WS_ID&document_id=$DOC_ID&extension=json" | jq
```

![image-20250906222353112](image/chunks9.png)

![image-20250906222416791](image/chunks10.png)

```shell
# PDF
base64 -d <<'B64' |
JVBERi0xLjQKMSAwIG9iago8PC9UeXBlL1BhZ2UvUGFyZW50IDIgMCBSL1Jlc291cmNlczw8Pj4vTWVkaWFCb3hbMCAw
IDU5NSA4NDJdPj4KZW5kb2JqCjIgMCBvYmoKPDwvVHlwZS9QYWdlcy9LaWRzWzEgMCBSXT4+CmVuZG9iagozIDAgb2Jq
Cjw8L1R5cGUvQ2F0YWxvZy9QYWdlcyAyIDAgUj4+CmVuZG9iagp4cmVmCjAgNAowMDAwMDAwMDAwIDY1NTM1IGYgCjAw
MDAwMDAxMDAgMDAwMDAgbiAKMDAwMDAwMDA1MCAwMDAwMCBuIAowMDAwMDAwMTUwIDAwMDAwIG4gCnRyYWlsZXIKPDwv
Um9vdCAzIDAgUi9TaXplIDQ+PgpzdGFydHhyZWYKMjgwCiUlRU9G
B64
curl -s -X POST -H "x-api-key: $KEY" \
  -F "file=@-;filename=mini.pdf" \
  "http://127.0.0.1:8000/chunks/upload?workspace_id=$WS_ID&document_id=$DOC_ID&extension=pdf" | jq
```

![image-20250906222457595](image/chunks11.png)

4. 绑定/解绑workspace

```shell
# 绑定
curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"chunk_ids":["'"$CHUNK_ID"'"]}' \
  "http://127.0.0.1:8000/chunks/assign?workspace_id=$WS_ID" | jq

# 解绑
curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"chunk_ids":["'"$CHUNK_ID"'"]}' \
  "http://127.0.0.1:8000/chunks/unassign?workspace_id=$WS_ID" | jq
```

![image-20250906224838400](image/chunks12.png)

![image-20250906233332717](image/chunks13.png)

5. 更新 tags / user_metadata

```shell
curl -s -X PATCH \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["a","b","c"], "user_metadata": {"score": 0.95, "note": "updated by API"}}' \
  "http://127.0.0.1:8000/chunks/$CHUNK_ID?workspace_id=$WS_ID" | jq
```

![image-20250906234504910](image/chunks14.png)

![image-20250906234516506](image/chunks15.png)

6. 删除

```shell
curl -s -X DELETE -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/chunks/$CHUNK_ID" | jq
```

![image-20250906234548336](image/chunks16.png)

![image-20250906234559210](image/chunks18.png)



### **/graphs**：构建图谱

1. 用三元组创建图

```shell
export GRAPH_ID=$(uuidgen | tr 'A-Z' 'a-z')
echo $GRAPH_ID

cat <<'JSON' | \
curl -s -X POST \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  -d @- \
  "http://127.0.0.1:8000/graphs/from_triples?graph_id=$GRAPH_ID" | jq
{
  "triples": [
    {
      "head": "Acme Inc",
      "head_type": "company",
      "relation": "employs",
      "tail": "Alice",
      "tail_type": "employee",
      "relation_properties": {"source": "demo-1"}
    },
    {
      "head": "Alice",
      "head_type": "employee",
      "relation": "works_in",
      "tail": "R&D",
      "tail_type": "department",
      "relation_properties": {"source": "demo-2"}
    }
  ]
}
JSON
```

![image-20250907111015974](image/graphs1.png)

![image-20250907111032853](image/graphs2.png)

2. 创建图

```shell
curl -s -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"demo-graph-1","workspace":"'"$WS_ID"'", "schema_id": null}' \
  http://127.0.0.1:8000/graphs | jq
```

![image-20250907120347721](image/graphs3.png)

2. 列出用户的图

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/graphs?limit=20&order=-1" | jq
```

![image-20250907124429246](image/graphs4.png)

3. 读取单个图

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/graphs/$GRAPH_ID" | jq
```

![image-20250907124507766](image/graphs5.png)

4. 列出图的节点

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/graphs/$GRAPH_ID/nodes?created_by=$USER_UUID&skip=0&limit=100&order=-1" | jq
```

![image-20250907130958351](image/graphs6.png)

5. 列出图的关系

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/graphs/$GRAPH_ID/relations?created_by=$USER_UUID&skip=0&limit=100&order=-1" | jq
```

![image-20250907130126206](image/graphs8.png)

6. 删除图

```shell
curl -s -X DELETE -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/graphs/$GRAPH_ID?created_by=$USER_UUID" | jq
```

![image-20250907131439647](image/graphs9.png)



### /nodes：构建节点

1. 创建节点

```shell
cat <<JSON | curl -s -X POST \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  "http://127.0.0.1:8000/nodes" | tee /tmp/node_create.json | jq
{
  "graph": "$GRAPH_ID",
  "name": "Alice",
  "type": "employee",
  "properties": { "email": "alice@example.com", "department": "R&D" },
  "chunks": ["$CHUNK_ID"]
}
JSON
```

![image-20250909170145969](image/nodes1.png)

2. 列表

```shell
export NODE_ID="10df2f58-ec49-4ccc-8ae6-564a4faf297d"
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/nodes?graph_id=$GRAPH_ID&skip=0&limit=50&order=-1" | jq
```

![image-20250909170757183](image/nodes2.png)

3. 读取单个节点

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/nodes/$NODE_ID?graph_id=$GRAPH_ID" | jq
```

![image-20250909170843832](image/nodes3.png)

4. 更换节点

```shell
cat <<JSON | curl -s -X PUT \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  "http://127.0.0.1:8000/nodes/$NODE_ID" | jq
{
  "name": "Alice Chen",
  "type": "person",
  "properties": { "email": "alice.chen@example.com", "department": "R&D", "level": "Senior" },
  "chunks": ["$CHUNK_ID"]
}
JSON
```

![image-20250909170944873](image/nodes4.png)

5. 删除节点

```shell
curl -s -X DELETE -H "x-api-key: $KEY" "http://127.0.0.1:8000/nodes/$NODE_ID" | jq
```

![image-20250909171603427](image/nodes5.png)



### /triples：构建三元组

1. 创建三元组

```shell
cat <<JSON | curl -s -X POST \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  "http://127.0.0.1:8000/triples" | tee /tmp/triple_create_1.json | jq
{
  "graph_id": "$GRAPH_ID",
  "subject": "Alice",
  "predicate": "works_in",
  "object": "R&D"
}
JSON
```

![image-20250909195418170](image/triples1.png)

2. 列出当前图的三元组

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/triples?graph_id=$GRAPH_ID&skip=0&limit=50" \
  | tee /tmp/triple_list.json | jq
```

![image-20250909195502133](image/triples2.png)

3. 更新三元组

   ```shell
   export TRIPLE_ID="0f04fda8-6f0f-439e-b756-68d888c4a6c9"
   ```

   - 修改谓词

   ```shell
   curl -s -X PUT \
     -H "x-api-key: $KEY" \
     -H "Content-Type: application/json" \
     -d '{"predicate":"reports_to"}' \
     "http://127.0.0.1:8000/triples/$TRIPLE_ID" | jq
   ```

   ![image-20250909200952071](image/triples4.png)

   - 合并 properties，并更新 subject

   ```shell
   curl -s -X PUT \
     -H "x-api-key: $KEY" \
     -H "Content-Type: application/json" \
     -d '{"subject":"Alice","properties":{"reason":"org change"}}' \
     "http://127.0.0.1:8000/triples/$TRIPLE_ID" | jq
   ```

   ![image-20250909201043929](image/triples5.png)

   - 覆盖 properties

   ```shell
   curl -s -X PUT \
     -H "x-api-key: $KEY" \
     -H "Content-Type: application/json" \
     -d '{"properties":{"source":"manual-sync"}, "merge_properties": false}' \
     "http://127.0.0.1:8000/triples/$TRIPLE_ID" | jq
   ```

   ![image-20250909201125814](image/triples6.png)

   - 更新 head/tail 节点及 chunks

   ```shell
   curl -s -X PUT \
     -H "x-api-key: $KEY" \
     -H "Content-Type: application/json" \
     -d '{"head_node_id":"'"$NODE_A_ID"'","tail_node_id":"'"$NODE_B_ID"'","chunks":["'"$CHUNK_ID"'"]}' \
     "http://127.0.0.1:8000/triples/$TRIPLE_ID" | jq
   ```

   ![image-20250909201206802](image/triples7.png)

4. 删除三元组

```shell
curl -i -X DELETE -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/triples/$TRIPLE_ID"
```

![image-20250909202146645](image/triples8.png)



### **/rules**：抽取规则维护

1. 创建规则（workspace）

```bash
cat <<'JSON' | curl -s -X POST \
  -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  --data-binary @- \
  "http://127.0.0.1:8000/rules?workspace_id=$WS_ID" | jq
{
  "name": "mask-employee-email",
  "if":   { "field": "label", "eq": "employee" },
  "then": { "action": "mask", "fields": ["email"] },
  "tags": ["demo", "workspace"]
}
JSON
```

![image-20250907150555463](image/rules1.png)

2. 查询规则（workspace）

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/rules?workspace_id=$WS_ID&limit=50&order=-1" | jq
```

![image-20250907150652500](image/rules2.png)

3. 创建规则（graphs）

```shell
# 前面测试的时候graph被删掉了，重新生成了一个
export GRAPH_ID="1cbb9f63-1f48-46ac-a18f-888fb2b364c5"

cat <<'JSON' | \
curl -s -X POST \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  "http://127.0.0.1:8000/rules?graph_id=$GRAPH_ID" | jq
{
  "name": "deny-secret-dept",
  "if":   { "field": "department", "eq": "R&D" },
  "then": { "action": "deny" },
  "tags": ["demo", "graph"]
}
JSON
```

![image-20250907151815165](image/rules3.png)

4. 查询规则（graphs）

```shell
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/rules?graph_id=$GRAPH_ID&skip=0&limit=50&order=-1" | jq
```

![image-20250907151831375](image/rules4.png)

5. 删除规则

```shell
curl -s -X DELETE -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/rules/$RULE_ID" | jq
```

![image-20250907151930745](image/rules5.png)



### **/tasks**：异步任务/执行记录

1. 创建任务

```bash
curl -s -X POST -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/tasks?created_by=$USER_UUID&title=t1&description=d1" | jq
```

![image-20250907130336357](image/tasks1.png)

2. 查询任务

```shell
export TASK_ID="19089051-05d4-465b-918d-cc8c2a06a3c0"
curl -s -H "x-api-key: $KEY" \
  "http://127.0.0.1:8000/tasks/$TASK_ID?created_by=$USER_UUID" | jq
```

![image-20250907130502491](image/task2.png)

