"""
cli.py holds an argparse-based CLI for Port collection processing.
"""

from __future__ import annotations

import argparse
import os
from getpass import getpass
from pathlib import Path
from shutil import get_terminal_size

from importlib.metadata import PackageNotFoundError, version as pkg_version

from .processing import (
    CollectionProcessor,
    ProcessingMode,
    ScoreProcessingOrchestrator,
    ScoreSelection,
)

# argparse config lists

ANALYSIS_METHODS: tuple[str, ...] = (
    "key_signature",
    "mode",
    "tonic",
    "time_signature",
    "number_of_parts",
    "bb_code",
)

DERIVATIVE_METHODS: tuple[str, ...] = (
    "pdf_download",
    "featured_image",
    "midi_audio_full",
    "incipit_audio",
    "abc_notation",
)

PROCESS_CHOICES: tuple[str, ...] = (
    "analysis",
    "derivatives",
    "soundslice",
    "passthrough-aws",
    "all",
)


def _build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """
    Build and parse CLI arguments.
    Uses a `run` subcommand so we can add future subcommands without
    breaking the interface.
    """

    # helper added to format help output in cli
    class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
        def _split_lines(self, text: str, width: int):
            if text.startswith("R|"):
                return text[2:].splitlines()
            return super()._split_lines(text, width)

        def _get_help_string(self, action: argparse.Action) -> str:
            # Prevent argparse from appending " (default: ...)" to every help
            # line.
            return action.help or ""

    term_width = get_terminal_size(fallback=(120, 24)).columns
    help_width = min(140, max(100, term_width))

    parser = argparse.ArgumentParser(
        prog="port",
        description="Run score and/or collection processing workflows.",
        add_help=False,
        formatter_class=lambda prog: HelpFormatter(
            prog,
            max_help_position=40,
            width=help_width,
        ),
    )

    parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="Show help and exit.",
    )

    try:
        port_ver = pkg_version("port")
    except PackageNotFoundError:
        port_ver = "unknown"

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {port_ver}",
        help="Show version and exit.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run",
        help="This command will process a single score or a collection "
             "of scores, depending on the other options provided.",
        formatter_class=parser.formatter_class,
    )
    run.add_argument(
        "--collection-root",
        required=True,
        metavar="",
        type=Path,
        help="Enter the full path to the collection root folder on "
             "your local machine. Use quotes if there are any spaces in the "
             "path. This must be provided even if processing a single score.",
    )

    # Score selection: defaults to --all if none provided.
    sel = run.add_mutually_exclusive_group(required=False)
    sel.add_argument(
        "--all",
        action="store_true",
        help="Default collection processing option. Automatically detect and "
             "processes all MusicXML files in the collection "
             "MusicXML subdirectory. Note: collection root must "
             "contain a MusicXML subdirectory named per: "
             "<collection_root>_xml.",
    )
    sel.add_argument(
        "--score-path",
        type=Path,
        metavar="",
        help="Select and process a single score by its file path. Please "
             "enter file path. Use quotes if there are any spaces in the path."
    )
    sel.add_argument(
        "--itma-id",
        type=str,
        help="Select and process a single score by its unigue ITMA id ("
             "slug). Please enter slug value without quotes. Note: input "
             "score file must be stored inside collection root directory.",
    )

    run.add_argument(
        "--process",
        choices=PROCESS_CHOICES,
        default="all",
        metavar="",
        help=(
            "R|Choose which processing workflow to run. Default is 'all'.\n"
            "Options are (without quotes):\n"
            "  analysis         Run all musicological analyses\n"
            "  derivatives      Create derivatives\n"
            "  soundslice       Push score to Soundslice\n"
            "  passthrough-aws  Copy passthrough xml and mp3 assets to AWS\n"
            "  all              Run all processing"
        ),
    )

    run.add_argument(
        "--metadata-csv",
        type=Path,
        metavar="",
        default=None,
        help="Path to optional input metadata CSV file (must be stored inside "
             "collection root directory). Use quotes if there are any spaces "
             "in the path.",
    )

    run.add_argument(
        "--no-save",
        action="store_true",
        help="Run processing but do not write any output CSV. This is useful "
             "for development & testing and largely can be ignored in "
             "production."
    )

    run.add_argument(
        "--parallel",
        action="store_true",
        help="Add this flag to process scores in parallel."
    )
    run.add_argument(
        "--max-workers",
        type=int,
        metavar="",
        default=None,
        help="Enter an integer value setting max worker processes for "
             "parallel processing. Please be cautious when selecting number of"
             " workers; a safe starting point is to match the number of CPU "
             "cores on your local machine. Note: has no effect unless "
             "--parallel is selected.",
    )

    run.add_argument(
        "--analysis-method",
        action="append",
        choices=ANALYSIS_METHODS,
        metavar="",
        default=None,
        help=(
            "R|Run selected musicological analysis method(s). Can be "
            "called repeatedly in a single session. Options are:"
            "\n  key_signature"
            "\n  mode"
            "\n  tonic"
            "\n  time_signature"
            "\n  number_of_parts"
            "\n  bb_code"
        ),
    )
    run.add_argument(
        "--derivative-method",
        action="append",
        choices=DERIVATIVE_METHODS,
        metavar="",
        default=None,
        help=(
            "R|Run selected derivative-creation method(s). "
            "Can be called repeatedly in a single session. Options are:"
            "\n  pdf_download"
            "\n  featured_image"
            "\n  midi_audio_full"
            "\n  incipit_audio"
            "\n  abc_notation"
        ),
    )

    run.add_argument(
        "--prompt-soundslice",
        action="store_true",
        help="Add this flag to force a prompt for Soundslice credentials for "
             "the current user session (overrides any existing/hardcoded "
             "credentials)."
    )
    run.add_argument(
        "--prompt-aws",
        action="store_true",
        help="Add this flag to force a prompt for AWS credentials for "
             "the current user session (overrides any existing/hardcoded "
             "credentials)."
    )

    return parser, run

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args."""
    parser, _run = _build_parser()
    return parser.parse_args(argv)


def _selection_from_args(args: argparse.Namespace) -> ScoreSelection:
    """
    Convert selection flags into a ScoreSelection object.

    If the user doesn't enter a selection flag, we default to "all" and
    fully process the collection/score.
    """
    if args.score_path is not None:
        return ScoreSelection.select_single_file_by_path(args.score_path)

    if args.itma_id is not None:
        return ScoreSelection.select_single_file_by_itma_id(args.itma_id)

    return ScoreSelection.select_all_files_in_xml_dir()


def _processing_mode_from_args(args: argparse.Namespace) -> ProcessingMode:
    """Map CLI mode selection strings to ProcessingMode."""
    mapping = {
        "analysis": ProcessingMode.ANALYSIS,
        "derivatives": ProcessingMode.DERIVATIVES,
        "soundslice": ProcessingMode.SOUNDSLICE,
        "passthrough-aws": ProcessingMode.PASSTHROUGH_AWS,
        "all": ProcessingMode.ALL,
    }
    return mapping[str(args.process)]


def _progress_bar(*, total: int, desc: str):
    """
    Create a tqdm progress bar.

    We keep this in a helper so the rest of main() stays readable.
    """
    try:
        from tqdm import tqdm  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Progress bars require 'tqdm'. Add it to the project dependencies."
        ) from e

    return tqdm(total=total, unit="score", desc=desc)


def _progress_bar_label(mode: ProcessingMode) -> str:
    """TQDM progress bar label."""
    return {
        ProcessingMode.ANALYSIS: "Analyzing",
        ProcessingMode.DERIVATIVES: "Generating derivatives",
        ProcessingMode.SOUNDSLICE: "Uploading to Soundslice",
        ProcessingMode.PASSTHROUGH_AWS: "Syncing to AWS",
        ProcessingMode.ALL: "Processing",
    }[mode]


def _prompt_soundslice_credentials() -> None:
    """
    Prompt for Soundslice credentials and store them in environment variables.
    This overrides any .env-loaded values for the duration of this run only.
    """
    app_id = input("Soundslice application id: ").strip()
    password = getpass("Soundslice password: ").strip()

    os.environ["APPLICATION_ID"] = app_id
    os.environ["PASSWORD"] = password


def _prompt_aws_credentials() -> None:
    """
    Prompt for AWS credentials and store them in environment variables.
    boto3 reads these standard environment variables automatically.
    """
    access_key = input("AWS access key id: ").strip()
    secret_key = getpass("AWS secret access key: ").strip()
    region = input("AWS region (blank for default): ").strip()

    os.environ["AWS_ACCESS_KEY_ID"] = access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
    if region:
        os.environ["AWS_DEFAULT_REGION"] = region


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    if argv is None:
        import sys
        argv = sys.argv[1:]

    # If user runs `port` with no args, show help instead of raising an error.
    if not argv or argv in (["-h"], ["--help"]):
        _parser, run = _build_parser()
        run.print_help()
        return 0

    args = _parse_args(argv)

    if args.command != "run":
        raise RuntimeError(f"Unknown command: {args.command}")

    selection = _selection_from_args(args)
    mode = _processing_mode_from_args(args)

    # Prompt creds only if asked.
    # Otherwise, existing .env/env credentials apply.
    if args.prompt_soundslice:
        _prompt_soundslice_credentials()
    if args.prompt_aws:
        _prompt_aws_credentials()

    processing_steps = ScoreProcessingOrchestrator(
        mode=mode,
        analysis_methods=args.analysis_method,
        derivative_methods=args.derivative_method,
        parallel=bool(args.parallel),
        max_workers=args.max_workers,
        allow_new_rows=True,
    )

    processor = CollectionProcessor()

    # Resolve score paths first so tqdm progress bar has an accurate total.
    score_paths = processor.resolve_score_paths(
        collection_root=args.collection_root,
        selection=selection,
    )

    bar = _progress_bar(total=len(score_paths), desc=_progress_bar_label(mode))
    try:
        out_path = processor.run(
            collection_root=args.collection_root,
            selection=selection,
            processing_steps=processing_steps,
            metadata_csv_path=args.metadata_csv,
            save=not bool(args.no_save),
            progress=bar,
        )
    finally:
        bar.close()

    if out_path:
        print(out_path)
    else:
        print("No metadata changes to write.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())