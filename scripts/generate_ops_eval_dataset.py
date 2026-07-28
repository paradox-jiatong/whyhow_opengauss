"""Generate the database-ops GraphRAG evaluation dataset."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"
DOCS_DIR = EVAL_DIR / "docs"


TOPICS = [
    {
        "doc_id": "slow_query",
        "module": "performance",
        "tag": "slow_query",
        "title": "慢查询排查",
        "seed": "慢查询",
        "supports": ["执行计划分析", "索引检查", "统计信息刷新", "SQL 改写"],
        "causes": [("缺少索引", "全表扫描"), ("统计信息过期", "执行计划不准"), ("锁等待", "响应时间增加"), ("work_mem 过小", "排序落盘")],
        "keywords": ["EXPLAIN", "ANALYZE", "seq scan", "work_mem"],
    },
    {
        "doc_id": "lock_wait",
        "module": "concurrency",
        "tag": "lock_wait",
        "title": "锁等待排查",
        "seed": "锁等待",
        "supports": ["pg_locks 查询", "pg_stat_activity 分析", "阻塞会话定位", "事务超时控制"],
        "causes": [("长事务", "锁释放延迟"), ("DDL 操作", "表级锁竞争"), ("热点更新", "行锁冲突"), ("未提交事务", "会话阻塞")],
        "keywords": ["pg_locks", "pg_stat_activity", "locktype", "wait_event"],
    },
    {
        "doc_id": "backup_restore",
        "module": "reliability",
        "tag": "backup",
        "title": "备份恢复",
        "seed": "备份恢复",
        "supports": ["全量备份", "增量备份", "时间点恢复", "备份校验"],
        "causes": [("归档日志缺失", "时间点恢复失败"), ("备份文件损坏", "恢复中断"), ("权限不足", "备份任务失败"), ("存储空间不足", "归档失败")],
        "keywords": ["gs_basebackup", "WAL", "PITR", "archive_mode"],
    },
    {
        "doc_id": "vector_index",
        "module": "vector",
        "tag": "vector_index",
        "title": "向量检索与索引",
        "seed": "向量检索",
        "supports": ["Embedding 入库", "Top-K 相似度查询", "向量距离计算", "元数据过滤"],
        "causes": [("维度过高", "计算开销增加"), ("过滤条件过宽", "候选集变大"), ("索引缺失", "召回延迟增加"), ("向量未归一", "相似度偏差")],
        "keywords": ["embedding", "Top-K", "cosine", "metadata"],
    },
    {
        "doc_id": "sql_plan",
        "module": "optimizer",
        "tag": "sql_plan",
        "title": "执行计划与优化器",
        "seed": "执行计划",
        "supports": ["Join 顺序分析", "谓词下推", "索引选择", "代价估算"],
        "causes": [("统计信息不准确", "错误 Join 顺序"), ("函数包裹列", "索引失效"), ("隐式类型转换", "过滤条件失效"), ("返回行估计偏差", "计划选择错误")],
        "keywords": ["nested loop", "hash join", "predicate pushdown", "cost"],
    },
    {
        "doc_id": "memory_config",
        "module": "configuration",
        "tag": "memory",
        "title": "内存参数配置",
        "seed": "内存配置",
        "supports": ["work_mem 调整", "shared_buffers 配置", "排序内存控制", "Hash Join 内存控制"],
        "causes": [("work_mem 过小", "排序落盘"), ("shared_buffers 过低", "缓存命中下降"), ("并发过高", "内存争用"), ("Hash 表过大", "临时文件增加")],
        "keywords": ["work_mem", "shared_buffers", "temp file", "hash join"],
    },
    {
        "doc_id": "connection_pool",
        "module": "connection",
        "tag": "connection",
        "title": "连接数与连接池",
        "seed": "连接池",
        "supports": ["最大连接数控制", "空闲连接回收", "连接复用", "会话超时配置"],
        "causes": [("连接泄漏", "连接数耗尽"), ("池大小过小", "请求排队"), ("空闲事务", "锁资源占用"), ("认证失败", "连接建立失败")],
        "keywords": ["max_connections", "idle timeout", "pool size", "session"],
    },
    {
        "doc_id": "vacuum_stats",
        "module": "maintenance",
        "tag": "vacuum",
        "title": "统计信息与膨胀治理",
        "seed": "表维护",
        "supports": ["统计信息采集", "垃圾回收", "膨胀检查", "自动维护任务"],
        "causes": [("统计信息过期", "执行计划不准"), ("死元组过多", "扫描成本增加"), ("自动维护关闭", "表膨胀"), ("高频更新", "索引膨胀")],
        "keywords": ["ANALYZE", "VACUUM", "dead tuples", "bloat"],
    },
]


def _doc_text(topic: dict) -> str:
    supports = "\n".join(f"- {topic['seed']} 支持 {item}。" for item in topic["supports"])
    causes = "\n".join(f"- {topic['seed']} 可能原因 {cause}。{cause} 导致 {effect}。" for cause, effect in topic["causes"])
    keywords = "、".join(topic["keywords"])
    return f"""# {topic['title']}

