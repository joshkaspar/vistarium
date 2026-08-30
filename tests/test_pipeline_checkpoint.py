from pathlib import Path

from vistarium.pipeline import _load_checkpoint, _write_checkpoint_line


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
