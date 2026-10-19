"""Filter shared agent tools and recheck permissions at execution time.

Install a separate instance on the main graph and every subagent graph. Both
sync and async hooks read backend.auth.current_identity on each invocation;
model state, runtime context, arguments and startup identity are never trusted.
"""
from collections.abc import Iterable
import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, hook_config
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage, SystemMessage, AIMessage
from agent.request_scope import request_scope, runtime_request_text, scope_notice

from agent.tools.auth_bridge import (
    INTERNAL_TOOL_NAMES,
    build_tool_registry,
    tool_allowed_for_identity,
    tool_name,
    validate_tool_registry,
)

__all__ = ['RoleToolPermissionsMiddleware', 'filter_tools_for_identity']


def filter_tools_for_identity(tools: Iterable[Any], user: dict | None) -> list[Any]:
    """Return an ordered tool view for a trusted backend identity, without mutation.

    Validate name conflicts across the complete pool before pruning. Unknown
    tools are hidden at runtime; startup registration must also call
    validate_tool_registry to report missing policies as configuration errors.
    Internal verification remains callable by the workflow but hidden to models.
    """
    registry = build_tool_registry(tools)
    return [tool for name, tool in registry.items()
            if name not in INTERNAL_TOOL_NAMES and tool_allowed_for_identity(name, user)]


class RoleToolPermissionsMiddleware(AgentMiddleware):
    """Use the current backend identity for model visibility and tool execution."""

    def __init__(self, registered_tools: Iterable[Any] | None = None):
        # Validate shared startup definitions; do not cache tools or principals.
        if registered_tools is not None:
            validate_tool_registry(registered_tools)

    @hook_config(can_jump_to=['end'])
    def before_agent(self, state, runtime):
        from backend.auth import current_identity
        denied = request_scope(runtime_request_text(runtime)).denial(current_identity.get())
        if denied:
            # A real graph transition persists both request and refusal, so a
            # later follow-up cannot silently resume the previous business goal.
            return {'messages': [AIMessage(content=denied)], 'jump_to': 'end'}
        return None

    @hook_config(can_jump_to=['end'])
    async def abefore_agent(self, state, runtime):
        return self.before_agent(state, runtime)

    def wrap_model_call(self, request: ModelRequest, handler):
        return handler(self._scoped_request(request))

    async def awrap_model_call(self, request: ModelRequest, handler):
        return await handler(self._scoped_request(request))

    @staticmethod
    def _scoped_request(request):
        from backend.auth import current_identity
        user = current_identity.get()
        text = runtime_request_text(request.runtime)
        scope = request_scope(text)
        tools = [tool for tool in filter_tools_for_identity(request.tools, user)
                 if scope.allows(tool_name(tool))]
        original = request.system_message
        notice = scope_notice(user, text)
        if original is None or isinstance(original.content, str):
            content = ((original.content + '\n\n') if original else '') + notice
        else:
            content = [*original.content, {'type': 'text', 'text': notice}]
        return request.override(tools=tools, system_message=SystemMessage(content=content))

    @staticmethod
    def _denied_tool_call(request: ToolCallRequest) -> ToolMessage | None:
        from backend.auth import current_identity

        user = current_identity.get()
        name = request.tool_call['name']
        # Check both the selected name and the actual tool, when present. A stale
        # or mismatched registration must not dispatch a differently named tool.
        matches = request.tool is None or tool_name(request.tool) == name
        aligned = request_scope(runtime_request_text(request.runtime)).allows(name)
        if matches and tool_allowed_for_identity(name, user) and aligned:
            return None
        code = 'AUTH_REQUIRED' if not user else 'PERMISSION_DENIED'
        if user and matches and tool_allowed_for_identity(name, user) and not aligned:
            code = 'REQUEST_ACTION_MISMATCH'
        return ToolMessage(
            name=name, tool_call_id=request.tool_call['id'], status='error',
            content=json.dumps({'ok': False, 'error': {'code': code, 'retryable': False}},
                               ensure_ascii=False),
        )

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        denied = self._denied_tool_call(request)
        if denied is not None:
            return denied
        return handler(request)

    async def awrap_tool_call(self, request: ToolCallRequest, handler):
        denied = self._denied_tool_call(request)
        if denied is not None:
            return denied
        return await handler(request)
