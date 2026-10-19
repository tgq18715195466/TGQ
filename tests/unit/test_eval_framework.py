import json

import httpx
import pytest

from evals import db_probe
from evals.models import EvalTrace
from evals.run_eval import summary
from evals.sse_client import _consume_stream


def test_parallel_tool_args_are_correlated_by_call_id():
    events = [
        {"type": "tool_start", "tool_call_id": "a", "tool_name": "part_search"},
        {"type": "tool_start", "tool_call_id": "b", "tool_name": "supplier_query"},
        {"type": "tool_args", "tool_call_id": "a", "args": '{"name":"活塞'},
        {"type": "tool_args", "tool_call_id": "b", "args": '{"name":"博世"}'},
        {"type": "tool_args", "tool_call_id": "a", "args": '环"}'},
        {"type": "tool_result", "tool_call_id": "b", "text": "ok"},
        {"type": "tool_result", "tool_call_id": "a", "text": "ok"},
    ]
    payload = "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
    response = httpx.Response(200, content=payload.encode(), request=httpx.Request("GET", "http://test"))
    trace = EvalTrace(case_id="c", thread_id="t")
    _consume_stream(response, trace, 0)
    assert {call.name: call.args for call in trace.tools} == {
        "supplier_query": {"name": "博世"},
        "part_search": {"name": "活塞环"},
    }


def test_summary_contains_required_metrics():
    row = {
        "passed": True, "route_correct": True, "tool_exact_match": True,
        "argument_accuracy": 1.0, "interrupt_exact_match": True,
        "db_effect_correct": True, "hitl_intercepted": True,
        "rejection_blocked": True, "minimal_change_correct": True,
        "latency_ms": 10.0, "ttft_ms": 2.0,
    }
    metrics = summary([row])
    assert metrics["hitl_approval_interception_rate"] == 100
    assert metrics["rejection_block_rate"] == 100
    assert metrics["minimal_change_accuracy"] == 100


def test_task_tool_records_configured_subagent_route():
    events = [
        {"type": "tool_start", "tool_call_id": "route-1", "tool_name": "task", "source": "main"},
        {"type": "tool_args", "tool_call_id": "route-1", "args": '{"subagent_type":"procurement-analyst"}'},
        {"type": "tool_result", "tool_call_id": "route-1", "text": "ok", "source": "main"},
    ]
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    response = httpx.Response(200, content=payload.encode(), request=httpx.Request("GET", "http://test"))
    trace = EvalTrace(case_id="c", thread_id="t")
    _consume_stream(response, trace, 0)
    assert "procurement-analyst" in trace.sources


def test_interrupted_task_still_records_configured_subagent_route():
    events = [
        {"type": "tool_start", "tool_call_id": "route-1", "tool_name": "task", "source": "main"},
        {"type": "tool_args", "tool_call_id": "route-1", "args": '{"subagent_type":"procurement-order"}'},
        {"type": "interrupt", "interrupt_type": "hitl_approval"},
    ]
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    response = httpx.Response(200, content=payload.encode(), request=httpx.Request("GET", "http://test"))
    trace = EvalTrace(case_id="c", thread_id="t")
    _consume_stream(response, trace, 0)
    assert "procurement-order" in trace.sources


def test_evaluator_refuses_any_non_eval_database(monkeypatch):
    monkeypatch.setenv("EVAL_MYSQL_DB", "motorparts_db")
    with pytest.raises(RuntimeError, match="motorparts_eval"):
        db_probe._config()
