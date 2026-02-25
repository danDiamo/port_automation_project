#!/usr/bin/env bash
set -euo pipefail

# scripts/package_macos_arm64.sh
#
# Build and package a macOS arm64 (Apple Silicon) one-folder PyInstaller release.
#
# Output:
#   Port-<version>-macos-arm64.zip
# Contents:
#   Port/
#     port            (executable)
#     README.txt
#     ...             (PyInstaller one-folder runtime files)
#
# Requirements:
# - Run on macOS arm64 (M1/M2)
# - `uv` available
# - PyInstaller available in the uv environment (recommended as a dev dependency)
#
# Usage:
#   ./scripts/package_macos_arm64.sh

APP_FOLDER_NAME="Port"
EXE_NAME="port"

README_SRC="release/README.txt"

WORK_DIR="release_build"
OUT_DIR="release_out"

# ---- Helpers ----

project_version() {
  python - <<'PY'
import tomllib
from pathlib import Path
data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
}

machine_arch() {
  python - <<'PY'
import platform
print(platform.machine())
PY
}

die() {
  echo "ERROR: $*" >&2
  exit 2
}

# ---- Preflight ----

VERSION="$(project_version)"
ARCH="$(machine_arch)"

if [[ "${ARCH}" != "arm64" ]]; then
  die "This script must be run on macOS arm64 (Apple Silicon). Detected: ${ARCH}"
fi

ZIP_NAME="Port-${VERSION}-macos-${ARCH}.zip"

echo "Packaging Port"
echo "  Version: ${VERSION}"
echo "  Arch:    ${ARCH}"
echo "  Output:  ${ZIP_NAME}"
echo

# Clean previous outputs
rm -rf "${WORK_DIR}" "${OUT_DIR}"
mkdir -p "${WORK_DIR}" "${OUT_DIR}"

# ---- Sync build environment (locked) ----
# Use dev extras so PyInstaller is available.
uv sync --extra dev

# Optional: run tests before packaging
# uv run pytest

# ---- Build (one-folder) ----
# Note:
# - Entry point is the wrapper that adds preflight + derivative auto-selection.
# - You can add a .spec later if you need to collect package metadata explicitly
#   for `port --version` (recommended if you hit PackageNotFoundError).
uv run pyinstaller \
  --noconfirm \
  --clean \
  --name "${EXE_NAME}" \
  --distpath "${WORK_DIR}/dist" \
  --workpath "${WORK_DIR}/build" \
  --specpath "${WORK_DIR}/spec" \
  scripts/port_entrypoint.py

# ---- Assemble deliverable folder Port/ ----
mkdir -p "${OUT_DIR}/${APP_FOLDER_NAME}"

# Copy PyInstaller one-folder runtime contents into Port/
cp -R "${WORK_DIR}/dist/${EXE_NAME}/." "${OUT_DIR}/${APP_FOLDER_NAME}/"

# Copy README into Port/
if [[ -f "${README_SRC}" ]]; then
  cp "${README_SRC}" "${OUT_DIR}/${APP_FOLDER_NAME}/README.txt"
else
  echo "WARNING: README source not found at ${README_SRC}. (Skipping README copy.)"
fi

# ---- Smoke test built executable ----
echo "Smoke test:"
"${OUT_DIR}/${APP_FOLDER_NAME}/${EXE_NAME}" --version || true
"${OUT_DIR}/${APP_FOLDER_NAME}/${EXE_NAME}" --help >/dev/null
echo "  OK"
echo

# ---- Create zip (zip contains top-level Port/ folder) ----
(
  cd "${OUT_DIR}"
  /usr/bin/zip -r "../${ZIP_NAME}" "${APP_FOLDER_NAME}" >/dev/null
)

echo "Done:"
echo "  ${ZIP_NAME}"