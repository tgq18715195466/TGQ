import asyncio
import sys
import types
from contextvars import ContextVar

import pytest
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from agent.backends import local_execution


@pytest.fixture
def identity(monkeypatch):
    context = ContextVar('test_identity', default=None)
    auth = types.ModuleType('backend.auth')
    auth.current_identity = context
    monkeypatch.setitem(sys.modules, 'backend.auth', auth)
    return context


def test_no_shell_and_user_isolation(tmp_path, identity):
    (tmp_path / 'shared.txt').write_text('legacy secret')
    backend = local_execution.create_local_backend(tmp_path)
    assert isinstance(backend, BackendProtocol)
    assert not isinstance(backend, SandboxBackendProtocol)
    assert not hasattr(backend, 'execute')
    assert not hasattr(backend, 'aexecute')
    backend.write('/internal.txt', 'system secret')
    identity.set({'id': 'alice'})
    assert backend.write('/report.txt', 'alice secret').error is None
    identity.set({'id': 'bob'})
    assert backend.read('/report.txt').error
    assert backend.read('/shared.txt').error
    assert backend.read('/internal.txt').error
    assert backend.write('/report.txt', 'bob secret').error is None
    identity.set({'id': 'alice'})
    assert backend.download_files(['/report.txt'])[0].content == b'alice secret'
    assert (tmp_path / 'users/alice/report.txt').read_text() == 'alice secret'
    assert (tmp_path / 'system/internal.txt').read_text() == 'system secret'
    approved = local_execution.Path(local_execution.__file__).resolve().parents[1] / 'memory/AGENTS.md'
    assert backend.download_files(['/AGENTS.md'])[0].content == approved.read_bytes()


@pytest.mark.parametrize('path', ['../secret.txt', '/../../secret.txt', r'..\secret.txt', 'C:/Windows/win.ini', r'C:\Windows\win.ini'])
def test_paths_cannot_escape(tmp_path, identity, path):
    identity.set({'id': 'alice'})
    backend = local_execution.create_local_backend(tmp_path / 'sandbox')
    (tmp_path / 'secret.txt').write_text('host secret')
    try:
        result = backend.download_files([path])[0]
    except ValueError:
        return
    assert result.error and result.content is None


@pytest.mark.parametrize('user_id', ['../bob', '/bob', r'..\bob', '', '.', 'CON', 'alice.', 'a:b', None])
def test_invalid_identity_fails_closed(tmp_path, identity, user_id):
    identity.set({'id': user_id})
    with pytest.raises(ValueError):
        local_execution.create_local_backend(tmp_path).ls('/')


async def test_concurrent_contexts_and_async_protocol(tmp_path, identity):
    backend = local_execution.create_local_backend(tmp_path)
    async def worker(user):
        token = identity.set({'id': user})
        try:
            assert (await backend.awrite('/report.txt', user)).error is None
            await asyncio.sleep(0)
            assert (await backend.aread('/report.txt')).file_data['content'] == user
            assert (await backend.aedit('/report.txt', user, user + '-edited')).error is None
            assert (await backend.aupload_files([('/nested/data.txt', user.encode())]))[0].error is None
            assert (await backend.adownload_files(['/nested/data.txt']))[0].content == user.encode()
            assert (await backend.als('/')).error is None
            assert (await backend.aglob('*.txt')).error is None
            assert (await backend.agrep(user, '/')).error is None
            assert (await backend.adelete('/nested/data.txt')).error is None
            assert (await backend.adownload_files(['/nested/data.txt']))[0].error
        finally:
            identity.reset(token)
    await asyncio.gather(*(worker(str(i)) for i in range(8)))


def test_instructions():
    text = local_execution.execution_instructions()
    assert 'generate_visualization' in text
    assert 'shell' in text
    assert '数据库' in text



def test_existing_host_file_cannot_be_read(tmp_path, identity):
    secret = tmp_path / 'secret.txt'
    secret.write_text('host secret')
    identity.set({'id': 'alice'})
    backend = local_execution.create_local_backend(tmp_path / 'sandbox')
    for path in (str(secret), secret.as_posix(), '/../../../secret.txt'):
        try:
            response = backend.download_files([path])[0]
        except ValueError:
            continue
        assert response.error and response.content is None


def test_protocol_surface_is_covered():
    methods = {name for name, method in vars(BackendProtocol).items()
               if not name.startswith('_') and callable(method)}
    backend = local_execution.create_local_backend('.')
    assert methods == {'ls', 'als', 'read', 'aread', 'write', 'awrite',
                       'edit', 'aedit', 'grep', 'agrep', 'glob', 'aglob',
                       'delete', 'adelete', 'upload_files', 'aupload_files',
                       'download_files', 'adownload_files'}
    for method in methods:
        assert callable(getattr(backend, method))


def test_same_users_concurrent_first_initialization(tmp_path, identity):
    from concurrent.futures import ThreadPoolExecutor
    backend = local_execution.create_local_backend(tmp_path)
    def worker(index):
        token = identity.set({'id': 42})
        try:
            assert backend.write(f'/file-{index}.txt', str(index)).error is None
            return backend.download_files(['/AGENTS.md'])[0].content
        finally:
            identity.reset(token)
    with ThreadPoolExecutor(max_workers=8) as pool:
        contents = list(pool.map(worker, range(16)))
    assert contents[0]
    assert all(content == contents[0] for content in contents)
