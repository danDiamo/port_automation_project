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
import subprocess
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


def test_cli_run_all_modes_with_multiple_scores_e2e(
    tmp_path,
    monkeypatch,
    capsys,
):
    """
    End-to-end integration test: Run full processing pipeline (`--process all`)
    on multiple scores with mocked external services.

    This test simulates a realistic user workflow and catches flow control bugs
    where disabled/unavailable external services are still invoked incorrectly
    (e.g., Soundslice API calls when folder_id is None).

    Mocks:
    - Soundslice API (returns None to simulate disabled state)
    - AWS S3 (using moto)
    - External tools (LilyPond, FFmpeg, FluidSynth via subprocess)
    - Progress bar (tqdm)
    """
    from moto import mock_aws
    from port.utils.aws_utils import create_s3_bucket
    from port import score as score_module
    from pypdf import PdfWriter

    # Clear the shared Soundslice list cache to ensure clean test state
    score_module.Score._soundslice_list_id_cache.clear()

    # ============================================================
    # Setup: Create test collection with 2 MusicXML files
    # ============================================================
    collection_root = tmp_path / "Test_Collection"
    xml_dir = collection_root / "Test_Collection_xml"
    xml_dir.mkdir(parents=True)

    # Create 2 minimal test MusicXML files
    for i in range(1, 3):
        xml_file = xml_dir / f"test_score_{i}.xml"
        xml_file.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" 
    "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1">
      <part-name>Music</part-name>
    </score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
      <note><pitch><step>D</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
      <note><pitch><step>E</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
      <note><pitch><step>F</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
    </measure>
  </part>
</score-partwise>
""",
            encoding="utf-8",
        )

    # ============================================================
    # Mock: Soundslice API (returns None = disabled state)
    # ============================================================
    def _mock_create_soundslice_list(collection_name):
        # Simulate disabled Soundslice: return None instead of list_id
        return None

    monkeypatch.setattr(
        "port.utils.soundslice_utils.create_soundslice_list",
        _mock_create_soundslice_list,
    )

    # Ensure Soundslice credentials don't trigger real API calls
    monkeypatch.setattr(
        score_module,
        "get_soundslice_credentials_from_env",
        lambda: ("FAKE_APP_ID", "FAKE_PASSWORD"),
    )

    # Mock the Soundslice Client to prevent instantiation errors
    # Even though list creation fails and returns None, the Client
    # is instantiated before that check
    class FakeSoundsliceClient:
        def __init__(self, app_id, password):
            pass  # Don't do anything

        def create_slice(self, **kwargs):
            # Should never be called because list_id will be None
            raise AssertionError(
                "create_slice should not be called when list creation fails"
            )

        def upload_slice_notation(self, *, scorehash: str, fp):
            # Should never be called
            raise AssertionError(
                "upload_slice_notation should not be called when list creation fails"
            )

    monkeypatch.setattr(score_module, "Client", FakeSoundsliceClient)

    # ============================================================
    # Mock: AWS S3 (using moto)
    # ============================================================
    mock_s3 = mock_aws()
    mock_s3.start()
    try:
        create_s3_bucket("port.itma.ie")

        # ============================================================
        # Mock: External subprocess tools (LilyPond, FFmpeg, FluidSynth)
        # ============================================================
        def _fake_subprocess_run(cmd, check, capture_output, text=None, shell=False):
            """
            Mock external tool invocations to avoid requiring real installations.
            """
            if isinstance(cmd, list) and cmd:
                tool = cmd[0]

                # Mock musicxml2ly (MusicXML to LilyPond converter)
                if tool == "musicxml2ly":
                    out_idx = cmd.index("-o") + 1
                    ly_path = Path(cmd[out_idx])
                    ly_path.write_text(
                        r"""
\version "2.24.0"
\header { title = "Test" }
{ c'4 d'4 e'4 f'4 }
""".lstrip(),
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

                # Mock lilypond (LilyPond PDF/SVG compiler)
                if tool == "lilypond":
                    out_idx = cmd.index("-o") + 1
                    output_stem = Path(cmd[out_idx])

                    # Determine output format based on flags
                    if "-dbackend=svg" in cmd:
                        # SVG output (cropped)
                        svg_path = output_stem.with_suffix(".cropped.svg")
                        svg_path.write_text(
                            '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                            encoding="utf-8",
                        )
                    else:
                        # PDF output
                        pdf_path = output_stem.with_suffix(".pdf")
                        writer = PdfWriter()
                        writer.add_blank_page(width=612, height=792)
                        with pdf_path.open("wb") as fp:
                            writer.write(fp)

                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

                # Mock fluidsynth (MIDI to WAV converter)
                if tool == "fluidsynth":
                    wav_idx = cmd.index("-F") + 1
                    wav_path = Path(cmd[wav_idx])
                    # Create minimal WAV file header
                    wav_path.write_bytes(b"RIFF" + b"\x00" * 40)
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

                # Mock ffmpeg (WAV to MP3 converter)
                if tool == "ffmpeg":
                    output_path = Path(cmd[-1])
                    output_path.write_bytes(b"ID3" + b"\x00" * 100)
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            raise AssertionError(f"Unexpected subprocess command: {cmd!r}")

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

        # Mock tool availability checks
        monkeypatch.setattr(score_module, "check_lilypond", lambda: True)

        def _fake_which(tool):
            return f"/usr/bin/{tool}" if tool in {
                "lilypond", "musicxml2ly", "fluidsynth", "ffmpeg"
            } else None

        monkeypatch.setattr("shutil.which", _fake_which)

        # ============================================================
        # Mock: Progress bar (avoid tqdm import requirement)
        # ============================================================
        bar = _FakeBar()

        def _fake_progress_bar(*, total: int, desc: str):
            assert total == 2  # We created 2 test scores
            return bar

        monkeypatch.setattr(cli, "_progress_bar", _fake_progress_bar)

        # ============================================================
        # Run: Execute CLI main() with --process all
        # ============================================================
        rc = cli.main(
            [
                "run",
                "--collection-root",
                str(collection_root),
                "--process",
                "all",
                "--no-save",  # Don't write CSV for this test
            ]
        )

        # ============================================================
        # Assertions: Verify successful completion
        # ============================================================
        assert rc == 0, "CLI should exit with status 0"
        assert bar.closed is True, "Progress bar should be closed"
        assert bar.updated == 2, "Progress bar should update once per score"

        # Verify no errors were printed
        captured = capsys.readouterr()
        assert "JSONDecodeError" not in captured.out
        assert "JSONDecodeError" not in captured.err
        assert "Traceback" not in captured.err

        # Verify processing message appears
        assert "No metadata changes to write" in captured.out

    finally:
        mock_s3.stop()