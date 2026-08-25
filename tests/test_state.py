"""Tests for core/state.py — SQLite-backed per-channel state."""
import pytest
from echidna.mythic.agent_functions.core.state import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


class TestPinnedCallbacks:
    def test_missing_returns_none(self, store):
        assert store.get_pinned(1) is None

    def test_set_and_get(self, store):
        store.set_pinned(1, 5)
        assert store.get_pinned(1) == 5

    def test_replace(self, store):
        store.set_pinned(1, 5)
        store.set_pinned(1, 9)
        assert store.get_pinned(1) == 9

    def test_clear(self, store):
        store.set_pinned(1, 5)
        store.clear_pinned(1)
        assert store.get_pinned(1) is None

    def test_channels_isolated(self, store):
        store.set_pinned(1, 5)
        assert store.get_pinned(2) is None


class TestTokenUsage:
    def test_missing_returns_none(self, store):
        assert store.get_tokens(1) is None

    def test_add_and_get(self, store):
        store.add_tokens(1, 100, 50)
        assert store.get_tokens(1) == {"input": 100, "output": 50}

    def test_accumulates(self, store):
        store.add_tokens(1, 100, 50)
        store.add_tokens(1, 25, 25)
        assert store.get_tokens(1) == {"input": 125, "output": 75}


class TestCutoff:
    def test_missing_returns_zero(self, store):
        assert store.get_cutoff(1) == 0

    def test_set_and_get(self, store):
        store.set_cutoff(1, 42)
        assert store.get_cutoff(1) == 42

    def test_replace(self, store):
        store.set_cutoff(1, 42)
        store.set_cutoff(1, 100)
        assert store.get_cutoff(1) == 100


def test_persists_across_instances(tmp_path):
    path = tmp_path / "state.db"
    s1 = StateStore(path)
    s1.set_pinned(7, 3)
    s1.add_tokens(7, 10, 5)
    s1.set_cutoff(7, 99)
    s1.close()

    s2 = StateStore(path)
    assert s2.get_pinned(7) == 3
    assert s2.get_tokens(7) == {"input": 10, "output": 5}
    assert s2.get_cutoff(7) == 99
    s2.close()
