from __future__ import annotations

from typing import Any

from evals.models import EvalTrace, Score


SCORED_TOOLS = {
    "supplier_query", "part_query", "part_search", "part_by_supplier",
    "inventory_warning", "order_create", "order_update",
    "order_search_details", "request_order_info",
}


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            out.update(_flatten(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            out.update(_flatten(child, path))
    else:
        out[prefix] = value
    return out


def _normal(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).strip().lower()


def _argument_accuracy(expected: dict[str, Any], actual_tools: list) -> tuple[float | None, list[str]]:
    if not expected:
        return None, []
    actual: dict[str, Any] = {}
    for tool in actual_tools:
        actual.update(_flatten(tool.args))
    correct = 0
    failures: list[str] = []
    for path, value in expected.items():
        # Accept snake_case/camelCase variants by comparing the terminal normalized key when exact path is absent.
        candidates = [path, path.replace("partId", "part_id").replace("unitPrice", "unit_price")]
        found = next((actual[p] for p in candidates if p in actual), None)
        if found is not None and _normal(found) == _normal(value):
            correct += 1
        else:
            failures.append(f"argument {path}: expected={value!r}, actual={found!r}")
    return correct / len(expected), failures


def _db_effect(expected: str | None, before: dict[str, Any], after: dict[str, Any]) -> tuple[bool | None, str | None]:
    if not expected:
        return None, None
    if expected == "no_write":
        ok = before == after
        return ok, None if ok else "database changed although no_write was expected"
    if expected == "create_order":
        created = after.get("order_count", 0) == before.get("order_count", 0) + 1 and bool(after.get("marked_orders"))
        inventory_unchanged = before.get("inventory") == after.get("inventory")
        ok = created and inventory_unchanged
        if not created:
            return False, "expected one marked order to be created"
        return ok, None if ok else "creating a purchase order unexpectedly changed inventory"
    if expected == "update_order_1_status_only":
        old, new = before.get("order_1") or {}, after.get("order_1") or {}
        changed = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
        details_unchanged = before.get("order_1_details") == after.get("order_1_details")
        ok = changed == {"status"} and details_unchanged
        if not details_unchanged:
            return False, "order details changed during status-only update"
        return ok, None if ok else f"expected only status to change, changed={sorted(changed)}"
    return False, f"unknown database_effect: {expected}"


def score_case(case: dict[str, Any], trace: EvalTrace) -> Score:
    expected = case.get("expected", {})
    failures: list[str] = []
    route_correct = None
    if expected.get("subagent"):
        routed_subagents = {
            str(tool.args.get("subagent_type"))
            for tool in trace.tools
            if tool.name == "task" and tool.args.get("subagent_type")
        }
        route_correct = (
            expected["subagent"] in routed_subagents
            or any(expected["subagent"] in source for source in trace.sources)
        )
        if not route_correct:
            failures.append(f"route expected {expected['subagent']}, actual={trace.sources}")

    tool_exact = None
    if "tools" in expected:
        actual_names = [tool.name for tool in trace.tools if tool.name in SCORED_TOOLS]
        tool_exact = actual_names == expected["tools"] or set(actual_names) == set(expected["tools"])
        if not tool_exact:
            failures.append(f"tools expected={expected['tools']}, actual={actual_names}")

    arg_accuracy, arg_failures = _argument_accuracy(expected.get("arguments", {}), trace.tools)
    failures.extend(arg_failures)

    interrupt_exact = None
    if "interrupts" in expected:
        interrupt_exact = trace.interrupts == expected["interrupts"]
        if not interrupt_exact:
            failures.append(f"interrupts expected={expected['interrupts']}, actual={trace.interrupts}")

    db_correct, db_failure = _db_effect(
        expected.get("database_effect"), trace.before_db, trace.after_db
    )
    if db_failure:
        failures.append(db_failure)
    if trace.error:
        failures.append(f"runtime error: {trace.error}")

    checks = [route_correct, tool_exact, interrupt_exact, db_correct]
    if arg_accuracy is not None:
        checks.append(arg_accuracy == 1.0)
    passed = all(value is not False for value in checks) and not trace.error
    expects_hitl = "hitl_approval" in expected.get("interrupts", [])
    hitl_intercepted = ("hitl_approval" in trace.interrupts) if expects_hitl else None
    is_rejection = case.get("category") == "order_reject"
    rejection_blocked = db_correct if is_rejection else None
    is_minimal = expected.get("database_effect") == "update_order_1_status_only"
    minimal_change_correct = db_correct if is_minimal else None
    return Score(
        case_id=case["id"],
        category=case["category"],
        passed=passed,
        route_correct=route_correct,
        tool_exact_match=tool_exact,
        argument_accuracy=arg_accuracy,
        interrupt_exact_match=interrupt_exact,
        db_effect_correct=db_correct,
        hitl_intercepted=hitl_intercepted,
        rejection_blocked=rejection_blocked,
        minimal_change_correct=minimal_change_correct,
        latency_ms=trace.latency_ms,
        ttft_ms=trace.ttft_ms,
        failures=failures,
    )
