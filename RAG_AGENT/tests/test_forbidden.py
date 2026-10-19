from pathlib import Path
from scripts.check_forbidden import violations


def test_no_old_runtime_dependencies():
    assert violations(Path(__file__).resolve().parents[1]) == []
