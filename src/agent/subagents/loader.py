"""
子 Agent YAML 配置加载器。

从 YAML 配置文件目录读取子 Agent 定义，转换为 DeepAgents
create_deep_agent() 所需的 SubAgent dict 格式。

使用方式:
    from agent.subagents.loader import load_subagent_configs, resolve_subagent_tools

    # 1. 加载原始配置（tools 为字符串名称列表）
    raw_configs = load_subagent_configs()

    # 2. 与 MCP 工具匹配，解析为可调用的工具对象
    subagents = resolve_subagent_tools(raw_configs, all_mcp_tools)
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

# YAML 配置文件目录
CONFIGS_DIR = Path(__file__).parent / "configs"


def load_subagent_configs(
    configs_dir: Optional[Path] = None,
) -> List[Dict]:
    """
    加载指定目录下所有 .yaml 子 Agent 配置文件。

    每个 YAML 文件的顶级结构:
        name: str               # 必须 — 子 Agent 唯一名称
        description: str        # 必须 — 供主 Agent 决策的描述
        system_prompt: str      # 必须 — 子 Agent 系统提示词
        tools: list[str]        # 必须 — 精确工具名称列表（运行时解析）
        optional_tools: list[str]  # 可选 — 明确允许未启用的工具
        model: str              # 可选 — 模型标识符
        skills: list[str]       # 可选 — 技能路径列表

    Args:
        configs_dir: 配置文件目录，默认为 CONFIGS_DIR。

    Returns:
        SubAgent 配置字典列表，tools 字段暂为字符串名称。
    """
    if configs_dir is None:
        configs_dir = CONFIGS_DIR

    configs: List[Dict] = []

    if not configs_dir.exists():
        print(f"[WARNING] 子 Agent 配置目录不存在: {configs_dir}")
        return configs

    for yaml_file in sorted(configs_dir.glob("*.yaml")):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML subagent config: {yaml_file.name}") from e
        except OSError as e:
            raise ValueError(f"Cannot read subagent config: {yaml_file.name}") from e

        # 校验必填字段
        missing = _validate_subagent_config(data, yaml_file.name)
        if missing:
            raise ValueError(f"Invalid subagent config {yaml_file.name}: {', '.join(missing)}")

        configs.append(data)
        print(f"[INFO] 已加载子 Agent 配置: {data['name']} ← {yaml_file.name}")

    return configs


def resolve_subagent_tools(
    configs: List[Dict],
    available_tools: List,
    extra_middleware: Dict[str, List] | None = None,
) -> List[Dict]:
    """
    将 YAML 配置中的工具名称字符串解析为实际的工具可调用对象。

    只匹配精确名称。tools 中的必需工具缺失时初始化失败；
    optional_tools 中未启用的工具可以缺失。完整工具池先检查同名冲突及权限策略，
    不按启动用户裁剪；共享图的权限由每次调用的中间件检查。

    Args:
        configs: load_subagent_configs() 返回的原始配置列表。
        available_tools: 可用的工具对象列表（来自 MCP 客户端）。
        extra_middleware: 子 Agent 额外中间件，key 为子 Agent 名称，
                          value 为中间件实例列表。

    Returns:
        可以直接传入 create_deep_agent(subagents=...) 的 SubAgent 字典列表。
    """
    if extra_middleware is None:
        extra_middleware = {}
    from agent.memory.language import CHINESE_REPLY_RULES

    from agent.tools.auth_bridge import validate_tool_registry

    # Validate the entire pool, including tools not requested by this agent.
    tool_index = validate_tool_registry(available_tools)

    subagents: List[Dict] = []

    for config in configs:
        invalid = _validate_subagent_config(config, "runtime config")
        if invalid:
            raise ValueError(f"Invalid subagent config: {', '.join(invalid)}")
        tool_names = config["tools"]
        missing = [name for name in tool_names if name not in tool_index]
        if missing:
            raise ValueError(
                f"Subagent '{config['name']}' missing required tools: {', '.join(missing)}"
            )
        names = dict.fromkeys(tool_names + config.get("optional_tools", []))
        unique_tools = [tool_index[name] for name in names if name in tool_index]

        subagent: Dict = {
            "name": config["name"],
            "description": config["description"].replace("\n", " ").strip(),
            "system_prompt": config["system_prompt"] + CHINESE_REPLY_RULES,
            "tools": unique_tools,
        }

        # 可选字段
        if config.get("model"):
            subagent["model"] = config["model"]

        if config.get("skills"):
            subagent["skills"] = config["skills"]

        if config.get("interrupt_on") and config["name"] != "procurement-order":
            subagent["interrupt_on"] = config["interrupt_on"]

        # 合并额外中间件
        agent_middleware = list(config.get("middleware", []))
        if any(tool.name in {"purchase_request_create", "order_create"} for tool in unique_tools):
            from agent.middlewares.procurement_result import ProcurementResultMiddleware
            agent_middleware.append(ProcurementResultMiddleware())
        if config["name"] in extra_middleware:
            agent_middleware.extend(extra_middleware[config["name"]])
        if config["name"] == "procurement-order" and config.get("interrupt_on"):
            # Fixed creation tools own lookup, supplementation and confirmation.
            # Keep the legacy gate for update and inventory operations only.
            interrupt_on = {
                name: policy for name, policy in config["interrupt_on"].items()
                if name not in {"order_create", "purchase_request_create"}
            }
            if interrupt_on:
                agent_middleware.append(_remaining_order_preflight(interrupt_on))
        if agent_middleware:
            subagent["middleware"] = agent_middleware

        subagents.append(subagent)
        print(
            f"[INFO] 子 Agent '{config['name']}' 已解析: "
            f"{len(unique_tools)} 个工具, "
            f"{len(config.get('skills', []))} 个技能"
        )

    return subagents


def _validate_subagent_config(data: Dict, filename: str) -> List[str]:
    """
    校验 SubAgent 配置的必填字段。

    Returns:
        缺失的字段名列表（空列表表示通过校验）。
    """
    if not isinstance(data, dict):
        return ["config (必须为字典)"]
    required = ["name", "description", "system_prompt", "tools"]
    missing = [f for f in required if f not in data or data[f] is None]

    # tools 必须是非空列表
    if "tools" in data:
        tools = data["tools"]
        if not isinstance(tools, list) or len(tools) == 0:
            missing.append("tools (必须为非空列表)")

    for field in ("tools", "optional_tools"):
        if field in data:
            names = data[field]
            if not isinstance(names, list) or any(
                not isinstance(name, str) or not name or name != name.strip()
                for name in names
            ):
                missing.append(f"{field} (必须为精确名称列表)")

    # system_prompt 不能为空字符串
    if "system_prompt" in data:
        sp = data["system_prompt"]
        if not isinstance(sp, str) or not sp.strip():
            missing.append("system_prompt (必须为非空字符串)")

    return missing


def _remaining_order_preflight(interrupt_on: Dict):
    """Keep legacy HITL without its unconditional creation-field preflight.

    OrderPreflightMiddleware validates creation calls even when absent from
    interrupt_on. Its existing date injection remains useful; the base HITL
    hook preserves update/inventory decisions and the full mixed tool batch.
    """
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain.agents.middleware.types import hook_config
    from agent.middlewares.order_preflight import OrderPreflightMiddleware

    class RemainingOrderPreflightMiddleware(OrderPreflightMiddleware):
        @hook_config(can_jump_to=["model"])
        def after_model(self, state, runtime):
            from backend.auth import current_identity
            from agent.tools.auth_bridge import tool_allowed_for_identity
            from agent.request_scope import request_scope, runtime_request_text
            from langchain_core.messages import AIMessage, ToolMessage
            import json
            message = state['messages'][-1]
            user = current_identity.get()
            scope = request_scope(runtime_request_text(runtime))
            if isinstance(message, AIMessage) and any(
                call['name'] in interrupt_on and not scope.allows(call['name'])
                for call in message.tool_calls
            ):
                return {'jump_to': 'model', 'messages': [ToolMessage(
                    name=call['name'], tool_call_id=call['id'], status='error',
                    content=json.dumps({'ok': False, 'error': {
                        'code': 'REQUEST_ACTION_MISMATCH', 'retryable': False,
                    }}, ensure_ascii=False),
                ) for call in message.tool_calls]}
            if isinstance(message, AIMessage) and any(
                call['name'] in interrupt_on and not tool_allowed_for_identity(call['name'], user)
                for call in message.tool_calls
            ):
                return {'jump_to': 'model', 'messages': [ToolMessage(
                    name=call['name'], tool_call_id=call['id'], status='error',
                    content=json.dumps({'ok': False, 'error': {
                        'code': 'PERMISSION_DENIED' if user else 'AUTH_REQUIRED',
                        'message': '本批操作未执行，您的账号没有执行其中部分操作的权限。',
                        'retryable': False,
                    }}, ensure_ascii=False),
                ) for call in message.tool_calls]}
            return HumanInTheLoopMiddleware.after_model(self, state, runtime)

        @hook_config(can_jump_to=["model"])
        async def aafter_model(self, state, runtime):
            return self.after_model(state, runtime)

    return RemainingOrderPreflightMiddleware(interrupt_on=interrupt_on)
