"""CI guard: old component names are permitted only in migration/history documentation/tests."""
import re
from pathlib import Path

FORBIDDEN = re.compile(r'qwen[_-]?vl|minio|mongodb|pymongo|\bmotor\b|\bMONGO(?:DB)?_', re.I)


def violations(root):
    files = []
    for folder in ['app', 'deploy', '.github']:
        path = root / folder
        if path.exists():
            files.extend(p for p in path.rglob('*') if p.is_file() and p.suffix in {'.py', '.yaml', '.yml', '.json', '.toml'})
    files.extend(root / name for name in ['pyproject.toml', 'requirements.lock', '.env.example', 'compose.yaml', 'Dockerfile'])
    hits = []
    for path in files:
        if path.exists():
            for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
                if FORBIDDEN.search(line):
                    hits.append(f'{path.relative_to(root)}:{number}')
    return hits


if __name__ == '__main__':
    results = violations(Path(__file__).resolve().parents[1])
    print('\n'.join(results) if results else 'Forbidden runtime dependency scan: PASS')
    raise SystemExit(bool(results))
