from __future__ import annotations

import argparse
import json
import os
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from evals.db_probe import cleanup_case, restore_snapshot, snapshot
from evals.scorers import score_case
from evals.sse_client import run_case


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{number}: {exc}") from exc
    return cases


def pct(values: list[bool | None]) -> float | None:
    selected = [v for v in values if v is not None]
    return round(100 * sum(selected) / len(selected), 2) if selected else None


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * p))
    return round(ordered[index], 2)


def summary(scores: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cases": len(scores),
        "end_to_end_task_success_rate": pct([s["passed"] for s in scores]),
        "route_accuracy": pct([s["route_correct"] for s in scores]),
        "tool_selection_accuracy": pct([s["tool_exact_match"] for s in scores]),
        "interrupt_exact_match": pct([s["interrupt_exact_match"] for s in scores]),
        "db_effect_accuracy": pct([s["db_effect_correct"] for s in scores]),
        "hitl_approval_interception_rate": pct([s.get("hitl_intercepted") for s in scores]),
        "rejection_block_rate": pct([s.get("rejection_blocked") for s in scores]),
        "minimal_change_accuracy": pct([s.get("minimal_change_correct") for s in scores]),
        "parameter_accuracy": round(
            100 * statistics.mean(s["argument_accuracy"] for s in scores if s["argument_accuracy"] is not None), 2
        ) if any(s["argument_accuracy"] is not None for s in scores) else None,
        "latency_p50_ms": percentile([s["latency_ms"] for s in scores], 0.50),
        "latency_p95_ms": percentile([s["latency_ms"] for s in scores], 0.95),
        "ttft_p50_ms": percentile([s["ttft_ms"] for s in scores if s["ttft_ms"] is not None], 0.50),
        "ttft_p95_ms": percentile([s["ttft_ms"] for s in scores if s["ttft_ms"] is not None], 0.95),
    }


def main() -> int:
    load_dotenv(".env.eval", override=True)
    parser = argparse.ArgumentParser(description="Run ERP Agent black-box SSE evaluation")
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/erp_agent_eval.jsonl"))
    parser.add_argument("--base-url", default=os.getenv("EVAL_BASE_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--category", action="append", help="May be supplied multiple times")
    parser.add_argument("--keep-writes", action="store_true")
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    if args.category:
        cases = [case for case in cases if case["category"] in set(args.category)]
    if args.limit:
        cases = cases[: args.limit]

    try:
        snapshot("__preflight__")
    except Exception as exc:  # noqa: BLE001
        print(f"Evaluation preflight failed: motorparts_eval is unavailable ({type(exc).__name__}: {exc})")
        return 2

    report_rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['id']} - {case['category']}", flush=True)
        if not args.keep_writes:
            cleanup_case(case["id"])
        before = snapshot(case["id"])
        trace = run_case(case, args.base_url)
        trace.before_db = before
        trace.after_db = snapshot(case["id"])
        score = score_case(case, trace)
        report_rows.append(asdict(score))
        traces.append(asdict(trace))
        print("  PASS" if score.passed else f"  FAIL: {'; '.join(score.failures)}")
        if not args.keep_writes:
            restore_snapshot(case["id"], before)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = Path("evals/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "generated_at": stamp,
        "dataset": str(args.dataset),
        "summary": summary(report_rows),
        "scores": report_rows,
    }
    (report_dir / f"result-{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (report_dir / f"traces-{stamp}.json").write_text(
        json.dumps(traces, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if all(row["passed"] for row in report_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
