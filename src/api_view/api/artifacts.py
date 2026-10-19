"""Serve only explicitly published local artifacts."""
import re
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from agent.artifacts import artifact_dir

router = APIRouter()

@router.get("/artifacts/{name}")
def get_artifact(name: str):
    if not re.fullmatch(r"[0-9a-f]{32}\.(svg|md|txt|csv|json|png|pdf|xlsx)", name):
        raise HTTPException(404, "Attachment not found")
    root = artifact_dir().resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "Attachment not found")
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'", "Cache-Control": "private, no-store"}
    if path.suffix in {".svg", ".png"}:
        return FileResponse(path, media_type="image/svg+xml" if path.suffix == ".svg" else "image/png", headers=headers)
    return FileResponse(path, filename=name, headers=headers)
