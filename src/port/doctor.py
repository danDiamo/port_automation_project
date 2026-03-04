"""
scripts/doctor.py

This script helps users verify
their environment before running long processing jobs.

Checks:
- Package assets exist (FAIL if missing)
- Required external tools exist on PATH (FAIL if missing)
- Prints tool versions when available (INFO)

Exit codes:
- 0: all required checks passed
- 1: one or more required checks failed
"""

# TODO: Inspect

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


def _run_version(cmd: list[str]) -> str | None:
    """
    Run a version command and return a
    PYTHONPATH=/path/to/port_automation_project/src python -m port --help
    string if available.

    Returns None on failure.
    """
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    out = (proc.stdout or proc.stderr or "").strip()
    if not out:
        return None

    return out.splitlines()[0].strip()


def _check_executable(name: str, *,
                      version_args: list[str] | None = None) -> CheckResult:
    """
    Check that an executable exists on PATH. If version_args is provided, also
    print a version line when possible.
    """
    path = shutil.which(name)
    if not path:
        return CheckResult(
            name=name,
            ok=False,
            message=f"Missing: {name!r} not found on PATH.",
        )

    msg = f"Found at {path}"
    if version_args:
        version = _run_version([name, *version_args])
        if version:
            msg += f" | {version}"

    return CheckResult(name=name, ok=True, message=msg)


def _check_file(label: str, path: Path) -> CheckResult:
    """
    Check that a file exists on disk.
    """
    if path.exists():
        return CheckResult(name=label, ok=True, message=f"Found: {path}")
    return CheckResult(name=label, ok=False, message=f"Missing: {path}")


def _asset_path(name: str) -> Path:
    """
    Resolve a package-owned asset path.

    We avoid relying on the current working directory so this works when Port
    is run from a portable folder or from source.
    """
    try:
        from port.utils.assets_utils import asset_path
    except Exception as e:
        raise RuntimeError(
            "Could not import Port to resolve package-owned assets. "
            "Ensure you are running this from an environment where Port is "
            "installed (e.g., after `uv sync`)."
        ) from e

    return asset_path(name)


def run_checks() -> list[CheckResult]:
    """
    Run all required checks.
    Keep this list explicit so it's easy to edit when prerequisites change.
    """
    results: list[CheckResult] = []

    # --- Package-owned assets ---
    results.append(
        _check_file("SoundFont", _asset_path("GeneralUser-GS.sf2"))
    )
    results.append(
        _check_file("PDF footer", _asset_path("itma_footer.pdf"))
    )

    # --- External tools required for derivatives ---
    results.append(_check_executable("lilypond", version_args=["--version"]))
    results.append(_check_executable("ffmpeg", version_args=["-version"]))
    results.append(_check_executable("fluidsynth", version_args=["--version"]))

    return results


def main(argv: list[str] | None = None) -> int:
    """
    Entry point. argv allows for future flags (e.g. --json).
    """
    _ = argv

    print("Port doctor")
    print(f"Python: {sys.version.split()[0]}")
    print(
        f"Platform: {platform.system()} {platform.release()} ({platform.machine()})"
    )
    print()

    results = run_checks()

    width = max(len(r.name) for r in results) if results else 10
    failed = 0

    for r in results:
        status = "OK" if r.ok else "FAIL"
        if not r.ok:
            failed += 1
        print(f"{status:4}  {r.name:<{width}}  {r.message}")

    print()
    if failed:
        print(f"Doctor result: FAIL ({failed} missing item(s))")
        return 1

    print("Doctor result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
