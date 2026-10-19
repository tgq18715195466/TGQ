"""Pass terminal procurement facts to DeepAgents without another model call."""
import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


class ProcurementResultMiddleware(AgentMiddleware):
    def after_agent(self, state, runtime):
        messages = state.get('messages', [])
        last = messages[-1] if messages else None
        if not isinstance(last, ToolMessage) or last.name not in {
            'purchase_request_create', 'order_create'
        }:
            return None
        # DeepAgents normally returns the last AI text, which can be stale or
        # empty after return_direct. Its structured_response channel preserves
        # the actual, verified workflow result instead.
        try:
            result = json.loads(last.content)
        except (TypeError, ValueError):
            return None
        if isinstance(result, dict) and isinstance(result.get('ok'), bool):
            return {'structured_response': result}
        return None

    async def aafter_agent(self, state, runtime):
        return self.after_agent(state, runtime)
