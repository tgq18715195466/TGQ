---
name: procurement-analysis
description: >
  采购分析主技能包。串联完整分析流程：需求拆解 → 数据收集 → 执行分析 → 生成图表 → 输出报告。
  当需要进行供应商对比、价格分析、物料筛选、采购策略评估时加载此技能。
---

# 采购分析技能（操作手册）

## 适用场景
- 供应商比价分析（多供应商、同物料价格对比）
- 物料行情评估（价格趋势、供需分析）
- 供应商综合评分（按信用评级、价格、交期加权打分）
- 采购策略建议（分阶段采购、集中采购、备选供应商推荐）
- 生成含图表的采购分析报告

## 分析流程（5 步）

### 第 1 步：理解需求
- 从任务描述中提取：【任务目标】、【用户偏好】、【分析需求正文】
- 明确分析维度：供应商对比？价格趋势？物料筛选？综合评估？

### 第 2 步：收集数据

**2a. ERP 内部数据（必须）**
- `supplier_query(name="供应商名")` → 供应商列表
- `part_query(name="物料名")` 或 `part_search(name="物料名")` → 零部件
- `part_by_supplier(supplier_id=N)` → 按供应商查零部件
- `order_search_details(partName="xxx")` → 历史订单价格
- `inventory_warning()` → 库存预警

**2b. 外部数据（不可跳过）**
1. 激活 `/skills/procurement/supplier-price-urls/`，读取 `data/url_mapping.yaml`
2. 按零部件名 + 供应商名查找对应报价 URL
3. **找到 URL** → 使用 `web-scraper` 直接抓取：
   ```bash
   execute("python /skills/procurement/web-scraper/scrape_page.py --url '<报价URL>'")
   ```
   脚本将 HTML 转为 Markdown 保存到 `/analysis/temp/{filename}.md`，再 `read_file` 读取内容提取价格/型号/品牌/材质/单位。
4. **未找到 URL** → 使用 `web_search` 搜索市场价格和规格参数
5. 无论哪种路径，最终都要有外部参考数据用于对比

### 第 3 步：执行分析
1. 编写 Python 分析脚本，使用 `execute` 工具运行
2. 如需安装依赖：`pip install -i https://mirrors.aliyun.com/pypi/simple/ pandas matplotlib numpy`（已自动路由到 `/opt/skills-venv/bin/pip`）
3. 常见分析维度：
   - 价格对比：最低价、均价、最高价、价差百分比
   - 供应商评估：信用评级 + 价格 + 交期加权
   - 物料质量：规格参数对比
   - 综合排名：多维度加权评分
4. 分析结果保留在上下文（`execute` 工具输出），无需写入文件

### 第 4 步：生成图表
1. **首次调用图表前**：`read_file("/skills/procurement/chart_params.md")` 获取本地四种图表的参数和限制
2. 简单清单查询先返回完整清单；图表可选，必要时选择 1-2 张本地 bar/column/line/pie 图。
4. 循环调用 `generate_visualization(chart_type="xxx", chart_config={...})`，本地仅传 data 和可选 title，不使用宽高、主题或样式参数
5. 参考文件只读一次，后续多次调用不额外消耗上下文
6. 参数报错时对照参考文件修正后重试

### 第 5 步：生成报告
1. 汇总分析结论和图表 URL
2. 完整答案直接写入回复。仅用户要求报告或导出时才写入 `/analysis/report_{timestamp}.md`，并用 download_sandbox_file 发布网页下载链接。

## 图表类型速查

- bar：横向类别对比。
- column：纵向类别对比。
- line：按输入顺序展示变化趋势。
- pie：非负数值的占比。

参数和限制见 `/skills/procurement/chart_params.md`。其他类型仅在运行时工具描述明确列出时可用。

## 报告模板

```markdown
# 采购分析报告

## 1. 分析概述
（目的、数据范围、方法说明）

## 2. 数据概览
（查询到的供应商数量、物料数量、数据时间范围）

## 3. 对比分析
（核心对比结果，附图表引用）

## 4. 结论与建议
（3-5 条可操作的采购建议）
```

## 返回主 Agent 格式

返回可直接展示给网页用户的完整 Markdown 正文：清单表格、关键结论、必要图片。
“所有供应商”必须包含查询到的全部明细，不得用统计图、摘要或报告路径替代。
图片使用工具返回的 /api/artifacts/ 链接；附件仅作为补充。禁止返回原始 JSON。
