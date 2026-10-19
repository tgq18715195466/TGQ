from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from evals.models import EvalTrace, ToolCall


def _parse_args(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"_raw": raw}


def _consume_stream(response: httpx.Response, trace: EvalTrace, started: float) -> dict[str, Any] | None:
    active: dict[str, ToolCall] = {}
    interrupt: dict[str, Any] | None = None
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        event = json.loads(line[5:].strip())
        event_type = event.get("type")
        source = event.get("source")
        if source and source not in trace.sources:
            trace.sources.append(source)
        if event_type == "token":
            if trace.ttft_ms is None:
                trace.ttft_ms = (time.perf_counter() - started) * 1000
            trace.final_content += event.get("content", "")
        elif event_type == "tool_start":
            call_id = event.get("tool_call_id")
            if not call_id:
                trace.error = "tool_start missing tool_call_id"
                continue
            call = ToolCall(tool_call_id=call_id, name=event.get("tool_name", "unknown"), source=source or "main")
            active[call_id] = call
        elif event_type == "tool_args":
            call_id = event.get("tool_call_id")
            call = active.get(call_id)
            if call is None:
                trace.error = f"tool_args references unknown tool_call_id: {call_id}"
                continue
            raw = call.args.get("_raw", "") + event.get("args", "")
            call.args = {"_raw": raw}
        elif event_type == "tool_result":
            call_id = event.get("tool_call_id")
            call = active.pop(call_id, None)
            if call is not None:
                call.args = _parse_args(call.args.get("_raw", ""))
                # DeepAgents uses the main-agent `task` tool to route work.  The
                # child namespace contains an execution UUID rather than the
                # configured subagent name, so capture the authoritative route
                # from task.subagent_type instead of guessing from namespace.
                if call.name == "task":
                    subagent = call.args.get("subagent_type")
                    if subagent and subagent not in trace.sources:
                        trace.sources.append(str(subagent))
                call.result_text = event.get("text", "")
                trace.tools.append(call)
        elif event_type == "interrupt":
            # Interrupted tools do not emit tool_result before suspension. Record the attempted
            # call here so HITL interception can still be scored.
            for call in active.values():
                call.args = _parse_args(call.args.get("_raw", ""))
                if call.name == "task":
                    subagent = call.args.get("subagent_type")
                    if subagent and subagent not in trace.sources:
                        trace.sources.append(str(subagent))
                trace.tools.append(call)
            active.clear()
            interrupt_type = event.get("interrupt_type", "unknown")
            trace.interrupts.append(interrupt_type)
            trace.final_status = "interrupted"
            interrupt = event
            break
        elif event_type == "error":
            trace.error = event.get("message", "unknown SSE error")
            trace.final_status = "error"
        elif event_type == "done":
            trace.final_status = "completed"
            if event.get("content") and not trace.final_content:
                trace.final_content = event["content"]
    return interrupt


def run_case(case: dict[str, Any], base_url: str, timeout_s: float = 180.0) -> EvalTrace:
    thread_id = f"eval-{case['id']}-{uuid.uuid4()}"
    trace = EvalTrace(case_id=case["id"], thread_id=thread_id)
    started = time.perf_counter()
    resume_steps = iter(case.get("resume", []))
    try:
        with httpx.Client(timeout=timeout_s) as client:
            with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/api/chat/stream",
                json={"message": case["input"], "thread_id": thread_id},
            ) as response:
                response.raise_for_status()
                interrupt = _consume_stream(response, trace, started)

            while interrupt is not None:
                try:
                    step = next(resume_steps)
                except StopIteration:
                    trace.error = f"Unexpected interrupt without resume instruction: {interrupt.get('interrupt_type')}"
                    break
                expected_type = step.get("interrupt_type")
                actual_type = interrupt.get("interrupt_type")
                if expected_type and expected_type != actual_type:
                    trace.error = f"Expected interrupt {expected_type}, got {actual_type}"
                    break
                with client.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/api/chat/{thread_id}/resume",
                    json={"resume": step["value"]},
                ) as response:
                    response.raise_for_status()
                    interrupt = _consume_stream(response, trace, started)
    except Exception as exc:  # noqa: BLE001
        trace.error = str(exc)
    trace.latency_ms = (time.perf_counter() - started) * 1000
    return trace
