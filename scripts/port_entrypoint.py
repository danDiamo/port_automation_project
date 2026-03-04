"""
scripts/port_entrypoint.py

PyInstaller entrypoint for the packaged `port` executable.

Responsibilities:
- Harden PATH (helps locate Homebrew/legacy-installed external tools)
- Preflight-check external tool availability once per run
- If user runs `--process all|derivatives` WITHOUT explicit
--derivative-method, inject only the derivative methods that are supported
on this machine (skip-and-warn).
- If user explicitly requests a derivative method, do not override; let it
fail.
- Emit preflight warnings to both stdout and a log file under:
    ~/Library/Logs/Port/
"""

# TODO: Inspect

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from port.cli import main as port_main


def _prepend_to_path(dirs: Iterable[str]) -> None:
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]

    for d in reversed(list(dirs)):
        if d and d not in parts:
            parts.insert(0, d)

    os.environ["PATH"] = os.pathsep.join(parts)


def _which(exe: str) -> str | None:
    p = shutil.which(exe)
    return str(p) if p else None


def _has(exe: str) -> bool:
    return _which(exe) is not None


def _has_flag(argv: list[str], flag: str) -> bool:
    return flag in argv


def _get_flag_value(argv: list[str], flag: str) -> str | None:
    """
    Supports: --process all
    Does not support: --process=all
    """
    try:
        i = argv.index(flag)
    except ValueError:
        return None

    return argv[i + 1] if i + 1 < len(argv) else None


def _log_path() -> Path:
    base = Path.home() / "Library" / "Logs" / "Port"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return base / f"port-preflight-{stamp}.log"


def _emit(lines: list[str], *, log_file: Path) -> None:
    """
    Write lines to stdout and append them to the log file.
    """
    text = "\n".join(lines).rstrip() + "\n"
    print(text, end="")

    try:
        with log_file.open("a", encoding="utf-8", errors="replace") as fp:
            fp.write(text)
    except OSError:
        # Logging should never break the run.
        pass


def _compute_allowed_derivatives() -> tuple[list[str], list[str]]:
    """
    Returns: (allowed_derivative_methods, warning_lines)

    Derivative methods correspond to CLI `--derivative-method` choices.

    - pdf_download + featured_image require LilyPond toolchain (lilypond +
    musicxml2ly)
    - incipit_audio requires fluidsynth + ffmpeg
    - midi_audio_full + abc_notation require no external executables (per
    current design)
    """
    warnings: list[str] = []

    lilypond_ok = _has("lilypond") and _has("musicxml2ly")
    audio_ok = _has("fluidsynth") and _has("ffmpeg")

    allowed: list[str] = ["midi_audio_full", "abc_notation"]

    if lilypond_ok:
        allowed += ["pdf_download", "featured_image"]
    else:
        missing = [x for x in ("lilypond", "musicxml2ly") if not _has(x)]
        warnings.append(
            "WARNING: Skipping PDF/SVG derivatives because the following "
            "tool(s) "
            f"were not found on PATH: {', '.join(missing)}"
        )

    if audio_ok:
        allowed += ["incipit_audio"]
    else:
        missing = [x for x in ("fluidsynth", "ffmpeg") if not _has(x)]
        warnings.append(
            "WARNING: Skipping MP3 derivative because the following tool(s) "
            f"were not found on PATH: {', '.join(missing)}"
        )

    return allowed, warnings


def main() -> int:
    argv = sys.argv[1:]

    # Improve odds of finding Homebrew/legacy-installed tools.
    # This is safe even when the user runs from Terminal; PATH is just
    # augmented.
    _prepend_to_path(
        [
            "/opt/homebrew/bin",  # Apple Silicon Homebrew default
            "/usr/local/bin",  # older Homebrew and some legacy installs
        ]
    )

    # Always delegate help/version behavior to the real CLI (no mutations).
    if not argv or argv in (["-h"], ["--help"], ["--version"]):
        return port_main(argv)

    # IMPORTANT:
    # Auto-injecting methods only makes sense for `port run`.
    # For other subcommands (e.g. `port doctor`), this
    # causes argparse "unrecognized arguments" errors.
    if argv[0] != "run":
        return port_main(argv)

    # Respect explicit derivative choices: do not auto-skip; let it fail
    # later if needed.
    if _has_flag(argv, "--derivative-method"):
        return port_main(argv)

    process = _get_flag_value(argv, "--process") or "all"
    if process not in ("derivatives", "all"):
        return port_main(argv)

    allowed, warnings = _compute_allowed_derivatives()

    if warnings:
        log_file = _log_path()
        _emit(
            [
                "Port preflight (external tools):",
                f"  Log file: {log_file}",
                *[f"  {w}" for w in warnings],
                "",
            ],
            log_file=log_file,
        )

    # Inject explicit derivative list so Port runs only supported derivatives
    # in the default "run all derivatives" mode.
    new_argv = argv[:]
    for m in allowed:
        new_argv += ["--derivative-method", m]

    return port_main(new_argv)


if __name__ == "__main__":
    raise SystemExit(main())
