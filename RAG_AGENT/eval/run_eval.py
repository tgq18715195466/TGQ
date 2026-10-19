"""Offline metric-calculator smoke run or evaluation of externally generated predictions.

Fixture predictions are hand-written calculator inputs, NOT system quality measurements.
"""
import argparse
import hashlib
import json
from pathlib import Path
from app.evaluation import evaluate_prediction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', default='eval/datasets/knowledge_qa_v1.jsonl')
    parser.add_argument('--predictions', required=True)
    parser.add_argument('--output', default='eval/reports/latest.json')
    args = parser.parse_args()
    data = Path(args.dataset).read_bytes()
    cases = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    predictions = {row['id']: row for row in map(json.loads, Path(args.predictions).read_text(encoding='utf-8').splitlines())}
    fixture = any('synthetic-fixture' in row.get('tags', []) for row in cases)
    results = [{'id': case['id'], 'metrics': evaluate_prediction(case, predictions[case['id']])} for case in cases]
    report = {'dataset_version': 'knowledge_qa_v1', 'dataset_sha256': hashlib.sha256(data).hexdigest(),
              'fixture_only': fixture, 'quality_claim_allowed': False if fixture else None,
              'results': results, 'unmeasured': ['OCR quality', 'RAGAS faithfulness', 'real service latency', 'million-chunk scale']}
    target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(results)} cases calculated; fixture_only={fixture}; report={target}')


if __name__ == '__main__':
    main()
