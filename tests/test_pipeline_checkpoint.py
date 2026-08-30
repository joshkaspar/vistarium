from pathlib import Path
from unittest.mock import patch

from vistarium.nps_client import NPSCandidate
from vistarium.pipeline import (
    _filter_by_park,
    _load_checkpoint,
    _search_with_cache,
    _write_checkpoint_line,
)


def test_load_checkpoint_missing_file_returns_empty(tmp_path: Path):
    outcomes, processed = _load_checkpoint(tmp_path / "checkpoint.jsonl")
    assert outcomes == {}
    assert processed == set()


def test_write_then_load_round_trips(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.jsonl"
    _write_checkpoint_line(checkpoint, {"id": "a", "outcome": "catalog", "record": {"id": "a"}})
    _write_checkpoint_line(checkpoint, {"id": "b", "outcome": "duplicate"})

    outcomes, processed = _load_checkpoint(checkpoint)
    assert processed == {"a", "b"}
    assert outcomes["a"]["outcome"] == "catalog"
    assert outcomes["a"]["record"] == {"id": "a"}
    assert outcomes["b"]["outcome"] == "duplicate"


def test_load_checkpoint_skips_blank_lines(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.jsonl"
    checkpoint.write_text('{"id": "a", "outcome": "catalog", "record": {}}\n\n\n')
    outcomes, processed = _load_checkpoint(checkpoint)
    assert processed == {"a"}


def test_later_write_for_same_id_overwrites_in_memory_view(tmp_path: Path):
    # Simulates a resumed run re-processing an ID that had previously failed
    # and now succeeds -- the checkpoint file itself keeps both lines
    # (append-only), but _load_checkpoint's dict view takes the last one.
    checkpoint = tmp_path / "checkpoint.jsonl"
    _write_checkpoint_line(checkpoint, {"id": "a", "outcome": "download_failed"})
    _write_checkpoint_line(checkpoint, {"id": "a", "outcome": "catalog", "record": {"id": "a"}})
    outcomes, _ = _load_checkpoint(checkpoint)
    assert outcomes["a"]["outcome"] == "catalog"


def test_search_with_cache_calls_search_on_first_run(tmp_path: Path):
    cache = tmp_path / "candidates_cache.json"
    fake_candidates = [NPSCandidate(id="1", title="a"), NPSCandidate(id="2", title="b")]
    with patch(
        "vistarium.pipeline.nps_client.search_candidates", return_value=fake_candidates
    ) as m:
        result = _search_with_cache(cache, terms=None, refresh=False)
    m.assert_called_once()
    assert [c.id for c in result] == ["1", "2"]
    assert cache.exists()


def test_search_with_cache_skips_search_on_second_run(tmp_path: Path):
    cache = tmp_path / "candidates_cache.json"
    fake_candidates = [NPSCandidate(id="1", title="a")]
    with patch(
        "vistarium.pipeline.nps_client.search_candidates", return_value=fake_candidates
    ) as m:
        _search_with_cache(cache, terms=None, refresh=False)
        result = _search_with_cache(cache, terms=None, refresh=False)
    m.assert_called_once()  # only the first call hit the network
    assert [c.id for c in result] == ["1"]


def test_search_with_cache_refresh_forces_new_search(tmp_path: Path):
    cache = tmp_path / "candidates_cache.json"
    fake_candidates = [NPSCandidate(id="1", title="a")]
    with patch(
        "vistarium.pipeline.nps_client.search_candidates", return_value=fake_candidates
    ) as m:
        _search_with_cache(cache, terms=None, refresh=False)
        _search_with_cache(cache, terms=None, refresh=True)
    assert m.call_count == 2


def test_filter_by_park_case_insensitive_substring():
    candidates = [
        NPSCandidate(id="1", park="Kenai Fjords National Park"),
        NPSCandidate(id="2", park="Zion National Park"),
        NPSCandidate(id="3", park="Kenai Fjords National Park"),
    ]
    result = _filter_by_park(candidates, "kenai")
    assert [c.id for c in result] == ["1", "3"]


def test_filter_by_park_none_returns_all():
    candidates = [NPSCandidate(id="1", park="Zion National Park")]
    assert _filter_by_park(candidates, None) == candidates


def test_filter_by_park_no_match_returns_empty():
    candidates = [NPSCandidate(id="1", park="Zion National Park")]
    assert _filter_by_park(candidates, "Denali") == []
