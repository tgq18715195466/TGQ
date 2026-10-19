"""Context-local file storage for a shared agent, with no shell capability."""
import os
import re
from pathlib import Path, PureWindowsPath
from threading import Lock

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol


def current_user_id() -> str | None:
    """Resolve auth lazily; malformed authenticated identities fail closed."""
    try:
        from backend.auth import current_identity
    except ModuleNotFoundError as exc:
        if exc.name not in {'backend', 'backend.auth'}:
            raise
        return None
    identity = current_identity.get()
    if identity is None:
        return None
    value = identity.get('id')
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Invalid user identity')
    user_id = str(value)
    if (not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', user_id)
            or PureWindowsPath(user_id).is_reserved()):
        raise ValueError('Invalid user identity')
    return user_id


def _resolved_path(path: Path) -> Path:
    # Windows may retain the extended-path prefix when a parent is created
    # concurrently. Normalize the spelling before comparing tenant boundaries.
    value = str(path.resolve())
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def user_directory(root: Path, user_id: str) -> Path:
    """Reject redirected tenant directories, including Windows junctions."""
    expected = _resolved_path(root) / 'users' / user_id
    if _resolved_path(expected) != expected:
        raise ValueError('User directory must not be redirected')
    return expected


class _FileOnlyFilesystemBackend(FilesystemBackend):
    def _ripgrep_search(self, *args, **kwargs):
        # Use the built-in Python literal search. No process is needed, and
        # UTF-8 files also work on Windows hosts whose default codec is GBK.
        return None, False


class UserFilesystemBackend(BackendProtocol):
    """Choose a fresh delegate per call; never store the active user's root.

    BackendProtocol's async methods use asyncio.to_thread, which copies the
    caller's ContextVars before invoking these synchronous delegates.
    """

    def __init__(self, root):
        self._base_root = _resolved_path(Path(root))
        self._init_lock = Lock()
        self._initialized = set()

    def _delegate(self):
        user_id = current_user_id()
        with self._init_lock:
            root = (user_directory(self._base_root, user_id) if user_id is not None
                    else self._base_root / 'system')
            if _resolved_path(root) != root:
                raise ValueError('Storage directory must not be redirected')
            root.mkdir(parents=True, exist_ok=True)
            if root not in self._initialized:
                source = Path(__file__).resolve().parents[1] / 'memory' / 'AGENTS.md'
                # Exclusive creation preserves a user's existing file and never
                # follows an existing AGENTS.md symlink when seeding.
                try:
                    with (root / 'AGENTS.md').open('xb') as target:
                        target.write(source.read_bytes())
                except FileExistsError:
                    pass
                self._initialized.add(root)
        return _FileOnlyFilesystemBackend(root_dir=root, virtual_mode=True)

    def ls(self, path):
        return self._delegate().ls(path)

    def read(self, file_path, offset=0, limit=2000):
        return self._delegate().read(file_path, offset, limit)

    def write(self, file_path, content):
        return self._delegate().write(file_path, content)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        return self._delegate().edit(file_path, old_string, new_string, replace_all)

    def grep(self, pattern, path=None, glob=None, *, max_count=None):
        return self._delegate().grep(pattern, path, glob, max_count=max_count)

    def glob(self, pattern, path=None):
        return self._delegate().glob(pattern, path)

    def delete(self, file_path):
        return self._delegate().delete(file_path)

    def upload_files(self, files):
        return self._delegate().upload_files(files)

    def download_files(self, paths):
        return self._delegate().download_files(paths)


def create_local_backend(root) -> BackendProtocol:
    return UserFilesystemBackend(root)


def execution_instructions():
    return (
        '\n运行规则：文件工具仅可访问当前用户的虚拟目录，工具无 shell 或 execute 能力。'
        '禁止执行任意命令、脚本，禁止访问宿主目录、数据库私有路径或其他用户文件。'
        '业务查询及操作统一通过经过鉴权的 MCP 工具完成。'
        '本地图表使用 generate_visualization；简单统计直接使用工具返回的数据。'
    )
