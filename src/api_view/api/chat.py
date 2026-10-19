"""
对话 API 模块

提供流式对话接口、中断恢复接口和会话状态查询接口
"""

import asyncio
import anyio
from contextlib import aclosing
import weakref
import json
import uuid
import re
import os
import tempfile
from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from agent.schema import (
    ChatRequest,
    ChatResponse,
    Message,
)
from api_view.answer_stream import BusinessAnswerStream
from agent.tools.business_answer import ANSWER_TOOL
from api_view.agent_loader import agent_loader
from api_view.session_ids import public_thread_id, storage_thread_id as scoped_thread_id
from api_view.interrupts import validate_resume, validate_resume_permissions
from agent.business_results import normalize_business_result, expose_business_result
from agent.procurement_workflow import STAGES as PROCUREMENT_STAGES
from agent.request_scope import request_scope
from api_view.output_protocol import READ_ONLY_TOOLS, TOOL_LABELS, tool_failure, terminal_answer, run_failure, DisplayHistoryError, decode_result


# 创建路由
router = APIRouter()

# 调试日志文件路径（在项目根目录的 temp 文件夹下）
DEBUG_LOG_DIR = os.path.join(tempfile.gettempdir(), "deepagent_debug")
os.makedirs(DEBUG_LOG_DIR, exist_ok=True)


