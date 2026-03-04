#!/usr/bin/env bash
set -euo pipefail

# Port installer (macOS)
# - Creates ~/.local/bin/port -> <this Port folder>/port
# - Removes macOS quarantine attribute recursively
# (prevents manual user management of macOS “permission” / Gatekeeper issues)
# - Normalizes permissions recursively
# (ensures bundled runtime files are readable)
# - Optionally adds ~/.local/bin to PATH (with prompt)
#
# Usage:
#   cd /path/to/Port
#   ./install.sh

say() { printf "%s\n" "$*"; }
warn() { printf "WARNING: %s\n" "$*" >&2; }
die() { printf "ERROR: %s\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1
  pwd -P
)"

PORT_EXE="${SCRIPT_DIR}/port"
BIN_DIR="${HOME}/.local/bin"
LINK_PATH="${BIN_DIR}/port"

[[ -f "$PORT_EXE" ]] || die "Expected executable not found: ${PORT_EXE}"

# --- Fix common macOS “manual permissions per dependency” issues ---
# 1) Clear macOS quarantine recursively
# 2) Check that folders and files are readable (& execute where needed)
#
# This runs without prompting; it’s part of the explicit user install action.
if command -v xattr >/dev/null 2>&1; then
  # Ignore failures: if attributes aren’t present, xattr exits non-zero.
  xattr -dr com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true
fi

# Ensure:
# - user can read/write, and execute directories (and any already-executable files)
# - others can read, and traverse directories
chmod -R u+rwX,go+rX "$SCRIPT_DIR" 2>/dev/null || true

mkdir -p "$BIN_DIR"
chmod +x "$PORT_EXE" || true

# Install symlink (replace if different)
if [[ -L "$LINK_PATH" ]]; then
  CURRENT_TARGET="$(readlink "$LINK_PATH" || true)"
  if [[ "$CURRENT_TARGET" == "$PORT_EXE" ]]; then
    say "Symlink already installed:"
    say "  ${LINK_PATH} -> ${PORT_EXE}"
  else
    say "Updating existing symlink:"
    say "  ${LINK_PATH} -> ${CURRENT_TARGET}"
    rm -f "$LINK_PATH"
    ln -s "$PORT_EXE" "$LINK_PATH"
    say "Installed:"
    say "  ${LINK_PATH} -> ${PORT_EXE}"
  fi
elif [[ -e "$LINK_PATH" ]]; then
  die "${LINK_PATH} already exists and is not a symlink. Please move it aside and re-run."
else
  ln -s "$PORT_EXE" "$LINK_PATH"
  say "Installed:"
  say "  ${LINK_PATH} -> ${PORT_EXE}"
fi

# PATH handling
path_contains_bin_dir() {
  echo "${PATH:-}" | tr ':' '\n' | grep -Fx "$BIN_DIR" >/dev/null 2>&1
}

detect_profile_file() {
  # Prefer the user's login shell; fall back to $SHELL.
  local shell_name=""
  shell_name="$(basename "${SHELL:-}")"

  case "$shell_name" in
    zsh)
      echo "${HOME}/.zshrc"
      ;;
    bash)
      # On macOS, bash often uses ~/.bash_profile for login shells.
      if [[ -f "${HOME}/.bash_profile" ]]; then
        echo "${HOME}/.bash_profile"
      else
        echo "${HOME}/.bashrc"
      fi
      ;;
    *)
      # Default to zsh on modern macOS; user can change if needed.
      echo "${HOME}/.zshrc"
      ;;
  esac
}

ensure_path_line_present() {
  local profile_file="$1"
  local line='export PATH="$HOME/.local/bin:$PATH"'

  mkdir -p "$(dirname "$profile_file")"

  if [[ -f "$profile_file" ]] && grep -Fqx "$line" "$profile_file"; then
    say "PATH already configured in: ${profile_file}"
    return 0
  fi

  say ""
  say "~/.local/bin is not currently on your PATH in this shell."
  say "Add it to your shell profile so you can type: port"
  say ""
  say "Proposed change to ${profile_file}:"
  say "  ${line}"
  say ""
  printf "Apply this change? [y/N]: "
  read -r reply || true

  case "${reply:-}" in
    y|Y|yes|YES)
      # Ensure file exists, then append with a preceding newline if needed.
      if [[ -f "$profile_file" ]] && [[ -s "$profile_file" ]]; then
        printf "\n%s\n" "$line" >> "$profile_file"
      else
        printf "%s\n" "$line" >> "$profile_file"
      fi
      say "Updated: ${profile_file}"
      ;;
    *)
      warn "Skipped PATH update. You can add ~/.local/bin to PATH manually."
      ;;
  esac
}

if path_contains_bin_dir; then
  say ""
  say "PATH looks good in this shell (already contains ${BIN_DIR})."
else
  PROFILE_FILE="$(detect_profile_file)"
  ensure_path_line_present "$PROFILE_FILE"
fi

say ""
say "Next steps:"
say "  1) Open a NEW Terminal window/tab (or run: exec \"${SHELL:-/bin/zsh}\" -l)"
say "  2) Run: port --help"