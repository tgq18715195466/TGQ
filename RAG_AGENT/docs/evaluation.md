# 评测说明

当前数据集 `knowledge_qa_v1` 含3条明确标记为 synthetic-fixture 的样本：事实、表格、无答案。没有真实业务文档，未伪造300条人工样本。
`eval/expected/fixture_predictions.jsonl` 是手工提供的指标计算器输入，不是模型输出，也不是系统质量结果。

运行离线计算器：

```powershell
python eval/run_eval.py --predictions eval/expected/fixture_predictions.jsonl
```

输出标记 `fixture_only=true`、`quality_claim_allowed=false`。支持 Recall@10、MRR@10、Context Precision、引用精确率、拒答正确性、字符准确率、标题/单元格集合F1及实测耗时。未给出真值或预测数据时指标为 null；不填假分数。

真实运行流程：

1. 上传业务资料并等待 READY；冻结文档版本与标注 chunk IDs。
2. 按任务书字段组织 JSONL，由人工记录 annotator。目标分类数量仍为80/50/50/50/40/30，目前未完成该标注规模。
3. `python scripts/manage.py import-eval --tenant <UUID> --user <UUID> --file dataset.jsonl --version v1`。
4. POST `/api/v1/eval-runs`，传 dataset_id、kb_ids、use_ragas；请求用户必须是指定 KB owner。
5. 启动对应租户 Worker。每例实际走问答，结果保存 eval_results，版本配置和聚合指标保存 eval_runs。GET接口会重新校验范围并记录导出审计。

RAGAS 为可选依赖：`pip install -e '.[eval]'`，配置可访问的评分 LLM/Embedding，use_ragas=true。当前环境未安装/执行该额外依赖，未产生 Faithfulness 或答案相关性分数；兼容性需要真实运行确认。引用覆盖率当前定义为严格摘录答案的逐句覆盖，不应与自由生成模型的语义覆盖率混淆。

Badcase 标签在 app/evaluation.py，当前自动标记检索和拒答失败，其余标签可人工补充；修复回归记录在 tests/test_regressions.py。未实现完整人工标注 UI 或独立标注一致性流程。

压测先运行 `python scripts/generate_corpus.py --documents 1000 --paragraphs 1000` 产生明确标记的合成文本，再用授权上传 API 导入。实际 chunk 数以 PostgreSQL 查询为准，不能用文件数估算后声称100万规模达成。

设置 BENCHMARK_TOKEN 后：

```powershell
python scripts/benchmark.py --kb <UUID> --chunk-count <真实数量> --hardware "CPU/RAM/GPU型号" --concurrency 30 --requests 100
```

报告记录平台、操作者填报硬件、数据量、请求明细、成功率和 SSE首个已验证答案片段P95。当前未运行真实模型、100万chunk、30路SSE或5并行MinerU压测，所有任务书质量/性能门槛均未声明达标。
