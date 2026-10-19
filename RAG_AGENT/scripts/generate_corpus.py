"""Generate explicitly synthetic plain-text benchmark corpus; actual chunks counted after ingest."""
import argparse
from pathlib import Path

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--documents', type=int, default=1000)
    parser.add_argument('--paragraphs', type=int, default=1000)
    parser.add_argument('--output', default='.runtime/benchmark-corpus')
    args = parser.parse_args()
    root = Path(args.output); root.mkdir(parents=True, exist_ok=True)
    for doc in range(args.documents):
        path = root / f'synthetic-{doc:06d}.txt'
        if path.exists(): raise SystemExit('Refusing to overwrite existing corpus')
        path.write_text('\n\n'.join(f'SYNTHETIC BENCHMARK DOCUMENT {doc} PARAGRAPH {para}. '
            + 'This is generated load data and must not be used to claim business accuracy. '*7
            for para in range(args.paragraphs)), encoding='utf-8')
    print(f'Generated {args.documents} synthetic documents; query PostgreSQL for actual resulting chunk count.')
