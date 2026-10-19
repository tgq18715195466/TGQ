"""Local web artifacts. Random public identifiers, no host paths in responses."""
import os
from pathlib import Path
from uuid import uuid4

from agent.backends.local_execution import current_user_id, user_directory


def artifact_dir():
    root = Path(os.getenv("ERP_ARTIFACT_DIR", str(Path(__file__).resolve().parents[1] / "download" / "web")))
    user_id = current_user_id()
    return root if user_id is None else user_directory(root, user_id)


def publish_artifact(content: bytes, suffix: str) -> str:
    if suffix not in {".svg", ".md", ".txt", ".csv", ".json", ".png", ".pdf", ".xlsx"}:
        raise ValueError("Unsupported attachment format")
    root = artifact_dir()
    root.mkdir(parents=True, exist_ok=True)
    name = uuid4().hex + suffix
    (root / name).write_bytes(content)
    return "/api/artifacts/" + name
