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
#     install.sh
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
INSTALLER_SRC="release/install.sh"
ENV_TEMPLATE_SRC="release/.env.template"

WORK_DIR="release_build"
OUT_DIR="release_out"

# Prefer an explicit Apple Silicon Homebrew Python for all preflight checks.
# This prevents pyenv shims / Intel Homebrew Python from causing x86_64 detection.
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/opt/python@3.13/bin/python3.13}"

# Resolve repo root (so relative paths work regardless of where script is run from)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ASSET_SOUNDFONT="${REPO_ROOT}/src/port/assets/GeneralUser-GS.sf2"
ASSET_PDF_FOOTER="${REPO_ROOT}/src/port/assets/itma_footer.pdf"


# ---- Helpers ----

project_version() {
  "${PYTHON_BIN}" - <<'PY'
import tomllib
from pathlib import Path
data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
}

machine_arch() {
  "${PYTHON_BIN}" - <<'PY'
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
# - Entry point is the wrapper that adds preflight + derivative auto-selection (defaulting to 'run').

[[ -f "${ASSET_SOUNDFONT}" ]] || die "Missing asset in repo: ${ASSET_SOUNDFONT}"
[[ -f "${ASSET_PDF_FOOTER}" ]] || die "Missing asset in repo: ${ASSET_PDF_FOOTER}"

uv run pyinstaller \
  --noconfirm \
  --clean \
  --name "${EXE_NAME}" \
  --copy-metadata port \
  --collect-all port \
  --add-data "${ASSET_SOUNDFONT}:_internal/port/assets" \
  --add-data "${ASSET_PDF_FOOTER}:_internal/port/assets" \
  --distpath "${WORK_DIR}/dist" \
  --workpath "${WORK_DIR}/build" \
  --specpath "${WORK_DIR}/spec" \
  scripts/port_entrypoint.py

# ---- Ad-hoc Code Signing (bug fix for Python 3.13 + PyInstaller 6.x) ----
echo "Applying code signatures to prevent Gatekeeper blocks..."

APP_BUNDLE="${WORK_DIR}/dist/${EXE_NAME}"

# Sign the Python framework specifically (this is what's failing)
if [[ -d "${APP_BUNDLE}/_internal/Python.framework" ]]; then
  echo "  Signing Python.framework..."
  codesign --force --deep --sign - "${APP_BUNDLE}/_internal/Python.framework" 2>/dev/null || true
fi

# Sign the Python dylib if it exists as a standalone file
if [[ -f "${APP_BUNDLE}/_internal/Python" ]]; then
  echo "  Signing Python dylib..."
  codesign --force --sign - "${APP_BUNDLE}/_internal/Python" 2>/dev/null || true
fi

# Sign all other dylibs and .so files
echo "  Signing all .dylib and .so files..."
find "${APP_BUNDLE}/_internal" -type f \( -name "*.dylib" -o -name "*.so" \) \
  -exec codesign --force --sign - {} \; 2>/dev/null || true

# Sign the main executable last
echo "  Signing main executable..."
codesign --force --sign - "${APP_BUNDLE}/${EXE_NAME}" 2>/dev/null || true

# Verify the signature
if codesign --verify --verbose=4 "${APP_BUNDLE}/${EXE_NAME}" 2>&1 | grep -q "valid on disk"; then
  echo "  ✓ Code signing complete"
else
  echo "  ⚠ Warning: Signature verification returned warnings (may still work)"
fi

echo

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

# Copy installer into Port/
if [[ -f "${INSTALLER_SRC}" ]]; then
  cp "${INSTALLER_SRC}" "${OUT_DIR}/${APP_FOLDER_NAME}/install.sh"
  chmod +x "${OUT_DIR}/${APP_FOLDER_NAME}/install.sh" || true
else
  echo "WARNING: installer source not found at ${INSTALLER_SRC}. (Skipping install.sh copy.)"
fi

# Copy .env template into Port/
if [[ -f "${ENV_TEMPLATE_SRC}" ]]; then
  cp "${ENV_TEMPLATE_SRC}" "${OUT_DIR}/${APP_FOLDER_NAME}/.env.template"
else
  echo "WARNING: .env template source not found at ${ENV_TEMPLATE_SRC}. (Skipping .env.template copy.)"
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
  # Use -ry flags to preserve symlinks and recursively store with Unix permissions
  /usr/bin/zip -ry "../${ZIP_NAME}" "${APP_FOLDER_NAME}" >/dev/null
)

echo "Done:"
echo "  ${ZIP_NAME}"