模块：{topic['module']}
标签：{topic['tag']}

## 能力说明
{supports}

## 故障关系
{causes}

## 关键词
排查该主题时经常出现这些术语：{keywords}。

## 处理建议
建议先确认模块和标签，再结合系统视图、执行计划、参数配置和历史变更记录进行排查。
"""


def _gold_graph_rows() -> list[dict]:
    rows = []
    for topic in TOPICS:
        for item in topic["supports"]:
            rows.append({"doc_id": topic["doc_id"], "triple": [topic["seed"], "支持", item]})
        for cause, effect in topic["causes"]:
            rows.append({"doc_id": topic["doc_id"], "triple": [topic["seed"], "可能原因", cause]})
            rows.append({"doc_id": topic["doc_id"], "triple": [cause, "导致", effect]})
    return rows


def _qa_rows() -> list[dict]:
    rows = []
    qid = 1

    def add(row: dict) -> None:
        nonlocal qid
        row["id"] = f"q_{qid:03d}"
        qid += 1
        rows.append(row)

    for idx in range(80):
        topic = TOPICS[idx % len(TOPICS)]
        support = topic["supports"][idx % len(topic["supports"])]
        add({
            "question": f"{topic['seed']}在 {topic['module']} 模块中支持什么能力，特别是{support}？",
            "answer": f"{topic['seed']}支持{support}，可用于{topic['title']}相关场景。",
            "type": "vector",
            "filters": {"tags": [topic["tag"]]},
            "gold_doc_ids": [topic["doc_id"]],
            "gold_chunk_keys": [f"{topic['doc_id']}#能力说明"],
            "gold_triples": [[topic["seed"], "支持", support]],
            "gold_paths": [],
        })

    for idx in range(40):
        topic = TOPICS[idx % len(TOPICS)]
        keyword = topic["keywords"][idx % len(topic["keywords"])]
        add({
            "question": f"排查 {topic['title']} 时，术语 {keyword} 通常和什么场景相关？",
            "answer": f"{keyword} 是 {topic['title']} 中常见的排查术语，通常结合模块标签和系统信息定位问题。",
            "type": "keyword",
            "filters": {"tags": [topic["tag"]]},
            "gold_doc_ids": [topic["doc_id"]],
            "gold_chunk_keys": [f"{topic['doc_id']}#关键词"],
            "gold_triples": [],
            "gold_paths": [],
        })

    for idx in range(40):
        topic = TOPICS[idx % len(TOPICS)]
        support = topic["supports"][(idx + 1) % len(topic["supports"])]
        add({
            "question": f"只在标签 {topic['tag']} 下，{topic['seed']}支持哪些能力？",
            "answer": f"在 {topic['tag']} 标签下，{topic['seed']}支持{support}等能力。",
            "type": "predicate",
            "filters": {"tags": [topic["tag"]], "module": topic["module"]},
            "gold_doc_ids": [topic["doc_id"]],
            "gold_chunk_keys": [f"{topic['doc_id']}#能力说明"],
            "gold_triples": [[topic["seed"], "支持", support]],
            "gold_paths": [],
        })

    for idx in range(40):
        topic = TOPICS[idx % len(TOPICS)]
        cause, effect = topic["causes"][idx % len(topic["causes"])]
        add({
            "question": f"{topic['seed']}可能由什么导致，以及该原因会进一步造成什么影响？",
            "answer": f"{topic['seed']}可能由{cause}导致，{cause}会进一步导致{effect}。",
            "type": "graph_path",
            "hop": 2,
            "filters": {"tags": [topic["tag"]]},
            "gold_doc_ids": [topic["doc_id"]],
            "gold_chunk_keys": [f"{topic['doc_id']}#故障关系"],
            "gold_triples": [[topic["seed"], "可能原因", cause], [cause, "导致", effect]],
            "gold_paths": [[topic["seed"], "可能原因", cause, "导致", effect]],
        })

    return rows


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for topic in TOPICS:
        (DOCS_DIR / f"{topic['doc_id']}.md").write_text(_doc_text(topic), encoding="utf-8")

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with (EVAL_DIR / "gold_graph.jsonl").open("w", encoding="utf-8") as f:
        for row in _gold_graph_rows():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (EVAL_DIR / "ops_qa_200.jsonl").open("w", encoding="utf-8") as f:
        for row in _qa_rows():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print("Generated eval/docs, eval/gold_graph.jsonl, eval/ops_qa_200.jsonl")


if __name__ == "__main__":
    main()
