"""Bounded agent tool query validation."""
import pytest
from knowledge_task_api.agent_bridge import ReadOnlyToolBridge

@pytest.mark.parametrize("query", ["!!!", " ".join(["word"] * 13), "@" * 512])
def test_bad_queries_never_call_backend(client, monkeypatch, query):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected HTTP call")
    monkeypatch.setattr(client, "get", forbidden)
    with pytest.raises(ValueError, match="searchable tokens"):
        ReadOnlyToolBridge(client, "demo-alice-local-only").execute("search_knowledge", {"query": query})

def test_valid_multilingual_query(client):
    result = ReadOnlyToolBridge(client, "demo-alice-local-only").execute("search_knowledge", {"query": "中文 检索", "limit": 1})
    assert result["ok"] and result["hits"] == []
