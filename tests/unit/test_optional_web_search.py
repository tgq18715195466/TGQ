import builtins


def test_disabled_web_search_does_not_import_optional_zhipu_dependency(monkeypatch):
    monkeypatch.setenv("DISABLE_WEB_SEARCH", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from agent.main_agent import _load_optional_web_search

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "agent.tools.web_search" or name == "zai":
            raise AssertionError("disabled web search must not import optional dependencies")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert _load_optional_web_search() == []
