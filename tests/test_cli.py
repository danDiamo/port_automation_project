"""
This file holds happy-path tests for the argparse-based CLI in port.cli.py.

These tests intentionally avoid:
- calling AWS or Soundslice
- needing tqdm installed
- running any real score processing

They focus on "does parsing work?" and "does main() wire arguments into the
processor correctly?"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from port import cli


class _FakeBar:
    """Tiny progress-bar stand-in (tqdm-like API)."""

    def __init__(self) -> None:
        self.updated = 0
        self.closed = False

    def update(self, n: int = 1) -> None:
        self.updated += int(n)

    def close(self) -> None:
        self.closed = True


class _FakeProcessor:
    """CollectionProcessor stand-in used to test main() wiring."""

    def __init__(self, score_paths: list[Path], out_path: str | None) -> None:
        self._score_paths = list(score_paths)
        self._out_path = out_path

        self.resolve_calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []

    def resolve_score_paths(
            self,
            *,
            collection_root: Path,
            selection: Any
    ) -> list[Path]:
        """
        Mock CollectionProcessor.resolve_score_paths for testing purposes.
        """
        self.resolve_calls.append(
            {"collection_root": collection_root, "selection": selection}
        )
        return list(self._score_paths)

    def run(
        self,
        *,
        collection_root: Path,
        selection: Any,
        processing_steps: Any,
        metadata_csv_path: Path | None = None,
        progress: Any | None = None,
        save: bool = True,
    ) -> str | None:
        """
        Mock CollectionProcessor.run for testing purposes.
        """
        self.run_calls.append(
            {
                "collection_root": collection_root,
                "selection": selection,
                "processing_steps": processing_steps,
                "metadata_csv_path": metadata_csv_path,
                "progress": progress,
                "save": save,
            }
        )
        return self._out_path


def test_parse_args_defaults(tmp_path: Path) -> None:
    args = cli._parse_args(
        ["run", "--collection-root", str(tmp_path)]
    )

    assert args.command == "run"
    assert args.collection_root == tmp_path
    assert args.process == "all"
    # No selection flags provided => selection defaults to "all"
    assert args.all is False
    assert args.score_path is None
    assert args.itma_id is None


def test_parse_args_repeatable_analysis_method(tmp_path: Path) -> None:
    args = cli._parse_args(
        [
            "run",
            "--collection-root",
            str(tmp_path),
            "--process",
            "analysis",
            "--analysis-method",
            "mode",
            "--analysis-method",
            "time_signature",
        ]
    )

    assert args.process == "analysis"
    assert args.analysis_method == ["mode", "time_signature"]


def test_main_wires_args_to_processor_and_prints_out_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    collection_root = tmp_path / "Port"
    fake_paths = [collection_root / "a.xml", collection_root / "b.xml"]
    fake_out = str(collection_root / "Port_metadata_processed.csv")

    processor = _FakeProcessor(score_paths=fake_paths, out_path=fake_out)
    bar = _FakeBar()

    # Avoid tqdm import: replace progress bar factory with fake.
    def _fake_progress_bar(*, total: int, desc: str):
        assert total == len(fake_paths)
        assert isinstance(desc, str) and desc
        return bar

    monkeypatch.setattr(cli, "_progress_bar", _fake_progress_bar)

    # Swap out the real CollectionProcessor with our fake instance.
    # main.main() constructs CollectionProcessor(), so we patch the name to
    # a tmp object.
    monkeypatch.setattr(cli, "CollectionProcessor", lambda: processor)

    rc = cli.main(
        [
            "run",
            "--collection-root",
            str(collection_root),
            "--itma-id",
            "alpha",
            "--process",
            "analysis",
            "--analysis-method",
            "mode",
        ]
    )
    assert rc == 0

    # resolve_score_paths was called once
    assert len(processor.resolve_calls) == 1
    assert processor.resolve_calls[0]["collection_root"] == collection_root

    # run() was called once with progress bar passed through
    assert len(processor.run_calls) == 1
    assert processor.run_calls[0]["collection_root"] == collection_root
    assert processor.run_calls[0]["progress"] is bar

    # main prints the output path
    out = capsys.readouterr().out
    assert fake_out in out

    # bar is always closed
    assert bar.closed is True