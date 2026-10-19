"""统一图表入口：本地支持 bar/column/line/pie，可选路由已加载的外部 MCP 扩展。

非图表工具保留为独立工具；能力描述以实际可用类型为准。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool

# 有效的可视化后缀
_CHART_SUFFIXES = ("_chart",)
_KEEP_SUFFIXES = ("_map", "_diagram", "_graph")

# 本地及可选外部类型的用途标签，不代表这些工具均已加载
CHART_TYPE_GUIDE: Dict[str, str] = {
    # === 标准图表（19 种）===
    "area": "数据随时间变化趋势，如库存量变化",
    "bar": "供应商价格横向分类对比",
    "boxplot": "多类别数据分布对比（箱线图），如供应商交货周期分布",
    "column": "各物料采购量/金额纵向对比",
    "dual_axes": "价格+数量双轴复合分析（柱+线）",
    "funnel": "转化漏斗，如采购流程各环节转化率",
    "histogram": "价格/交货周期数值分布频率",
    "line": "价格/订单量时间趋势",
    "liquid": "单一百分比指标（水波图），如采购完成率、预算执行率",
    "organization": "组织架构层级关系",
    "pie": "采购金额按分类/供应商占比",
    "radar": "供应商多维度综合评分（价格、质量、交期、服务）",
    "sankey": "数据流向/转化（桑基图），如物料从供应商到仓库流转",
    "scatter": "价格-质量二维相关性分析",
    "treemap": "采购金额层级占比（矩形树图）",
    "venn": "集合交集关系（韦恩图），如供应商与物料交叉分析",
    "violin": "数据分布密度（小提琴图），比箱线图展示更详细分布形态",
    "waterfall": "累计增减变化（瀑布图），如采购成本逐项构成",
    "word_cloud": "物料名称/供应商高频分析词云",
    # === 地图（3 种）===
    "district_map": "中国行政区划数据分布，如各省/市采购额分布",
    "path_map": "路线规划展示，如物流配送路线",
    "pin_map": "兴趣点位置分布，如供应商/仓库地理位置",
    # === 图表/关系图（3 种）===
    "fishbone_diagram": "质量问题根因分析（鱼骨图）",
    "flow_diagram": "采购审批/业务流程步骤展示",
    "network_graph": "供应商-物料关系网络拓扑",
    # === 思维导图（1 种）===
    "mind_map": "采购策略/产品架构结构化梳理",
}


# ---- 紧凑速查表：chart_type → 数据模式 + 特殊参数（用于工具描述，~800 tokens） ----

# ---- 从 MCP 工具 args_schema 提取参数信息 ----

def _extract_schema(tool) -> Optional[dict]:
    """从工具提取 JSON Schema。"""
    if not hasattr(tool, "args_schema") or not tool.args_schema:
        return None
    try:
        return tool.args_schema.schema()
    except Exception:
        return None


def _prop_to_md(name: str, prop: dict, required: set, indent: int = 0) -> str:
    """将单个 JSON Schema 属性转为 Markdown 行。"""
    prefix = "  " * indent
    req = " **必填**" if name in required else ""

    # 处理嵌套对象
    if prop.get("type") == "object" and "properties" in prop:
        lines = [f"{prefix}- **`{name}`** (object){req}: {prop.get('description', '')}"]
        sub_props = prop["properties"]
        sub_req = set(prop.get("required", []))
        for sub_name, sub_prop in sub_props.items():
            lines.append(_prop_to_md(sub_name, sub_prop, sub_req, indent + 1))
        return "\n".join(lines)

    # 处理数组（含 items 为对象的情况）
    if prop.get("type") == "array" and "items" in prop:
        items = prop["items"]
        desc = prop.get("description", "")
        lines = [f"{prefix}- **`{name}`** (array){req}: {desc}"]

        if isinstance(items, dict) and items.get("type") == "object" and "properties" in items:
            item_props = items["properties"]
            item_req = set(items.get("required", []))
            for item_name, item_prop in item_props.items():
                lines.append(_prop_to_md(item_name, item_prop, item_req, indent + 1))
        return "\n".join(lines)

    # 简单类型
    ptype = prop.get("type", "any")
    if prop.get("enum"):
        ptype = " | ".join(repr(v) for v in prop["enum"])
    desc = prop.get("description", "")
    return f"{prefix}- **`{name}`** ({ptype}){req}: {desc}"


def _extract_tool_params(tool) -> str:
    """从单个 MCP 工具的 args_schema 提取参数描述。"""
    schema = _extract_schema(tool)
    if not schema:
        return "  *参数信息不可用*"
    props = schema.get("properties", {})
    if not props:
        return "  *无额外参数*"
    required = set(schema.get("required", []))
    lines = []
    for name, prop in props.items():
        lines.append(_prop_to_md(name, prop, required))
    return "\n".join(lines)


def build_chart_params_md(tool_map: Dict[str, Any]) -> str:
    """
    根据 tool_map 生成完整的图表参数参考文档（Markdown 格式），
    用于上传到沙箱 /skills/procurement/chart_params.md。

    Args:
        tool_map: chart_type → MCP StructuredTool 的映射字典

    Returns:
        Markdown 格式的完整参数参考文档
    """
    from agent.tools.local_charts import LOCAL_TYPES
    lines = [
        "# 可视化图表参数参考",
        "",
        "## 本地图表",
        "支持 bar、column、line、pie，优先本地生成 SVG，无需外部服务。",
        "chart_config: data=[{category: string, value: number}]，可选 title。",
        "data 为 1–60 行有限数值；pie 要求数值非负且总和大于零。",
        "不支持分组、堆叠或自定义宽高/主题/样式。中文、绿色配色、自动布局。",
        "工具返回 /api/artifacts/ 图片链接，直接嵌入回复。",
    ]
    external = {key: value for key, value in tool_map.items() if key not in LOCAL_TYPES}
    if external:
        lines.extend(["", "## 已加载的外部扩展", "仅以下类型使用外部 MCP，参数以各自 schema 为准。"])
        for ct, t in sorted(external.items()):
            lines.extend(["", f"### {ct} — {CHART_TYPE_GUIDE.get(ct, '')}", _extract_tool_params(t)])
    else:
        lines.extend(["", "未加载额外的外部图表类型。"])


    return "\n".join(lines)


def _build_tool_description(tool_map: Dict[str, Any]) -> str:
    """生成 generate_visualization 的工具描述（紧凑速查表 + 参考文件提示）。"""
    from agent.tools.local_charts import LOCAL_TYPES
    chart_list = ", ".join(sorted(set(tool_map) | LOCAL_TYPES))
    return (
        "Generate a data visualization and return an embeddable image link.\n"
        "\n"
        "Local types: bar, column, line, pie. Additional types require loaded external MCP tools.\n"
        f"Available chart_type values: {chart_list}\n"
        "\n"
        + "Local bar, column, line and pie accept data=[{category: string, value: number}] and title. Local rendering is preferred, works offline and returns an embeddable /api/artifacts/ image. Local charts accept 1-60 finite values, use fixed green colors and automatic layout; custom sizes/themes/styles and grouped/stacked data are unsupported. Pie requires non-negative values with a positive total. Other chart types require a loaded external tool. Only use the available types listed above." +
        "\n\n"
        "When unsure about the exact fields for chart_config, read "
        "`/skills/procurement/chart_params.md` first, then call this tool."
    )


def create_generate_chart_tool(chart_mcp_tools: list):
    """
    工厂函数：将本地图表与已加载的外部扩展统一为 generate_visualization 入口。

    Returns:
        (generate_visualization, other_tools) 二元组
        - generate_visualization: 合并后的可视化入口工具
        - other_tools: 未被合并且应保留的独立工具列表
    """
    tool_map: Dict[str, Any] = {}
    other_tools: List[Any] = []

    for t in chart_mcp_tools:
        name = t.name
        if not name.startswith("generate_"):
            other_tools.append(t)
            continue

        suffix = name[len("generate_"):]

        # _chart 后缀 → 去掉后缀作为 key（bar_chart → bar）
        matched = False
        for chart_suf in _CHART_SUFFIXES:
            if suffix.endswith(chart_suf) and suffix != chart_suf:
                chart_type = suffix[:-len(chart_suf)]
                tool_map[chart_type] = t
                matched = True
                break
        if matched:
            continue

        # _map / _diagram / _graph → 保留后缀作为 key
        for keep_suf in _KEEP_SUFFIXES:
            if suffix.endswith(keep_suf) and suffix != keep_suf:
                chart_type = suffix
                tool_map[chart_type] = t
                matched = True
                break
        if not matched:
            other_tools.append(t)

    # 动态生成工具描述（紧凑速查表）
    tool_description = _build_tool_description(tool_map)

    @tool
    async def generate_visualization(chart_type: str, chart_config: dict) -> str:
        """PLACEHOLDER — 实际描述在运行时动态注入。"""
        from agent.tools.local_charts import LOCAL_TYPES, generate_local_chart
        chart_type = chart_type.strip().lower()
        if chart_type in LOCAL_TYPES:
            try:
                return generate_local_chart(chart_type, chart_config)
            except ValueError as exc:
                return f"Error: Invalid chart data: {exc}"
        chart_tool = tool_map.get(chart_type)
        if not chart_tool:
            available = ", ".join(sorted(set(tool_map) | LOCAL_TYPES))
            return (
                f"Error: Unknown chart type '{chart_type}'. "
                f"Available types: {available}"
            )
        result = await chart_tool.ainvoke(chart_config)
        return result

    # 注入动态生成的描述
    generate_visualization.name = "generate_visualization"
    generate_visualization.description = tool_description

    return generate_visualization, other_tools
