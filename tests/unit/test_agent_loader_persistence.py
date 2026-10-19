from datetime import datetime, timezone

from api_view.agent_loader import AgentLoader


class FakePersistence:
    def __init__(self):
        self.calls = []
        self.updated_at = datetime(2026, 9, 22, tzinfo=timezone.utc)

    async def get_all_thread_ids(self):
        self.calls.append(("get_all_thread_ids",))
        return ["thread-1"]

    async def get_session_updated_at(self, thread_id):
        self.calls.append(("get_session_updated_at", thread_id))
        return self.updated_at

    async def save_display_messages(self, thread_id, messages):
        self.calls.append(("save_display_messages", thread_id, messages))
        return True

    async def get_display_messages(self, thread_id):
        self.calls.append(("get_display_messages", thread_id))
        return [{"id": "m1"}]

    async def delete_session(self, thread_id):
        self.calls.append(("delete_session", thread_id))
        return True

    async def close(self):
        self.calls.append(("close",))


def make_loader(persistence):
    loader = object.__new__(AgentLoader)
    loader._persistence = persistence
    loader._agent = object()
    loader._initialized = True
    return loader


async def test_agent_loader_delegates_session_repository_operations():
    persistence = FakePersistence()
    loader = make_loader(persistence)
    messages = [{"id": "m1"}]

    assert await loader.get_all_thread_ids() == ["thread-1"]
    assert await loader.get_session_updated_at("thread-1") == persistence.updated_at
    assert await loader.save_display_messages("thread-1", messages) is True
    assert await loader.get_display_messages("thread-1") == messages
    assert await loader.delete_session("thread-1") is True

    assert persistence.calls == [
        ("get_all_thread_ids",),
        ("get_session_updated_at", "thread-1"),
        ("save_display_messages", "thread-1", messages),
        ("get_display_messages", "thread-1"),
        ("delete_session", "thread-1"),
    ]


async def test_agent_loader_close_releases_persistence_and_resets_agent():
    persistence = FakePersistence()
    loader = make_loader(persistence)

    await loader.close()

    assert persistence.calls == [("close",)]
    assert loader._agent is None
    assert loader._initialized is False
