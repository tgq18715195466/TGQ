from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    tool_call_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "main"
    result_text: str = ""


@dataclass
class EvalTrace:
    case_id: str
    thread_id: str
    tools: list[ToolCall] = field(default_factory=list)
    interrupts: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    final_content: str = ""
    error: str | None = None
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    before_db: dict[str, Any] = field(default_factory=dict)
    after_db: dict[str, Any] = field(default_factory=dict)
    final_status: str = "unknown"


@dataclass
class Score:
    case_id: str
    category: str
    passed: bool
    route_correct: bool | None
    tool_exact_match: bool | None
    argument_accuracy: float | None
    interrupt_exact_match: bool | None
    db_effect_correct: bool | None
    hitl_intercepted: bool | None
    rejection_blocked: bool | None
    minimal_change_correct: bool | None
    latency_ms: float
    ttft_ms: float | None
    failures: list[str] = field(default_factory=list)
