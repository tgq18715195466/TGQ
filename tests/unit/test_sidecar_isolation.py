import asyncio
import sys
import types
from contextvars import ContextVar

import pytest
from fastapi import HTTPException
from deepagents.backends.protocol import FileUploadResponse
from agent.artifacts import artifact_dir, publish_artifact
from agent.backends.local_execution import create_local_backend
from api_view.api.artifacts import get_artifact


@pytest.fixture
def identity(monkeypatch):
    context = ContextVar('sidecar_identity', default=None)
    auth = types.ModuleType('backend.auth')
    auth.current_identity = context
    monkeypatch.setitem(sys.modules, 'backend.auth', auth)
    return context


def test_artifacts_are_private(tmp_path, monkeypatch, identity):
    monkeypatch.setenv('ERP_ARTIFACT_DIR', str(tmp_path))
    assert artifact_dir() == tmp_path
    public_name = publish_artifact(b'legacy', '.txt').rsplit('/', 1)[-1]
    identity.set({'id': 'alice'})
    name = publish_artifact(b'alice report', '.txt').rsplit('/', 1)[-1]
    assert artifact_dir() == tmp_path / 'users/alice'
    assert get_artifact(name).path.read_bytes() == b'alice report'
    identity.set({'id': 'bob'})
    for other_name in (name, public_name, '../' + name):
        with pytest.raises(HTTPException) as error:
            get_artifact(other_name)
        assert error.value.status_code == 404
    identity.set({'id': 'alice'})
    assert get_artifact(name).path.read_bytes() == b'alice report'


@pytest.fixture
def skills(tmp_path, monkeypatch):
    from agent.middlewares import skills_sync
    directory = tmp_path / 'source/example'
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text('approved skill')
    monkeypatch.setattr(skills_sync, 'LOCAL_SKILLS_DIR', directory.parent)
    return skills_sync.SkillsSyncMiddleware(create_local_backend(tmp_path / 'sandbox'))


async def test_skills_context_and_cache_are_per_user(skills, identity):
    async def worker(user):
        token = identity.set({'id': user})
        try:
            assert await skills.abefore_agent({}, None)
            assert skills.backend.download_files(['/skills/example/SKILL.md'])[0].content == b'approved skill'
            assert await skills.abefore_agent({}, None) is None
        finally:
            identity.reset(token)
    await asyncio.gather(worker('alice'), worker('bob'))
    assert skills.backend.download_files(['/skills/example/SKILL.md'])[0].error


def test_skills_retry_failed_upload(skills, identity, monkeypatch):
    identity.set({'id': 'alice'})
    original = skills.backend.upload_files
    monkeypatch.setattr(skills.backend, 'upload_files', lambda files: [FileUploadResponse(path=p, error='permission_denied') for p, _ in files])
    assert skills._sync_files() == []
    monkeypatch.setattr(skills.backend, 'upload_files', original)
    assert skills._sync_files() == ['example']


def test_skills_sequential_users_are_both_synced(skills, identity):
    for user in ('alice', 'bob'):
        identity.set({'id': user})
        assert skills.before_agent({}, None)
        assert skills.backend.download_files(['/skills/example/SKILL.md'])[0].content == b'approved skill'
        assert skills.before_agent({}, None) is None


def test_http_artifact_context_and_cache(tmp_path, monkeypatch, identity):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from api_view.api.artifacts import router
    monkeypatch.setenv('ERP_ARTIFACT_DIR', str(tmp_path))
    identity.set({'id': 'alice'})
    name = publish_artifact(b'alice secret', '.txt').rsplit('/', 1)[-1]
    identity.set(None)
    app = FastAPI()
    @app.middleware('http')
    async def test_identity(request: Request, call_next):
        token = identity.set({'id': request.headers['x-test-user']})
        try:
            return await call_next(request)
        finally:
            identity.reset(token)
    app.include_router(router, prefix='/api')
    with TestClient(app) as client:
        own = client.get('/api/artifacts/' + name, headers={'x-test-user': 'alice'})
        assert own.status_code == 200 and own.content == b'alice secret'
        assert 'no-store' in own.headers['cache-control']
        other = client.get('/api/artifacts/' + name, headers={'x-test-user': 'bob'})
        assert other.status_code == 404


async def test_concurrent_artifact_publishing(tmp_path, monkeypatch, identity):
    monkeypatch.setenv('ERP_ARTIFACT_DIR', str(tmp_path))
    async def worker(user):
        token = identity.set({'id': user})
        try:
            url = await asyncio.to_thread(publish_artifact, user.encode(), '.txt')
            assert (await asyncio.to_thread(get_artifact, url.rsplit('/', 1)[-1])).path.read_bytes() == user.encode()
            return url
        finally:
            identity.reset(token)
    urls = await asyncio.gather(*(worker(str(i)) for i in range(8)))
    assert len(set(urls)) == 8
