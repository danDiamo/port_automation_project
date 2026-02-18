# TODO: Docstrings

"""
Happy-path unit & integration(ish) tests for processing.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pytest

import processing as processing_module
from processing import (
    CollectionContext,
    CollectionProcessor,
    ScoreProcessingOrchestrator,
    ScoreProcessor,
    ScoreSelection,
    ScoreSelectionMode,
)


# =============================================================================
# UNIT TESTS (initial setup)
# =============================================================================


def test_collection_context_paths(tmp_path: Path) -> None:
    collection_root = tmp_path / "Port"
    context = CollectionContext(collection_root=collection_root)

    assert context.xml_dir == collection_root / "Port_xml"
    assert context.incipit_mp3_dir == collection_root / "Port_incipit_mp3"
    assert (
            context.performance_mp3_dir == collection_root /
            "Port_performance_mp3"
    )
    assert context.slow_mp3_dir == collection_root / "Port_slow_mp3"


def test_score_selection(tmp_path: Path) -> None:
    sel_all = ScoreSelection.select_all_files_in_xml_dir()
    assert sel_all.by == ScoreSelectionMode.ALL_FILES_IN_XML_DIR
    assert sel_all.score_path is None
    assert sel_all.itma_id is None

    score_path = tmp_path / "alpha.xml"
    sel_path = ScoreSelection.select_single_file_by_path(score_path)
    assert sel_path.by == ScoreSelectionMode.SINGLE_FILE_BY_PATH
    assert sel_path.score_path == score_path
    assert sel_path.itma_id is None

    sel_id = ScoreSelection.select_single_file_by_itma_id("alpha")
    assert sel_id.by == ScoreSelectionMode.SINGLE_FILE_BY_ITMA_ID
    assert sel_id.itma_id == "alpha"
    assert sel_id.score_path is None


def test_resolve_score_paths_for_all_files_in_xml_dir(tmp_path: Path) -> None:
    collection_root = tmp_path / "Port"
    xml_dir = collection_root / "Port_xml"
    xml_dir.mkdir(parents=True)

    alpha_xml = xml_dir / "alpha.xml"
    beta_musicxml = xml_dir / "beta.musicxml"
    alpha_xml.write_text("<xml/>", encoding="utf-8")
    beta_musicxml.write_text("<xml/>", encoding="utf-8")

    cp = CollectionProcessor()
    context = CollectionContext(collection_root=collection_root)

    paths = cp._resolve_score_paths(
        context=context,
        selection=ScoreSelection.select_all_files_in_xml_dir(),
    )

    # Implementation returns *.xml (sorted) then *.musicxml (sorted)
    assert paths == [alpha_xml, beta_musicxml]


def test_resolve_single_score_paths_and_id_given_path(tmp_path: Path) -> None:
    collection_root = tmp_path / "Port"
    context = CollectionContext(collection_root=collection_root)

    p = tmp_path / "single.xml"
    p.write_text("<xml/>", encoding="utf-8")

    cp = CollectionProcessor()
    paths = cp._resolve_score_paths(
        context=context,
        selection=ScoreSelection.select_single_file_by_path(p),
    )

    assert paths == [p]


def test_resolve_single_score_paths_and_id_given_itma_id(
    tmp_path: Path,
) -> None:
    collection_root = tmp_path / "Port"
    xml_dir = collection_root / "Port_xml"
    xml_dir.mkdir(parents=True)

    alpha_xml = xml_dir / "alpha.xml"
    alpha_musicxml = xml_dir / "alpha.musicxml"
    alpha_xml.write_text("<xml/>", encoding="utf-8")
    alpha_musicxml.write_text("<xml/>", encoding="utf-8")

    cp = CollectionProcessor()
    context = CollectionContext(collection_root=collection_root)

    paths = cp._resolve_score_paths(
        context=context,
        selection=ScoreSelection.select_single_file_by_itma_id("alpha"),
    )

    assert paths == [alpha_xml]


# =============================================================================
# UNIT TESTS: ScoreProcessor (fake Score; no MusicXML parsing)
# =============================================================================


class _FakeScore:
    def detect_key(self) -> str:
        return "KS"

    def extract_mode_from_key_signature(self) -> str:
        return "mode"

    def extract_tonic_from_key_signature(self) -> str:
        return "tonic"

    def extract_time_signature(self) -> str:
        return "4/4"

    def count_number_of_parts(self) -> int:
        return 2

    def create_breathnach_codes(self) -> list[int]:
        return [1, 2, 3]

    def convert_score_to_pdf(self) -> str:
        return "pdf-path-or-s3-uri"

    def convert_incipit_to_svg(self) -> str:
        return "svg-path-or-s3-uri"

    def write_score_to_midi(self) -> str:
        return "midi-path-or-s3-uri"

    def convert_incipit_to_mp3(self) -> str:
        return "mp3-path-or-s3-uri"

    def convert_score_to_abc(self) -> str:
        return "abc-path-or-s3-uri"

    def create_soundslice_slice(
        self,
        *,
        _folder_id: int | None,
    ) -> str:
        return "https://www.soundslice.com/slices/fake/embed/"

    def copy_musicxml_file_to_aws(self, *, collection_root: Path) -> str:
        return "s3://BUCKET_PLACEHOLDER/key.xml"


def test_score_processor_run_analysis_steps_all() -> None:
    sp = ScoreProcessor()
    fake = _FakeScore()
    steps = ScoreProcessingOrchestrator(analysis_methods=None)

    out = sp._run_analysis_steps(score=fake, processing_steps=steps)

    assert out == {
        "key_signature": "KS",
        "mode": "mode",
        "tonic": "tonic",
        "time_signature": "4/4",
        "number_of_parts": 2,
        "bb_code": [1, 2, 3],
    }


def test_score_processor_run_analysis_steps_subset() -> None:
    sp = ScoreProcessor()
    fake = _FakeScore()
    steps = ScoreProcessingOrchestrator(
        analysis_methods=["mode", "time_signature"]
    )

    out = sp._run_analysis_steps(score=fake, processing_steps=steps)

    assert set(out.keys()) == {"mode", "time_signature"}
    assert out["mode"] == "mode"
    assert out["time_signature"] == "4/4"


def test_score_processor_run_derivatives_steps_subset() -> None:
    sp = ScoreProcessor()
    fake = _FakeScore()
    steps = ScoreProcessingOrchestrator(
        derivative_methods=["abc_notation", "pdf_download"]
    )

    out = sp._run_derivatives_steps(score=fake, processing_steps=steps)

    assert set(out.keys()) == {"abc_notation", "pdf_download"}
    assert out["abc_notation"] == "abc-path-or-s3-uri"
    assert out["pdf_download"] == "pdf-path-or-s3-uri"


def test_score_processor_run_passthrough_aws_step_includes_optional_mp3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection_root = tmp_path / "Port"
    incipit_mp3_dir = collection_root / "Port_incipit_mp3"
    incipit_mp3_dir.mkdir(parents=True)

    itma_id = "alpha"
    (incipit_mp3_dir / f"{itma_id}.mp3").write_bytes(b"fake-mp3-content")

    def _fake_copy_mp3_to_aws(
        mp3_path: str | None,
        *,
        collection_root: str,
        bucket_name: str = "port.itma.ie",
    ) -> str | None:
        assert mp3_path is not None
        assert isinstance(collection_root, str)
        assert bucket_name  # not empty
        return "s3://BUCKET_PLACEHOLDER/key.mp3"

    monkeypatch.setattr(
        processing_module,
        "copy_mp3_to_aws",
        _fake_copy_mp3_to_aws
    )

    sp = ScoreProcessor()
    fake = _FakeScore()
    context = CollectionContext(collection_root=collection_root)

    out = sp._run_passthrough_aws_step(
        score=fake,
        itma_id=itma_id,
        context=context)

    assert out["musicxml"].startswith("s3://")
    assert out["incipit_audio"].startswith("s3://")


# =============================================================================
# INTEGRATION TESTS: CollectionProcessor.run() with real CSV I/O (offline)
# =============================================================================


def _make_collection_layout(
    tmp_path: Path,
    *,
    name: str = "Port",
) -> tuple[Path, Path]:
    collection_root = tmp_path / name
    xml_dir = collection_root / f"{name}_xml"
    xml_dir.mkdir(parents=True)
    return collection_root, xml_dir


def test_collection_processor_run_without_input_metadata_writes_new_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection_root, xml_dir = _make_collection_layout(tmp_path, name="Port")
    (xml_dir / "alpha.xml").write_text("<xml/>", encoding="utf-8")

    def _fake_process_single_score(**kwargs: Any) -> dict[str, dict[str, Any]]:
        assert kwargs["itma_id"] == "alpha"
        return {"alpha": {"title": "Alpha", "mode": "major"}}

    monkeypatch.setattr(
        ScoreProcessor,
        "process_single_score",
        staticmethod(_fake_process_single_score),
    )

    cp = CollectionProcessor()
    out_path = cp.run(
        collection_root=collection_root,
        selection=ScoreSelection.select_all_files_in_xml_dir(),
        processing_steps=ScoreProcessingOrchestrator(parallel=False),
        metadata_csv_path=None,
        save=True,
    )

    assert isinstance(out_path, str)
    out_file = Path(out_path)
    assert out_file.exists()
    assert out_file.stat().st_size > 0

    df = pd.read_csv(out_file, encoding="utf-8-sig")
    row = df.loc[df["slug"] == "alpha"].iloc[0]
    assert row["title"] == "Alpha"


def test_collection_processor_run_with_input_metadata_passes_metadata_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection_root, xml_dir = _make_collection_layout(tmp_path, name="Port")
    (xml_dir / "alpha.xml").write_text("<xml/>", encoding="utf-8")

    metadata_csv = collection_root / "metadata.csv"
    metadata_csv.write_text("slug,title\nalpha,My Title\n", encoding="utf-8")

    def _fake_process_single_score(**kwargs: Any) -> dict[str, dict[str, Any]]:
        assert kwargs["custom_title"] is None
        assert kwargs["has_metadata"] is True
        assert kwargs["collection_metadata"] is not None
        return {"alpha": {"title": "My Title"}}

    monkeypatch.setattr(
        ScoreProcessor,
        "process_single_score",
        staticmethod(_fake_process_single_score),
    )

    cp = CollectionProcessor()
    out_path = cp.run(
        collection_root=collection_root,
        selection=ScoreSelection.select_all_files_in_xml_dir(),
        processing_steps=ScoreProcessingOrchestrator(parallel=False),
        metadata_csv_path=metadata_csv,
        save=True,
    )

    assert isinstance(out_path, str)
    out_file = Path(out_path)
    assert out_file.exists()
    assert out_file.stat().st_size > 0


# =============================================================================
# PARALLEL BRANCH SMOKE TEST (no real processes)
# =============================================================================


class _FakeFuture:
    def __init__(self, result_obj: Any):
        self._result_obj = result_obj

    def result(self) -> Any:
        return self._result_obj


class _FakeExecutor:
    """
    Minimal ProcessPoolExecutor stand-in:
    - used as a context manager
    - submit() returns futures that already contain their result
    """

    def __init__(self, *, max_workers: int | None = None):
        self.max_workers = max_workers
        self._futures: list[_FakeFuture] = []

    def __enter__(self) -> "_FakeExecutor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def submit(self, fn: Callable[..., Any], /, **kwargs: Any) -> _FakeFuture:
        res = fn(**kwargs)
        fut = _FakeFuture(res)
        self._futures.append(fut)
        return fut


def test_collection_processor_run_parallel_smoke_aggregates_patches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection_root, xml_dir = _make_collection_layout(tmp_path, name="Port")
    (xml_dir / "alpha.xml").write_text("<xml/>", encoding="utf-8")
    (xml_dir / "beta.xml").write_text("<xml/>", encoding="utf-8")

    def _fake_process_single_score(**kwargs: Any) -> dict[str, dict[str, Any]]:
        itma_id = kwargs["itma_id"]
        return {itma_id: {"title": itma_id.capitalize()}}

    def _fake_as_completed(futures: list[_FakeFuture]):
        return list(reversed(futures))

    monkeypatch.setattr(
        processing_module,
        "ProcessPoolExecutor",
        _FakeExecutor
    )
    monkeypatch.setattr(processing_module, "as_completed", _fake_as_completed)
    monkeypatch.setattr(
        ScoreProcessor,
        "process_single_score",
        staticmethod(_fake_process_single_score),
    )

    cp = CollectionProcessor()
    out_path = cp.run(
        collection_root=collection_root,
        selection=ScoreSelection.select_all_files_in_xml_dir(),
        processing_steps=ScoreProcessingOrchestrator(
            parallel=True,
            max_workers=2
        ),
        metadata_csv_path=None,
        save=True,
    )

    assert isinstance(out_path, str)
    out_file = Path(out_path)
    assert out_file.exists()

    df = pd.read_csv(out_file, encoding="utf-8-sig")
    slugs = set(df["slug"].astype(str))
    assert {"alpha", "beta"} <= slugs