def get_debug_log_path(thread_id: str) -> str:
    """获取当前会话的调试日志文件路径"""
    safe_id = thread_id.replace("/", "_").replace("\\", "_")[:50]
    return os.path.join(DEBUG_LOG_DIR, f"stream_{safe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


def write_debug_log(filepath: str, event_type: str, data: dict, raw_token: object = None):
    """写入调试日志"""
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {event_type}\n")
            f.write(f"  data: {json.dumps(data, ensure_ascii=False, default=str)[:2000]}\n")
            if raw_token is not None:
                token_info = {}
                if hasattr(raw_token, 'type'):
                    token_info['type'] = raw_token.type
                if hasattr(raw_token, 'name'):
                    token_info['name'] = raw_token.name
                if hasattr(raw_token, 'content'):
                    c = raw_token.content
                    if isinstance(c, str):
                        token_info['content_preview'] = c[:500]
                    elif isinstance(c, list):
                        token_info['content_len'] = len(c)
                        token_info['content_preview'] = str(c)[:500]
                    else:
                        token_info['content_preview'] = str(c)[:500]
                if hasattr(raw_token, 'tool_call_chunks') and raw_token.tool_call_chunks:
                    token_info['tool_call_chunks'] = str(raw_token.tool_call_chunks)[:500]
                if hasattr(raw_token, 'id'):
                    token_info['id'] = raw_token.id
                f.write(f"  token: {json.dumps(token_info, ensure_ascii=False, default=str)[:2000]}\n")
            f.write("\n")
    except Exception:
        pass  # 调试日志失败不影响主流程


def extract_subagent_name(namespace: tuple) -> str:
    """
    从 namespace 中提取子代理名称
    """
    for segment in namespace:
        if segment.startswith("tools:"):
            return segment.replace("tools:", "")
    return "main"


def is_likely_uuid(text: str) -> bool:
    """
    判断文本是否大概率是 UUID（而非有意义的回复内容）
    例如：3576bba4-42e5-a769-c2f7-ea8444829951
    """
    # UUID 格式：8-4-4-4-12
    uuid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    return bool(re.match(uuid_pattern, text.strip()))


def extract_content_from_token(token) -> str:
    """
    从 token 中提取文本内容

    处理 token.content 可能是字符串、列表或其他类型的情况
    过滤掉明显是 UUID 或无关标识符的内容
    """
    if not hasattr(token, 'content'):
        return ""

    content = token.content

    # 如果 content 是字符串，直接返回
    if isinstance(content, str):
        # 过滤纯 UUID 内容
        if is_likely_uuid(content):
            return ""
        return content

    # 如果 content 是列表，尝试提取其中的文本
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                # 结构化内容：只提取 text 类型，忽略 image_url 等
                item_type = item.get("type", "")
                if item_type == "text":
                    t = item.get("text", "")
                    if not is_likely_uuid(t):
                        text_parts.append(t)
                elif item_type == "image_url":
                    # 图片 URL 不提取为文本，由 serialize_tool_result 处理
                    pass
                elif "text" in item:
                    t = item["text"]
                    if not is_likely_uuid(str(t)):
                        text_parts.append(str(t))
                elif "content" in item:
                    text_parts.append(str(item["content"]))
            else:
                text_parts.append(str(item))
        return ''.join(text_parts)

    # 其他类型转换为字符串
    return str(content) if content is not None else ""


def serialize_tool_result(content) -> dict:
    """
    将工具执行结果序列化为前端可用的结构化数据

    处理 tool 消息的 content，可能是字符串、列表（包含文本和图片）
    提取文本和图片数据，返回结构化字典
    """
    result = {"text": "", "images": []}

    if isinstance(content, str):
        result["text"] = content
        # 提取 markdown 图片语法
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', content):
            url = match.group(1)
            if url not in result["images"]:
                result["images"].append(url)

    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    t = item.get("text", "")
                    text_parts.append(t)
                elif item_type == "image_url":
                    image_url = item.get("image_url", {})
                    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                    if url:
                        result["images"].append(url)
                elif item_type == "image":
                    image_data = item.get("data", "") or item.get("image_data", "")
                    if image_data:
                        result["images"].append(image_data)
                elif "text" in item:
                    text_parts.append(item["text"])
                elif "content" in item:
                    text_parts.append(str(item["content"]))
                else:
                    text_parts.append(str(item))
            else:
                text_parts.append(str(item))
        result["text"] = ''.join(text_parts)
    else:
        result["text"] = str(content) if content is not None else ""

    # 从文本中提取所有图片 URL（包括 markdown 语法和直链）
    # 支持常见图片托管域名（即使 URL 不以图片扩展名结尾）
    if result["text"]:
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', result["text"]):
            url = match.group(1)
            if url not in result["images"]:
                result["images"].append(url)
        # 也检测 data:image base64
        for match in re.finditer(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', result["text"]):
            url = match.group(0)
            if url not in result["images"]:
                result["images"].append(url)

    return result


def create_sse_message(data: dict) -> str:
    """创建 SSE 格式的消息"""
    if data.get("type", "").startswith("tool_"):
        data.setdefault("channel", "delegation" if data.get("tool_name") == "task" else "execution")
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


PROCUREMENT_WRITES = frozenset({'order_create', 'purchase_request_create'})
NEUTRAL_OUTCOMES = frozenset({'cancelled', 'needs_information'})
BUSINESS_WRITES = PROCUREMENT_WRITES | {'order_update', 'inventory_adjust', 'master_change_request'}
QUERY_KINDS = frozenset({'supplier', 'part', 'inventory', 'replenishment', 'order_detail', 'procurement'})


def public_business_result(tool_name, content, tool_call_id, arguments=None, status=None):
    """Project actual tool evidence; procurement success requires both signals."""
    raw = decode_result(content)
    if isinstance(raw, dict) and isinstance(raw.get('error'), dict) and raw['error'].get('code') == 'REQUEST_ACTION_MISMATCH':
        return None  # No business operation took place; do not label it as one.
    if status == 'error':
        raw = {'ok': False, 'error': raw.get('error') or {'code': 'TOOL_ERROR'}} if isinstance(raw, dict) else {'ok': False, 'error': {'code': 'TOOL_ERROR'}}
    if tool_name in PROCUREMENT_WRITES and isinstance(raw, dict):
        if raw.get('verified') is not True or raw.get('verificationOutcome') != 'success':
            raw = {k: v for k, v in raw.items() if k not in {'verified', 'verificationOutcome', 'verification_outcome'}}
    result = normalize_business_result(tool_name, raw, arguments)
    if result is None:
        return None
    if isinstance(raw, dict) and raw.get('outcome') == 'unknown':
        result.execution.outcome = 'unknown'
        result.execution.message = '采购执行结果尚未确认，请先核验业务单据，避免重复提交。'
    safe = expose_business_result(result)
    # Correlation IDs are opaque keys, never business cell values.
    safe['result_id'] = tool_call_id
    return safe


class BusinessSummaryStream:
    """Buffer candidate headers until the next line proves or disproves a table."""
    separator = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$')

    def __init__(self):
        self.buffer = ''
        self.header = ''
        self.in_table = False

    def _line(self, line):
        if self.in_table:
            if '|' in line and line.strip():
                return ''
            self.in_table = False
        prefix = ''
        if self.header:
            if self.separator.match(line.rstrip('\n')):
                self.header = ''
                self.in_table = True
                return ''
            prefix, self.header = self.header, ''
        if '|' in line:
            self.header = line
            return prefix
        return prefix + line

    def feed(self, fragment, complete=False):
        self.buffer += fragment
        output = []
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            output.append(self._line(line + '\n'))
        if complete:
            if self.buffer:
                output.append(self._line(self.buffer))
                self.buffer = ''
            if self.header:
                output.append(self.header)
                self.header = ''
        return ''.join(output)


def summary_without_tables(content):
    """Use the same table policy for streamed and durable model summaries."""
    filtered = BusinessSummaryStream().feed(content, complete=True)
    return re.sub(r'\n{3,}', '\n\n', filtered).strip()


# ============================================================
# 中断恢复请求模型
# ============================================================

class ResumeRequest(BaseModel):
    """中断恢复请求体，resume 字段的格式取决于中断类型：
    - 数据补充中断: {"supplement": "用户自由文本输入"}
    - HITL 审批中断: {"decisions": [{"type": "approve"}]} 或 [{"type": "reject"}]
    """
    resume: dict


# ============================================================
# 流式对话核心逻辑
# ============================================================

async def stream_chat_response(
    message: str = None,
    thread_id: str = None,
    resume_data: dict = None,
    user_id: str = "anonymous",
    username: str | None = None,
) -> AsyncIterator[str]:
    """
    流式生成对话响应，支持 Human-in-the-Loop 中断与恢复。

    两种调用模式：
    1. 初始对话 — 传入 message（用户消息）
    2. 中断恢复 — 传入 resume_data（Command.resume 的值）

    当 Agent 触发中断时（request_order_info 数据补充 / order_create|update HITL 审批），
    流会发送 interrupt 事件后结束。前端收集用户决策后通过 /resume 端点恢复。

    同时累积完整的展示消息列表（包含子代理消息），在流结束后存入 PostgreSQL。
    """
    context = {"user_id": user_id, "username": username or user_id}
    thread_id = public_thread_id(thread_id, user_id)
    storage_thread_id = scoped_thread_id(thread_id, user_id)
    config = agent_loader.create_config(storage_thread_id, user_id=user_id)
    collected_content = ""
    final_state = {}
    history_saved = False
    # Tool calls are keyed by model-provided IDs so parallel chunks cannot cross-wire.
    active_tool_calls = {}
    tool_call_indexes = {}

    # ---- 根据模式构建 input 和 display_messages ----
    if resume_data is not None:
        # 恢复模式：加载已有展示消息，用 Command(resume=...) 恢复
        existing = await agent_loader.get_display_messages(storage_thread_id) or []
        display_messages = existing
        current_input = Command(resume=resume_data)
    else:
        # 初始模式：新建展示消息，用普通消息作为 input
        current_input = {"messages": [{"role": "user", "content": message}]}
        display_messages = [
            {
                "id": f"user-{uuid.uuid4()}",
                "role": "user",
                "content": message
            }
        ]

    # 创建调试日志文件
    debug_log = get_debug_log_path(thread_id)

    answer_streams = {}
    summary_streams = {}
    answer_id = f"assistant-{uuid.uuid4()}"
    business_results = {}
    other_failures = {}
    raw_results = {}
    emitted_results = {}
    last_user = next((i for i in range(len(display_messages) - 1, -1, -1) if display_messages[i].get('role') == 'user'), -1)
    turn_messages = display_messages[last_user + 1:]
    result_tools = {m['id']: m.get('tool_name') for m in turn_messages if m.get('role') == 'tool'}
    request_text = display_messages[last_user].get('content', '') if last_user >= 0 else (message or '')
    context['request_text'] = request_text
    scope = request_scope(request_text)
    write_seen = bool(scope.actions) or any(name in BUSINESS_WRITES for name in result_tools.values())
    # A fact-only assistant from an interrupted turn is resumed in place.
    if resume_data is not None:
        for previous in reversed(display_messages):
            if previous.get('role') == 'user':
                break
            if previous.get('role') == 'assistant':
                if previous.get('business_results') and not previous.get('content'):
                    answer_id = previous['id']
                    business_results = {r['result_id']: r for r in previous['business_results']}
                break

    def is_query(result):
        name = result_tools.get(result['result_id'])
        return name in READ_ONLY_TOOLS or name is None and result.get('kind') in QUERY_KINDS

    def has_write_result():
        return any(result_tools.get(r['result_id']) in BUSINESS_WRITES for r in business_results.values())

    def visible_results():
        visible, seen = [], set()
        for result in business_results.values():
            query = is_query(result)
            if query:
                if scope.query_kinds is not None:
                    if result.get('kind') not in scope.query_kinds:
                        continue
                elif write_seen:
                    continue
            if result.get('kind') == 'receipt' and result.get('total') == 0 and result['execution']['outcome'] == 'success':
                continue
            # Keep pages, changed records and separate writes. Only identical
            # public query snapshots are redundant; their tool audit stays intact.
            key = json.dumps({k: v for k, v in result.items() if k != 'result_id'}, sort_keys=True, ensure_ascii=False)
            if query and key in seen:
                continue
            if query:
                seen.add(key)
            visible.append(result)
        return visible

    def answer_failures():
        # A verified creation includes fresh prerequisite checks. Earlier
        # internal lookup failures must not replace its actual saved result.
        verified_creation = any(result_tools.get(r['result_id']) in PROCUREMENT_WRITES
            and r['execution']['outcome'] == 'success' for r in business_results.values())
        failures = [r['execution']['message'] for r in business_results.values()
            if r['execution']['outcome'] != 'success' and not (verified_creation and is_query(r))]
        for result_id, message in other_failures.items():
            raw = raw_results.get(result_id)
            error = raw.get('error') if isinstance(raw, dict) else None
            if isinstance(error, dict) and error.get('code') == 'REQUEST_ACTION_MISMATCH' and any(
                result_tools.get(r['result_id']) in BUSINESS_WRITES
                and scope.allows(result_tools[r['result_id']]) for r in business_results.values()
            ):
                # The rejected attempt never executed. Once routing is corrected,
                # the actual operation's verified/failed/unknown outcome wins.
                continue
            # DeepAgents echoes the child's structured result through task.
            # Keep independent task failures, but not the same payload twice.
            echoed = result_tools.get(result_id) == 'task' and isinstance(raw_results.get(result_id), dict) and any(
                raw_results.get(result_id) == raw_results.get(r['result_id'])
                for r in business_results.values() if r['execution']['outcome'] != 'success'
            )
            if not echoed:
                failures.append(message)
        return list(dict.fromkeys(failures))

    def query_events():
        # Tool results are execution evidence until a completed read-only run
        # or the root final-answer tool establishes that they are the deliverable.
        if write_seen and scope.query_kinds is None:
            return []
        events = []
        for result in visible_results():
            if is_query(result) and emitted_results.get(result['result_id']) != result:
                emitted_results[result['result_id']] = result
                events.append({'type': 'business_result', 'channel': 'answer', 'id': answer_id, 'result': result})
        return events

    def failure_ids():
        ids = [r['result_id'] for r in business_results.values() if r['execution']['outcome'] in {'failure', 'unknown'}]
        return list(dict.fromkeys(ids + list(other_failures)))

    def store_assistant(content=""):
        facts = visible_results()
        assistant = next((dm for dm in display_messages if dm.get('id') == answer_id and dm.get('role') == 'assistant'), None)
        if assistant is None:
            if not content and not facts:
                return
            assistant = {"id": answer_id, "role": "assistant", "source": "main", "channel": "answer"}
            display_messages.append(assistant)
        assistant.update(content=content, business_results=facts, failure_tool_call_ids=failure_ids() if content else [])
        if content:
            for dm in display_messages:
                if dm.get('role') == 'tool' and dm.get('id') in failure_ids() and dm.get('failure'):
                    # Failure is an existing dictionary field, so this correlation
                    # survives Message/history serialization without schema changes.
                    dm['failure'] = {**dm['failure'], 'answer_id': answer_id}

    async def persist_partial():
        if history_saved:
            return
        store_assistant()
        for dm in display_messages:
            if dm.get('role') == 'tool' and dm.get('tool_status') == 'calling':
                dm['tool_status'] = 'error'
                dm['failure'] = tool_failure(dm.get('tool_name'), {'ok':False,'error':{'code':'TOOL_ERROR','retryable':True}})
        existing = (await agent_loader.get_display_messages(storage_thread_id) or []) if resume_data is None else []
        if await agent_loader.save_display_messages(storage_thread_id, existing + display_messages) is not True:
            raise DisplayHistoryError()

    try:
        yield create_sse_message({"type":"progress", "channel":"progress", "message":"正在处理您的请求。", "stage":"preparing"})
        write_debug_log(debug_log, "STREAM_START", {
            "message": message,
            "thread_id": thread_id,
            "is_resume": resume_data is not None,
        })

        # 流式调用 agent.astream()
        # stream_mode=["messages", "values", "custom"] — messages 流显示 + values 流检测中断
        from api_view.daily_receipts import is_daily_receipt_request, receipt_summary_stream
        from backend.auth import current_identity
        fixed_daily = resume_data is None and is_daily_receipt_request(message, (current_identity.get() or {}).get('role'))
        stream = receipt_summary_stream(invoke_readonly_tool) if fixed_daily else agent_loader.agent.astream(
            input=current_input,
            config=config,
            context=context,
            stream_mode=["messages", "values", "custom"],
            subgraphs=True, # 启用子代理流式输出
            version="v2",
        )
        async for chunk in stream:
            chunk_type = chunk.get("type")
            if chunk_type == "custom":
                data = chunk.get('data')
                if isinstance(data, dict) and data.get('type') == 'procurement_progress' and data.get('stage') in PROCUREMENT_STAGES:
                    write_seen = True
                    yield create_sse_message({'type': 'procurement_progress', 'channel': 'progress', 'stage': data['stage'], 'message': PROCUREMENT_STAGES[data['stage']]})
                continue

            # Let the graph finish its checkpoint writes before exposing approvals.
            # Returning from a child interrupt event would truncate parallel subgraphs.
            # ---- 消息流（token / tool_call / tool_result）----
            if chunk_type == "values" and not chunk.get("ns"):
                final_state = chunk.get("data") or {}
            if chunk_type != "messages":
                continue

            token, metadata = chunk["data"]
            # Internal entity extraction is not a user-facing assistant answer.
            if "erp:internal-memory" in metadata.get("tags", []) or str(metadata.get("langgraph_node", "")).startswith("MemoryUpdateMiddleware."):
                continue
            if getattr(token, "type", "") not in {"ai", "AIMessageChunk", "tool", "ToolMessageChunk"}:
                continue
            namespace = chunk.get("ns", ())

            # 判断消息来源
            is_subagent = any(s.startswith("tools:") for s in namespace)
            source = extract_subagent_name(namespace) if is_subagent else "main"

            write_debug_log(debug_log, "RAW_CHUNK", {
                "ns": list(namespace),
                "source": source,
            }, raw_token=token)

            # 处理工具调用
            tool_chunks = getattr(token, 'tool_call_chunks', None) or [
                {'id':call.get('id'), 'name':call.get('name'), 'args':json.dumps(call.get('args',{}),ensure_ascii=False), 'index':index}
                for index,call in enumerate(getattr(token, 'tool_calls', []) or [])
            ]
            # Only this dedicated root output tool may stream business text.
            # Ordinary root narration and all child messages remain private.
            if any(part.get('name') in BUSINESS_WRITES for part in tool_chunks):
                write_seen = True
            public_chunks = []
            for part in tool_chunks:
                index = part.get('index', 0)
                if not is_subagent and part.get('name') == ANSWER_TOOL:
                    answer_streams[index] = BusinessAnswerStream()
                    summary_streams[index] = BusinessSummaryStream()
                decoder = answer_streams.get(index) if not is_subagent else None
                if decoder is not None and (not part.get('name') or part.get('name') == ANSWER_TOOL):
                    if not active_tool_calls and not any(p.get('name') not in {None, ANSWER_TOOL} for p in tool_chunks):
                        for event in query_events():
                            yield create_sse_message(event)
                    delta = decoder.feed(part.get('args') or '')
                    # Query reports keep their curated tables. For write results,
                    # authoritative evidence replaces model-authored receipt tables.
                    if has_write_result():
                        try:
                            complete = isinstance(json.loads(decoder.raw).get('answer'), str)
                        except (ValueError, AttributeError):
                            complete = False
                        delta = summary_streams[index].feed(delta, complete=complete)
                    unsafe = answer_failures()
                    if delta and not unsafe and not active_tool_calls and not any(part.get('name') not in {None, ANSWER_TOOL} for part in tool_chunks):
                        yield create_sse_message({"type":"answer_delta", "channel":"answer", "id":answer_id, "content":delta, "source":"main"})
                elif part.get('name') != ANSWER_TOOL:
                    public_chunks.append(part)
            tool_chunks = public_chunks
            if getattr(token, 'name', None) == ANSWER_TOOL and getattr(token, 'type', '') == 'tool':
                continue
            if tool_chunks:
                for tool_chunk in tool_chunks:
                    # 工具开始调用
                    if tool_chunk.get('name'):
                        tool_id = str(tool_chunk.get("id") or f"{source}:{tool_chunk.get('index', uuid.uuid4())}")
                        new_tool = {
                            "id": tool_id,
                            "name": tool_chunk['name'],
                            "args": "",
                            "source": source
                        }
                        active_tool_calls[tool_id] = new_tool
                        tool_call_indexes[(source, tool_chunk.get("index"))] = tool_id

                        yield create_sse_message({"type":"progress", "channel":"progress", "stage":"executing", "message":"正在" + TOOL_LABELS.get(tool_chunk["name"], "处理业务任务") + "。"})
                        yield create_sse_message({
                            "type": "tool_start",
                            "tool_call_id": tool_id,
                            "tool_name": tool_chunk['name'],
                            "source": source
                        })

                        # 添加到展示消息
                        display_messages.append({
                            "id": tool_id,
                            "role": "tool",
                            "tool_name": tool_chunk['name'],
                            "args": "",
                            "text": "",
                            "images": [],
                            "source": source,
                            "tool_status": "calling"
                        })

                        write_debug_log(debug_log, "TOOL_START", {
                            "name": tool_chunk['name'],
                            "source": source,
                            "active_count": len(active_tool_calls)
                        })

                    # 工具参数
                    if tool_chunk.get('args'):
                        args_str = tool_chunk['args']
                        tool_id = str(
                            tool_chunk.get("id")
                            or tool_call_indexes.get((source, tool_chunk.get("index")), "")
                        )
                        if tool_id in active_tool_calls:
                            active_tool_calls[tool_id]["args"] += args_str

                        yield create_sse_message({
                            "type": "tool_args",
                            "tool_call_id": tool_id,
                            "args": args_str,
                            "source": source
                        })

                        # 更新展示消息中对应工具的 args（从后向前找最后一个 calling 的 tool）
                        for dm in reversed(display_messages):
                            if dm["role"] == "tool" and dm["id"] == tool_id:
                                dm["args"] += args_str
                                break

            # 处理工具执行结果
            if hasattr(token, 'type') and token.type == "tool":
                tool_name = getattr(token, 'name', '未知工具')
                if tool_name in BUSINESS_WRITES:
                    write_seen = True
                result_content = getattr(token, 'content', '')

                # 序列化工具结果为结构化数据
                serialized = serialize_tool_result(result_content)
                raw_result = decode_result(result_content, getattr(token, 'artifact', None))
                neutral = isinstance(raw_result, dict) and raw_result.get('outcome') in NEUTRAL_OUTCOMES
                failure = None if neutral else tool_failure(tool_name, raw_result, getattr(token, "status", None))

                tool_id = str(getattr(token, "tool_call_id", "") or "")
                finished_tool = active_tool_calls.pop(tool_id, None)
                if finished_tool is None and not tool_id:
                    matching = next(
                        ((key, call) for key, call in active_tool_calls.items() if call["name"] == tool_name),
                        None,
                    )
                    if matching:
                        tool_id, finished_tool = matching
                        active_tool_calls.pop(tool_id, None)

                arguments = None
                if finished_tool:
                    try:
                        arguments = json.loads(finished_tool.get('args') or '{}')
                    except (ValueError, TypeError):
                        pass
                result_id = tool_id or str(getattr(token, 'id', None) or f'{source}:{tool_name}')
                result_tools[result_id] = tool_name
                raw_results[result_id] = raw_result
                business = public_business_result(tool_name, raw_result, result_id, arguments, getattr(token, 'status', None))
                changed = business is not None and business_results.get(result_id) != business
                if business is not None:
                    business_results[result_id] = business
                elif failure:
                    other_failures[result_id] = failure['message']
                else:
                    other_failures.pop(result_id, None)

                # 更新展示消息中对应工具的结果
                for dm in reversed(display_messages):
                    if dm["role"] == "tool" and dm["id"] == tool_id:
                        dm["text"] = serialized["text"]
                        dm["images"] = serialized["images"]
                        dm["tool_status"] = "error" if failure else "done"
                        dm["failure"] = failure
                        break

                yield create_sse_message({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "tool_call_id": tool_id,
                    "text": serialized["text"],
                    "images": serialized["images"],
                    "source": source
                })

                if changed and not is_query(business):
                    emitted_results[result_id] = business
                    yield create_sse_message({'type': 'business_result', 'channel': 'answer', 'id': answer_id, 'result': business})
                if failure:
                    yield create_sse_message({"type":"tool_error", "channel":"failure", "tool_call_id":tool_id, "tool_name":tool_name, "failure":failure})
                yield create_sse_message({"type":"progress", "channel":"progress", "stage":"summarizing", "message":"正在整理业务结果。"})

                write_debug_log(debug_log, "TOOL_RESULT", {
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "text_preview": serialized["text"][:300],
                    "images_count": len(serialized["images"]),
                    "source": source,
                    "active_remaining": len(active_tool_calls)
                })

                # 发送 tool_end 事件，标记工具调用结束
                yield create_sse_message({
                    "type": "tool_end",
                    "tool_name": tool_name,
                    "tool_call_id": tool_id,
                    "source": source
                })

                # 确保展示消息中的工具标记为 done（兜底）
                for dm in reversed(display_messages):
                    if dm["role"] == "tool" and dm["id"] == tool_id and dm["tool_status"] == "calling":
                        dm["tool_status"] = "done"
                        break

            # Model chunks include pre-tool narration. Never forward them as body text.
            # The terminal main-graph state is authoritative, even with parallel subagents.

        pending = await agent_loader.get_pending_interrupts(storage_thread_id, thread_id)
        if any(item.get('workflow_type') == 'procurement' or any(action.get('name') in BUSINESS_WRITES for action in item.get('action_requests', [])) for item in pending):
            write_seen = True
        for event in query_events():
            yield create_sse_message(event)
        if not pending:
            messages = answer_failures()
            collected_content = '\n\n'.join(messages) if messages else terminal_answer(final_state)
            if not collected_content and failure_ids():
                collected_content = '\n\n'.join(dict.fromkeys(r['execution']['message'] for r in visible_results()))
            if has_write_result():
                collected_content = summary_without_tables(collected_content)
        store_assistant(collected_content)
        # Persist the complete display history before announcing completion.
        # 兜底：将所有 calling 状态的工具标记为 done
        for dm in display_messages:
            if dm["role"] == "tool" and dm["tool_status"] == "calling":
                dm["tool_status"] = "done"

        # 清理空的 assistant 消息
        display_messages = [
            dm for dm in display_messages
            if not (dm["role"] == "assistant" and not dm.get("content") and not dm.get("business_results"))
        ]

        # 保存完整展示消息到 PostgreSQL（包含子代理消息）
        # 多轮对话：追加到已有消息，而非覆盖（每轮调用 save 时 display_messages 仅含当前轮）
        if resume_data is None:
            # 初始对话：追加到已有历史
            existing = await agent_loader.get_display_messages(storage_thread_id) or []
            all_messages = existing + display_messages
        else:
            # resume 模式：display_messages 已包含历史（load 时获取），直接保存
            all_messages = display_messages
        if await agent_loader.save_display_messages(storage_thread_id, all_messages) is not True:
            raise DisplayHistoryError()
        history_saved = True
        write_debug_log(debug_log, "SAVE_DISPLAY", {
            "thread_id": thread_id,
            "total_count": len(all_messages)
        })

        if collected_content:
            yield create_sse_message({"type":"answer", "channel":"answer", "id":answer_id, "content":collected_content, "source":"main", "business_results": visible_results(), "business_results_mode": "replace", "failure_tool_call_ids": failure_ids()})
        if pending:
            yield create_sse_message({"type":"progress", "channel":"progress", "stage":"waiting", "message":"请补充信息或确认待审批操作。"})
            yield create_sse_message({**pending[0], "interrupts": pending})

        # 流结束，发送 done 事件
        write_debug_log(debug_log, "STREAM_DONE", {
            "thread_id": thread_id,
            "total_content_len": len(collected_content)
        })

        yield create_sse_message({
            "type": "done",
            "thread_id": thread_id,
            "content": collected_content,
            "id": answer_id,
            "business_results": visible_results(),
            "business_results_mode": "replace",
            "failure_tool_call_ids": failure_ids(),
            "interrupted": bool(pending)
        })

    except (asyncio.CancelledError, GeneratorExit):
        # Keep the session lock until bounded, cancellation-shielded cleanup ends.
        try:
            with anyio.move_on_after(5, shield=True) as cleanup:
                await persist_partial()
            if cleanup.cancel_called:
                write_debug_log(debug_log, "CANCEL_SAVE_TIMEOUT", {})
        except Exception as exc:
            write_debug_log(debug_log, "CANCEL_SAVE_ERROR", {"error":str(exc)})
        raise
    except Exception as e:
        write_debug_log(debug_log, "STREAM_ERROR", {"error": str(e)})
        if not history_saved and not isinstance(e, DisplayHistoryError):
            try:
                await persist_partial()
            except Exception as save_error:
                write_debug_log(debug_log, "PARTIAL_SAVE_ERROR", {"error":str(save_error)})
        yield create_sse_message({
            "type": "error", "message": run_failure(e),
            "code": "HISTORY_SAVE_FAILED" if isinstance(e, DisplayHistoryError) else "RUN_FAILED"
        })



# ============================================================
# API 端点
# ============================================================

_thread_locks = weakref.WeakValueDictionary()

async def _open_stream(thread_id, user_id, username, message=None, resume=None):
    public_id = public_thread_id(thread_id, user_id)
    key = scoped_thread_id(public_id, user_id)
    lock = _thread_locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "会话正在执行，请稍后再试。")
    await lock.acquire()
    try:
        pending = await agent_loader.get_pending_interrupts(key, public_id)
        if resume is not None:
            resume = validate_resume(pending, resume)
            from backend.auth import current_identity
            validate_resume_permissions(pending, resume, current_identity.get())
        elif pending:
            raise HTTPException(409, "请先处理当前待补充信息或待审批操作。")
    except BaseException:
        lock.release()
        raise

    async def generate():
        try:
            async with aclosing(stream_chat_response(message=message, thread_id=public_id,
                    resume_data=resume, user_id=user_id, username=username)) as stream:
                async for event in stream:
                    yield event
        finally:
            lock.release()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, x_user_id: str = Header("anonymous"), x_username: str | None = Header(None)):
    return await _open_stream(request.thread_id or str(uuid.uuid4()), x_user_id, x_username, message=request.message)

