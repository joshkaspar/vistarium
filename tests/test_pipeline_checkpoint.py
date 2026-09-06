import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from vistarium.nps_client import NPSCandidate
from vistarium.pipeline import (
    _filter_by_park,
    _load_checkpoint,
    _sample_candidates,
    _search_with_cache,
    _write_checkpoint_line,
    run,
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


def test_run_survives_unexpected_error_building_one_record(tmp_path: Path):
    # A source file large/malformed enough to trip PIL's decompression-bomb
    # guard (or any other unexpected error) should be skipped, not abort
    # every remaining candidate in the run -- see DECISIONS.md, 2026-08-31.
    workdir = tmp_path / "data"
    candidate = NPSCandidate(id="bad-1", park="Zion National Park", license="Public domain/Full")
    fake_image = tmp_path / "bad-1.jpg"
    fake_image.write_bytes(b"not a real image")

    with (
        patch("vistarium.pipeline._search_with_cache", return_value=[candidate]),
        patch("vistarium.pipeline.nps_client.download_image", return_value=fake_image),
        patch("vistarium.pipeline.Deduplicator.is_duplicate", return_value=None),
        patch("vistarium.pipeline.build_record", side_effect=RuntimeError("boom")),
    ):
        run(
            limit=10,
            workdir=workdir,
            out_path=workdir / "catalog.json",
            excluded_out_path=workdir / "excluded_non_photo.json",
            terms=None,
        )

    checkpoint_lines = (workdir / "checkpoint.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in checkpoint_lines]
    assert entries == [{"id": "bad-1", "outcome": "processing_error"}]
    assert json.loads((workdir / "catalog.json").read_text()) == []


def test_run_resumes_candidate_whose_image_survived_a_prior_crash(tmp_path: Path):
    # A run that crashes after download_image() but before the checkpoint
    # line is written (e.g. judge_image() raising, see DECISIONS.md
    # 2026-09-02) leaves an orphan file in images_dir. On resume,
    # Deduplicator pre-seeds every file already in images_dir -- including
    # this candidate's own file -- so download_image() (idempotent, returns
    # the existing path) must not have its own pre-seeded entry mistaken
    # for a real duplicate and silently dropped forever.
    workdir = tmp_path / "data"
    images_dir = workdir / "images"
    images_dir.mkdir(parents=True)
    image_path = images_dir / "orphan-1.jpg"
    image_path.write_bytes(b"fake jpeg bytes")

    candidate = NPSCandidate(
        id="orphan-1", park="Zion National Park", title="Orphan", license="Public domain/Full"
    )
    fake_record = {"id": "orphan-1", "is_photograph": True}

    with (
        patch("vistarium.pipeline._search_with_cache", return_value=[candidate]),
        patch("vistarium.pipeline.nps_client.download_image", return_value=image_path),
        patch("vistarium.pipeline.build_record", return_value=fake_record),
        patch("vistarium.pipeline.schema_validate.validate_record"),
    ):
        run(
            limit=10,
            workdir=workdir,
            out_path=workdir / "catalog.json",
            excluded_out_path=workdir / "excluded_non_photo.json",
            terms=None,
        )

    checkpoint_lines = (workdir / "checkpoint.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in checkpoint_lines]
    assert entries == [{"id": "orphan-1", "outcome": "catalog", "record": fake_record}]
    assert json.loads((workdir / "catalog.json").read_text()) == [fake_record]


def test_run_skips_undersized_originals_before_calling_build_record(tmp_path: Path):
    # A source original below MIN_WALLPAPER_LONG_EDGE isn't wallpaper-sized
    # and can never be published (see build_site.py) -- checked here,
    # before build_record()'s VLM call, so it doesn't cost a judge_image()
    # round trip for a candidate that's already a dead end. See
    # DECISIONS.md, 2026-09-05.
    workdir = tmp_path / "data"
    candidate = NPSCandidate(
        id="tiny-1", park="Zion National Park", title="Tiny", license="Public domain/Full"
    )
    fake_image = tmp_path / "tiny-1.jpg"
    Image.new("RGB", (499, 400), "red").save(fake_image)

    with (
        patch("vistarium.pipeline._search_with_cache", return_value=[candidate]),
        patch("vistarium.pipeline.nps_client.download_image", return_value=fake_image),
        patch("vistarium.pipeline.build_record") as mock_build_record,
    ):
        run(
            limit=10,
            workdir=workdir,
            out_path=workdir / "catalog.json",
            excluded_out_path=workdir / "excluded_non_photo.json",
            terms=None,
        )

    mock_build_record.assert_not_called()
    checkpoint_lines = (workdir / "checkpoint.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in checkpoint_lines]
    assert entries == [{"id": "tiny-1", "outcome": "too_small"}]
    assert json.loads((workdir / "catalog.json").read_text()) == []


def test_run_skips_undersized_originals_via_file_info_before_download(tmp_path: Path):
    # When the album API's FileInfo already gives dimensions (see
    # NPSCandidate.original_width/height), skip before ever calling
    # download_image() -- cheaper than the post-download pixel check
    # above, which is the fallback for search paths without FileInfo.
    workdir = tmp_path / "data"
    candidate = NPSCandidate(
        id="tiny-2",
        park="Zion National Park",
        title="Tiny",
        license="Public domain/Full",
        original_width=500,
        original_height=375,
    )

    with (
        patch("vistarium.pipeline._search_with_cache", return_value=[candidate]),
        patch("vistarium.pipeline.nps_client.download_image") as mock_download,
        patch("vistarium.pipeline.build_record") as mock_build_record,
    ):
        run(
            limit=10,
            workdir=workdir,
            out_path=workdir / "catalog.json",
            excluded_out_path=workdir / "excluded_non_photo.json",
            terms=None,
        )

    mock_download.assert_not_called()
    mock_build_record.assert_not_called()
    checkpoint_lines = (workdir / "checkpoint.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in checkpoint_lines]
    assert entries == [{"id": "tiny-2", "outcome": "too_small"}]


def test_run_does_not_skip_restricted_license_candidates(tmp_path: Path):
    # 2026-09-06: license is a client-side site filter (docs/app.js), not
    # a scrape-time exclusion -- reverted the same-day "license_excluded"
    # pre-download skip once the site started publishing restricted-
    # license records too (tagged, not hidden). See DECISIONS.md.
    workdir = tmp_path / "data"
    candidate = NPSCandidate(
        id="copyrighted-1",
        park="Zion National Park",
        title="Copyrighted",
        license="Restrictions apply on use and/or reproduction (Copyrighted material)/Full",
    )
    fake_image = tmp_path / "copyrighted-1.jpg"
    Image.new("RGB", (1920, 1080), "red").save(fake_image)
    fake_record = {"id": "copyrighted-1", "is_photograph": True}

    with (
        patch("vistarium.pipeline._search_with_cache", return_value=[candidate]),
        patch("vistarium.pipeline.nps_client.download_image", return_value=fake_image),
        patch("vistarium.pipeline.build_record", return_value=fake_record),
        patch("vistarium.pipeline.schema_validate.validate_record"),
    ):
        run(
            limit=10,
            workdir=workdir,
            out_path=workdir / "catalog.json",
            excluded_out_path=workdir / "excluded_non_photo.json",
            terms=None,
        )

    checkpoint_lines = (workdir / "checkpoint.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in checkpoint_lines]
    assert entries == [{"id": "copyrighted-1", "outcome": "catalog", "record": fake_record}]


def test_sample_candidates_returns_all_when_pool_smaller_than_limit():
    candidates = [NPSCandidate(id=str(i)) for i in range(5)]
    result = _sample_candidates(candidates, already_processed=set(), limit=10)
    assert {c.id for c in result} == {str(i) for i in range(5)}


def test_sample_candidates_excludes_already_processed():
    candidates = [NPSCandidate(id=str(i)) for i in range(5)]
    result = _sample_candidates(candidates, already_processed={"0", "1"}, limit=10)
    assert {c.id for c in result} == {"2", "3", "4"}


def test_sample_candidates_is_not_a_positional_slice():
    # Real regression motivation (2026-09-01): NPS's own default result
    # order isn't random, and a park's candidate pool can be huge (15,242
    # for Kenai Fjords via Categories:Scenic) -- a plain [:limit] slice
    # would silently bias every run toward whatever NPS sorts first.
    candidates = [NPSCandidate(id=str(i)) for i in range(1000)]
    result = _sample_candidates(candidates, already_processed=set(), limit=50)
    assert len(result) == 50
    # Overwhelmingly unlikely to be the first 50 IDs by chance if this is
    # a real random sample rather than a positional slice.
    assert {c.id for c in result} != {str(i) for i in range(50)}
