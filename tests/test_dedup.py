from pathlib import Path

from vistarium.dedup import Deduplicator


def test_first_file_is_not_a_duplicate(tmp_path: Path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"hello world")
    dedup = Deduplicator()
    assert dedup.is_duplicate(f) is None


def test_identical_content_flagged_as_duplicate(tmp_path: Path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"hello world")
    b.write_bytes(b"hello world")
    dedup = Deduplicator()
    assert dedup.is_duplicate(a) is None
    assert dedup.is_duplicate(b) == a


def test_different_content_is_not_a_duplicate(tmp_path: Path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"hello world")
    b.write_bytes(b"goodbye world")
    dedup = Deduplicator()
    assert dedup.is_duplicate(a) is None
    assert dedup.is_duplicate(b) is None


def test_same_file_checked_twice_is_a_duplicate_of_itself():
    # Not a realistic pipeline path, but documents the actual behavior:
    # is_duplicate registers on first sight, so a second check of the same
    # path returns itself as the "existing" match.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        f.write(b"content")
        f.flush()
        path = Path(f.name)
        dedup = Deduplicator()
        assert dedup.is_duplicate(path) is None
        assert dedup.is_duplicate(path) == path