@router.post("/chat/{thread_id}/resume")
async def chat_resume(thread_id: str, request: ResumeRequest, x_user_id: str = Header("anonymous"), x_username: str | None = Header(None)):
    return await _open_stream(thread_id, x_user_id, x_username, resume=request.resume)


@router.get("/chat/{thread_id}")
async def get_chat_state(thread_id: str, x_user_id: str = Header("anonymous")):
    """获取会话状态"""
    public_id = public_thread_id(thread_id, x_user_id)
    thread_id = scoped_thread_id(thread_id, x_user_id)
    try:
        display = await agent_loader.get_display_messages(thread_id)
        if display:
            message_list = [Message(**item) for item in display]
        else:
            messages = await agent_loader.get_current_messages(thread_id)
            from api_view.api.history import serialize_messages_from_checkpoint
            message_list = [Message(**item) for item in serialize_messages_from_checkpoint(messages)]
        return ChatResponse(thread_id=public_id, messages=message_list)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/{thread_id}/history")
async def get_chat_history(
    thread_id: str,
    limit: int = Query(50, ge=1, le=100),
    x_user_id: str = Header("anonymous")
):
    """获取会话历史状态列表"""
    public_id = public_thread_id(thread_id, x_user_id)
    thread_id = scoped_thread_id(thread_id, x_user_id)
    try:
        states = await agent_loader.get_state_history(thread_id, limit)

        return {
            "thread_id": public_id,
            "states": [
                {
                    "config": state.config,
                    "values": state.values,
                    "created_at": state.created_at,
                    "parent_config": state.parent_config
                }
                for state in states
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def invoke_readonly_tool(name, args):
    """Resolve only ERP tools; clients cannot supply a tool name or new arguments."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from agent.tools.mcp_client import MCP_SERVER_CONFIG
    from agent.tools.auth_bridge import authenticated_mcp_call
    client = MultiServerMCPClient({'erp-api': MCP_SERVER_CONFIG['erp-api']}, tool_interceptors=[authenticated_mcp_call])
    available = await client.get_tools(server_name='erp-api')
    target = next((tool for tool in available if tool.name == name), None)
    if target is None:
        raise RuntimeError('Tool unavailable')
    return await target.ainvoke(args)


@router.post('/chat/{thread_id}/tools/{tool_call_id}/retry')
async def retry_tool(thread_id: str, tool_call_id: str, x_user_id: str = Header('anonymous')):
    public_id = public_thread_id(thread_id, x_user_id)
    key = scoped_thread_id(public_id, x_user_id)
    lock = _thread_locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, '会话正在执行，请稍后重试。')
    async with lock:
        if await agent_loader.get_pending_interrupts(key, public_id):
            raise HTTPException(409, '请先处理待补充信息或待审批操作。')
        messages = await agent_loader.get_display_messages(key) or []
        target = next((m for m in reversed(messages) if m.get('id') == tool_call_id and m.get('role') == 'tool'), None)
        if target is None:
            raise HTTPException(404, '未找到该查询记录。')
        name = target.get('tool_name')
        if name not in READ_ONLY_TOOLS or not (target.get('failure') or {}).get('retryable'):
            raise HTTPException(409, '该操作不可直接重试，请检查业务单据或修正输入。')
        try:
            args = json.loads(target.get('args') or '{}')
            if not isinstance(args, dict): raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(409, '原查询参数不完整，请重新发起查询。')
        status = None
        try:
            result = await asyncio.wait_for(invoke_readonly_tool(name, args), timeout=60)
            if hasattr(result, 'content'):
                status = getattr(result, 'status', None)
                content = result.content
            else:
                status, content = None, result
            raw_result = decode_result(content, getattr(result, 'artifact', None))
            failure = tool_failure(name, raw_result, status)
        except Exception:
            content = {'ok': False, 'error': {'code': 'ERP_UNAVAILABLE', 'retryable': True}}
            raw_result = content
            failure = tool_failure(name, content)
        serialized = serialize_tool_result(content)
        target.update(text=serialized['text'], images=serialized['images'], failure=failure,
                      tool_status='error' if failure else 'done')
        business = public_business_result(name, raw_result, tool_call_id, args, status)
        answer = next((m for m in reversed(messages) if m.get('role') == 'assistant' and
                       any(r.get('result_id') == tool_call_id for r in m.get('business_results', []))), None)
        # Upgrade existing query facts in place, including failed retries. Legacy
        # histories gain an assistant only after a successful query.
        if business is not None and (answer is not None or not failure):
            if answer is None:
                answer = {'id': f'assistant-{uuid.uuid4()}', 'role': 'assistant', 'source': 'main', 'channel': 'answer', 'business_results': []}
                messages.append(answer)
            results = {r['result_id']: r for r in answer.get('business_results', [])}
            results[tool_call_id] = business
            answer['business_results'] = list(results.values())
            answer['content'] = '\n\n'.join(dict.fromkeys(r['execution']['message'] for r in results.values()))
        if await agent_loader.save_display_messages(key, messages) is not True:
            raise HTTPException(503, '查询已执行，但结果保存失败。请检查数据库连接并刷新会话；本次未执行任何写操作。')
        return {'ok': not bool(failure), 'tool_call_id': tool_call_id, 'failure': failure, 'answer': answer, 'business_result': business}
