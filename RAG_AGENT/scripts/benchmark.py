"""Actual HTTP SSE load generator; records hardware/data size supplied by operator."""
import argparse
import asyncio
import json
import os
import platform
import time
from pathlib import Path

import httpx
from app.evaluation import percentile


async def measure(args):
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(base_url=args.url, headers={'Authorization': 'Bearer '+os.environ['BENCHMARK_TOKEN']}, timeout=300) as client:
        async def query(index):
            async with semaphore:
                started, first = time.monotonic(), None
                session = await client.post('/api/v1/chat/sessions', json={'title': f'benchmark-{index}'})
                session.raise_for_status()
                run = await client.post('/api/v1/chat/runs', json={'session_id': session.json()['id'],
                    'question': args.question, 'kb_ids': [args.kb]})
                run.raise_for_status()
                completed = False
                async with client.stream('GET', f'/api/v1/chat/runs/{run.json()["run_id"]}/events') as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line == 'event: answer.delta' and first is None: first = (time.monotonic()-started)*1000
                        if line == 'event: run.completed': completed = True
                return {'success': completed, 'first_answer_ms': first, 'total_ms': (time.monotonic()-started)*1000}
        results = await asyncio.gather(*(query(i) for i in range(args.requests)), return_exceptions=True)
    rows = [row if isinstance(row, dict) else {'success': False, 'error': type(row).__name__} for row in results]
    return {'platform': platform.platform(), 'hardware': args.hardware, 'chunk_count': args.chunk_count,
            'concurrency': args.concurrency, 'requests': args.requests,
            'success_rate': sum(row['success'] for row in rows)/len(rows),
            'sse_first_answer_p95_ms': percentile([r['first_answer_ms'] for r in rows if r.get('first_answer_ms') is not None],95),
            'rows': rows}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='http://127.0.0.1:8000'); p.add_argument('--kb', required=True)
    p.add_argument('--question', default='合同金额是多少？'); p.add_argument('--concurrency', type=int, default=30)
    p.add_argument('--requests', type=int, default=100); p.add_argument('--chunk-count', type=int, required=True)
    p.add_argument('--hardware', required=True); p.add_argument('--output', default='eval/reports/benchmark.json')
    args = p.parse_args()
    result = asyncio.run(measure(args))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
