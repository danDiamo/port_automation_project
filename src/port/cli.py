"""
cli.py holds an argparse-based CLI for Port collection processing.
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
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

    sub.add_parser(
        "doctor",
        help="Check presence of required external tools and bundled assets "
             "before running.",
        formatter_class=parser.formatter_class,
        description=(
            "Run preflight checks to verify Port's bundled assets exist and "
            "that required external tools are available on PATH."
        ),
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
    """
    Convert CLI argument to ProcessingMode enum.

    Since enum values now match CLI strings exactly, we can do direct conversion.
    """
    return ProcessingMode(str(args.process))


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
    This overrides any .env.template-loaded values for the duration of this run only.
    """
    app_id = input("Soundslice application id: ").strip()
    password = getpass("Soundslice password: ").strip()

    os.environ["APPLICATION_ID"] = app_id
    os.environ["PASSWORD"] = password


def _prompt_aws_credentials() -> None:
    """
    Prompt for AWS credentials and store them in environment variables.
    boto3 reads these standard environment variables automatically.

    Note:
        AWS Region is intentionally omitted from prompts as it is hard-coded
        to 'eu-west-1'.
    """
    access_key = input("AWS access key id: ").strip()
    secret_key = getpass("AWS secret access key: ").strip()

    os.environ["AWS_ACCESS_KEY_ID"] = access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key


def _load_dotenv_from_executable_dir() -> None:
    """
    Load .env file from a stable location.

    - In packaged (PyInstaller) builds: load from the executable directory.
      This supports the release layout:
        Port/
          port
          .env.template  (user copies to .env)
          .env           (optional; contains credentials)

      We intentionally do NOT load from the current working directory, so users
      can run `port ...` from anywhere.

    - In dev (running from source): load from the repo root .env so local
      development and PyCharm run configs behave as expected.
    """
    import sys

    is_frozen = bool(getattr(sys, "frozen", False))

    if is_frozen:
        exe_path = Path(sys.executable).resolve()
        dotenv_path = exe_path.parent / ".env"
    else:
        # src/port/cli.py -> repo root is two parents up from "src"
        repo_root = Path(__file__).resolve().parents[2]
        dotenv_path = repo_root / ".env"

    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=False)


def _resolve_metadata_csv_path(
        metadata_csv: Path,
        collection_root: Path,
) -> Path:
    """
    Resolve metadata CSV path intelligently:
    - If absolute path is provided, use it as-is
    - If relative path is provided, resolve it relative to collection root
    - Support shell expansion (~/...)

    Args:
        metadata_csv: Path provided by user via --metadata-csv
        collection_root: Collection root directory

    Returns:
        Resolved absolute Path to metadata CSV

    Raises:
        FileNotFoundError: If the resolved path doesn't exist
    """
    # Expand user home directory if present
    csv_path = metadata_csv.expanduser()

    # If it's already absolute, use it as-is
    if csv_path.is_absolute():
        resolved_path = csv_path.resolve()
    else:
        # Relative path - resolve relative to collection root
        collection_root_resolved = collection_root.expanduser().resolve()
        resolved_path = (collection_root_resolved / csv_path).resolve()

    # Check if file exists and provide helpful error message
    if not resolved_path.exists():
        if csv_path.is_absolute():
            raise FileNotFoundError(
                f"Metadata CSV file not found at: {resolved_path}\n"
                f"Please check the path and try again."
            )
        else:
            raise FileNotFoundError(
                f"Metadata CSV file not found.\n"
                f"  Looking for: {csv_path}\n"
                f"  In collection root: {collection_root.expanduser().resolve()}\n"
                f"  Full path: {resolved_path}\n"
                f"Please check the filename and try again."
            )

    return resolved_path


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    _load_dotenv_from_executable_dir()

    if argv is None:
        import sys
        argv = sys.argv[1:]

    # If user runs `port` with no args, show help instead of raising an error.
    if not argv or argv in (["-h"], ["--help"]):
        parser, run = _build_parser()

        # Top-level help (includes global options like --version)
        parser.print_help()

        # Also show the "run" subcommand help
        # so users see all options in one place.
        print("\n" + ("-" * 80))
        print("Run command help (most common):\n")
        run.print_help()
        return 0

    args = _parse_args(argv)

    if args.command == "doctor":
        from .doctor import main as doctor_main
        return int(doctor_main([]))

    if args.command != "run":
        raise RuntimeError(f"Unknown command: {args.command}")

    selection = _selection_from_args(args)
    mode = _processing_mode_from_args(args)

    # Prompt creds only if asked.
    # Otherwise, existing .env.template/env credentials apply.
    if args.prompt_soundslice:
        _prompt_soundslice_credentials()
    if args.prompt_aws:
        _prompt_aws_credentials()

    # Resolve metadata CSV path relative to collection root if needed
    metadata_csv_path = None
    if args.metadata_csv is not None:
        metadata_csv_path = _resolve_metadata_csv_path(
            args.metadata_csv,
            args.collection_root
        )

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
            metadata_csv_path=metadata_csv_path,
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