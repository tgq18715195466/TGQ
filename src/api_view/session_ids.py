"""Map public thread IDs to user-scoped checkpoint keys exactly once."""
import re
from fastapi import HTTPException

def public_thread_id(thread_id: str, user_id: str = "anonymous") -> str:
    if not re.fullmatch(r"[\w.@-]{1,128}", user_id):
        raise HTTPException(422, "Invalid user ID")
    prefix = user_id + ":"
    # Compatibility with same-user IDs previously returned by history.
    if thread_id.startswith(prefix):
        thread_id = thread_id[len(prefix):]
    if not re.fullmatch(r"[\w.-]{1,200}", thread_id):
        raise HTTPException(404, "Session not found")
    return thread_id

def storage_thread_id(thread_id: str, user_id: str = "anonymous") -> str:
    return f"{user_id}:{public_thread_id(thread_id, user_id)}"
