from __future__ import annotations

from typing import Any

from agent_os.memory.backends.mem0 import Mem0MemoryBackend


class FakeClient:
    def __init__(self, value: Any) -> None:
        self._value = value

    def search(self, query: str, *, filters: dict[str, str], top_k: int) -> Any:
        _ = (query, filters, top_k)
        return self._value


def _backend(value: Any) -> Mem0MemoryBackend:
    backend = Mem0MemoryBackend.__new__(Mem0MemoryBackend)
    backend._client = FakeClient(value)  # type: ignore[attr-defined]
    return backend


def test_mem0_search_non_collection_response_returns_empty() -> None:
    assert _backend(None).search("q", client_id="c1", user_id=None) == []
    assert _backend("bad").search("q", client_id="c1", user_id=None) == []
    assert _backend({"results": "bad"}).search("q", client_id="c1", user_id=None) == []


def test_mem0_search_dict_results_still_parses() -> None:
    hits = _backend({"results": [{"memory": "hello", "recorded_at": "t"}]}).search(
        "q", client_id="c1", user_id=None
    )
    assert len(hits) == 1
    assert hits[0].text == "hello"
    assert hits[0].metadata["recorded_at"] == "t"
