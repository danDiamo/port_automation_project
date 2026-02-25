Port (macOS Apple Silicon / M1) - Command Line Tool
====================================================

Package: Port-<version>-macos-arm64.zip

This package contains a standalone `port` executable. You do NOT need to install
Python.

Some outputs require external tools (LilyPond / FFmpeg / FluidSynth). If those
tools are not installed, Port will warn at startup and skip the affected
derivatives when running in default "all derivatives" mode.

----------------------------------------------------
1) Install `port` so you can run it from any Terminal
----------------------------------------------------

1. Unzip Port-<version>-macos-arm64.zip
   You will get a folder named: Port/

2. Move the `port` executable into a personal bin folder:

   mkdir -p "$HOME/.local/bin"
   mv "/path/to/Port/port" "$HOME/.local/bin/port"
   chmod +x "$HOME/.local/bin/port"

3. Add ~/.local/bin to your PATH (zsh is default on macOS):

   echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
   source "$HOME/.zshrc"

4. Verify:

   which port
   port --help
   port --version


------------------------------------------
2) Install external tools (recommended)
------------------------------------------

PDF + SVG outputs require LilyPond (provides `lilypond` and `musicxml2ly`)
MP3 outputs require FluidSynth + FFmpeg

Recommended installation method (Homebrew):

  brew install lilypond ffmpeg fluidsynth

If you already have these installed (including via vendor installers), you do
NOT need to reinstall them, as long as the commands are available on your PATH.

You can verify:

  which lilypond musicxml2ly ffmpeg fluidsynth


----------------
3) Run Port
----------------

Typical full collection run:

  port run --collection-root "/path/to/CollectionRoot" --metadata-csv "/path/to/MetadataCSV"

Derivatives only:

  port run --collection-root "/path/to/CollectionRoot" --metadata-csv "/path/to/MetadataCSV" --process derivatives

Analysis only:

  port run --collection-root "/path/to/CollectionRoot" --metadata-csv "/path/to/MetadataCSV" --process analysis

If you don't have a MetadataCSV, simply omit the `--metadata-csv` option and Port will generate one for you.

-------------------------
4) Logs / troubleshooting
-------------------------

Preflight warnings are printed to the Terminal and also written to:

  ~/Library/Logs/Port/

If you need support, please send the newest log file from that folder to support.