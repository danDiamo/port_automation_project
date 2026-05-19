# Port (macOS Apple Silicon) - Command Line Tool

**Package:** `Port-v2.0.2-macos-arm64.zip`

This package contains a standalone `port` executable, with Python 3.13 and all local dependencies installed “under the hood” (the user does not need to install Python or manage dependencies).

Some outputs require external tools (LilyPond / FFmpeg / FluidSynth). If these tools are not installed, Port will warn at startup and skip the affected derivatives when running in default “all derivatives” mode.

## Release notes for Port v2.0.2

- CLI changed to simplify metadata CSV input: --metadata_csv arg now requires a filename (including ‘.csv’ suffix) rather than a full absolute filepath.
- All Soundslice integration has been temporarily disabled, pending update of external API.
- Flow control logic bugs affecting partial processing runs have been fixed. README.md has been updated to reflect these changes (see section 5b).
- Incipit svg images now include key signature and force inclusion of a treble clef.
- Left-side incipit svg image padding has been decreased by 30px.
- Top padding of score content has been increased for all pages in PDF outputs.
- Persistent issue with overfull final lines in PDFs has been fixed.

## 1. Install `port` so you can run it from any Terminal

1. Unzip `Port-v2.0.2-macos-arm64.zip`.
   You will get a folder named `Port/`.

2. Move the *entire* `Port/` folder to your user Applications directory (recommended):

   ```
   mkdir -p "$HOME/Applications"
   mv "/path/to/Port" "$HOME/Applications/Port"
   ```

   - The first command creates the Applications directory if it does not already exist.
   - The second command moves the Port folder to Applications.

   **Important:** Do not move only the `port` executable out of the `Port/` folder.
   This is a one-folder app, and `port` needs the other bundled files next to it.

3. Run the installer script:

   ```
   cd "$HOME/Applications/Port"
   chmod +x ./install.sh
   ./install.sh
   ```

4. Verify installation in a **new** Terminal window/tab:

   ```
   which port
   port --help
   port --version
   ```

## 2. Automatically check dependencies and assets

Preflight step: run a preflight “doctor” check.

Before running, especially for the first time, you can automatically verify that:

- Port’s bundled assets are present (soundfont for MP3 generation and PDF footer image)
- External tools (`LilyPond`, `FFmpeg`, `FluidSynth`) are available on your `PATH` (required for some derivative outputs)

Run:\
`port doctor`

If any required item is missing, `port doctor` exits with status code `1` and prints a message telling the user what to install or fix.

## 3. Install external tools (recommended)

PDF and SVG outputs require LilyPond (which provides `lilypond` and `musicxml2ly` resources).
MP3 outputs require FluidSynth and FFmpeg.

Recommended installation method (using Homebrew via macOS Terminal):\
`brew install lilypond ffmpeg fluidsynth`

If you already have these external dependencies installed (via Homebrew or vendor/online installers), you do not need to reinstall them as long as the commands are available on your `PATH` and the versions are compatible.

You can verify via Terminal:\
`which lilypond musicxml2ly ffmpeg fluidsynth`


If this command cannot find them, please install them and try again.

## 4. Credentials setup (AWS + Soundslice)

Port needs credentials to access AWS (for passthrough uploads) and Soundslice (for slice creation). You can provide credentials in two ways:

### A) Use the bundled `.env` template (convenient, but less secure)

The Port release folder includes a template file:\
`text .env.template`

To use it:

1. Copy and rename it to `.env` in the **same** folder as the `port` executable (the `Port/` folder):

   ```bash
   cp .env.template .env
   ```

2. Edit `.env` and fill in the values and credentials for your AWS and Soundslice accounts into the blank fields (this is essentially the same procedure as per the `.env` file in ITMA’s legacy Andy Dickson Collection pipeline implementation).

**Notes:**

- Treat `.env` content as private and sensitive.
- If you provide or create a `.env` file, Port will read and use the embedded credentials by default.
- If CLI credential prompt flags are entered, they override any `.env` values for a single processing run only.
- If you do not provide a `.env` file, you will need to enter credentials manually via prompt flags for each run.

### B) Prompt via CLI (more secure; less convenient)

Credentials are entered at runtime; they are not written to disk by Port and are not recorded in Terminal logs.

Prompt for Soundslice credentials:
`port run ... --prompt-soundslice`

Prompt for AWS credentials:\
`port run ... --prompt-aws`


You can also use both flags in the same run.

This is a very secure, best-practice approach, but it is less convenient because credentials must be entered on each run.

## 5. Run Port

Port requires the user to provide the path to a collection root directory to run. The user can also provide an optional metadata CSV file.

If you do provide a metadata CSV, it must be stored inside the collection root directory. It should be named per `<collection_root>_metadata.csv` (i.e. for a collection root named test_collection, the CSV file should be named `test_collection_metadata.csv`). 

The metadata CSV file must contain a unique identifier column named `slug` (case-sensitive) containing a unique value for each score. Port will not be able to process the collection if this column is missing. Please see the Metadata Schema section below for more details on metadata formatting and management.

If you do not have a metadata CSV, simply omit the `--metadata-csv` option and Port will generate one from scratch.

**Important:** To ensure output derivatives/assets are written to the correct local and remote locations, input score XML files must be provided by the user in a single folder under the collection root named `<collection_root>_xml`.
For example, for a collection root named `test_collection`, input score files should be stored in `test_collection/test_collection_xml`.

### Typical full collection processing run

`port run --collection-root "/path/to/CollectionRoot" --metadata-csv "collection_metadata.csv"`

### Creating derivatives/assets only

`port run --collection-root "/path/to/CollectionRoot" --metadata-csv "collection_metadata.csv" --process derivatives`

### Running musicological analyses only

`port run --collection-root "/path/to/CollectionRoot" --metadata-csv "collection_metadata.csv" --process analysis`


Port can also process single score files instead of a full collection. There are two ways to select a score for processing:

### A) Select a single score file by ITMA id / slug (`--itma-id`)

Full processing (all workflows) for ONE score file selected by ITMA id / slug:
`port run --collection-root "/path/to/CollectionRoot"
--itma-id <ITMA_ID>
--process all`


**Note:** When selecting a score by `--itma-id`, the score file must exist inside the collection’s XML folder.

### B) Select a single score file by filepath (`--score-path`)

Full processing (all workflows) for ONE file selected by filepath:\
`port run --collection-root "/path/to/CollectionRoot"
--score-path "/path/to/CollectionRoot/<collection_root>_xml/<ITMA_ID>.xml"
--process all`


**Notes:** (for both collection and score processing)

- `--collection-root` is always required, even if only processing a single file.
- Use quotes if any paths contain spaces, and check that the quotes are correctly formatted: they must be plain text (`"`) not formatted “curly” quotes.
- Input files can be either `.xml` or `.musicxml`.

## 5b. Re-processing Collections (Second and Subsequent Runs)

After your first processing run, you may want to re-run Port to:
- Regenerate derivatives (PDFs, SVGs, MP3s)
- Recompute analysis data (key signatures, Breathnach codes)
- Update Soundslice slices
- Process newly added scores

**IMPORTANT:** For second or subsequent runs, **OMIT** the `--metadata-csv` flag.

When `--metadata-csv` is omitted, Port automatically loads the processed metadata file from your previous run (`collection_metadata_processed.csv`). This preserves all existing data while allowing you to update or regenerate selected outputs.

### Examples

**First run - provide raw metadata CSV:**

```bash
port run --collection-root "/path/to/CollectionRoot" \
  --metadata-csv "collection_metadata.csv" \
  --process all
```

**Second run - regenerate all derivatives (omit `--metadata-csv`)** 

```bash
port run --collection-root "/path/to/CollectionRoot" \
  --process derivatives
```

**Third run - recompute analysis data (omit `--metadata-csv`)**

```bash
port run --collection-root "/path/to/CollectionRoot" \
  --process analysis
```

**Fourth run - update Soundslice slices (omit `--metadata-csv`)**

```bash
port run --collection-root "/path/to/CollectionRoot" \
  --process soundslice
```

**Note:** You can bypass the automatic loading of metadata and explicitly provide an alternate metadata CSV if needed, for example if your metadata file has been renamed.
This metadata csv must also be stored inside the collection root directory.

```bash
port run --collection-root "/path/to/CollectionRoot" \
  --metadata-csv "collection_metadata_renamed.csv" \
  --process derivatives
```

## 6. Advanced functionality

The examples below illustrate how to run individual steps of the processing pipeline in advanced or non-standard configurations.

### A) Run a single processing step for an entire collection

For example, Soundslice only, with no analysis or derivatives:\
`port run --collection-root "/path/to/CollectionRoot" --metadata-csv "collection_metadata.csv" --process soundslice`


### B) Run two processing steps separately

Port’s CLI runs a single processing workflow per run (`analysis`, `soundslice`, `derivatives`, `passthrough-aws`, or `all`).

So the most reliable way to do bespoke combinations is via two separate Port runs:

1) Compute bb_code only (analysis subset):\
`port run --collection-root "/path/to/CollectionRoot" --metadata-csv "collection_metadata.csv"
--process analysis
--analysis-method bb_code`

2) Upload to Soundslice only:
`port run --collection-root "/path/to/CollectionRoot" --metadata-csv "collection_metadata.csv"
--process soundslice`


Port can also run in parallel mode, per the following example. Caveat: this functionality is still experimental/beta.

### C) Typical full collection run in parallel
`port run --collection-root "/path/to/CollectionRoot" --metadata-csv "collection_metadata.csv"
--process all
--parallel
--max-workers 8`


**Notes:**

- Start with `max-workers` equal to the number of local CPU cores and adjust if needed.
- Parallel mode increases load on your machine and on any external tools invoked during derivative creation.
- It may be advisable to contact Soundslice before interacting with their API in parallel mode.

## 7. CLI reference (commands and options)

Port uses a subcommand-based CLI. The primary commands are:\
 `port doctor port run [OPTIONS]`


### Global options

- `-h`, `--help`
  - Show help and exit.
- `--version`
  - Show version and exit.

### Doctor command

`port doctor`

Checks whether Port’s bundled assets are present and whether required external tools are available on `PATH`.

- Exit code: `0` on success, `1` if any required check fails
- Exit code `1` also prints a message explaining what needs to be installed or fixed

### Run command

`port run [OPTIONS]`


Run Port’s processing pipeline.

#### Required for `run`

- `--collection-root <PATH>`
  - Full path to the collection root directory. Use quotes if the path contains spaces.

#### Options for `run`

##### Score selection
Default: `--all` if none provided.

- `--all`
  - Process all MusicXML files in the collection’s `<collection_root>_xml` subdirectory.
- `--itma-id <SLUG>`
  - Process a single score by its ITMA id (`slug`). Input score file must be stored inside the collection root directory.
- `--score-path <PATH>`
  - Process a single score by its file path.

##### Workflow selection

- `--process <analysis|derivatives|soundslice|passthrough-aws|all>`
  - Choose which workflow option to run. Default is `all`.
  - `all`: Run entire Port workflow
  - `analysis`: Run musicological analyses only
  - `derivatives`: Generate all derivative outputs (`pdf`, `svg`, `mp3`, `MIDI`, `ABC Notation`)
  - `soundslice`: Create Soundslice slices
  - `passthrough-aws`: Upload passthrough assets to AWS (`MusicXML` and `mp3` files, if provided)

##### Metadata I/O

- `--metadata-csv <PATH>`
  - Optional input metadata CSV path (must be stored inside collection root directory).

By default, whether metadata is provided or not, Port will generate a new metadata CSV named per `<collection_root>_processed.csv`. If a metadata CSV is provided, it will have new columns and content added to record the various outputs and derivatives created by Port. If a metadata CSV is not provided, Port will generate one from scratch and record the same output columns and content.

- `--no-save`
  - Run processing but do not write any output CSV (useful for development and testing).

##### Parallel processing

- `--parallel`
  - Add this flag to enable parallel score processing. Caveat: this functionality is still experimental/beta.
- `--max-workers <INT>`
  - Maximum worker processes for parallel mode (no effect unless `--parallel` is set).

##### Select specific analysis method(s)

- `--analysis-method <key_signature|mode|tonic|time_signature|number_of_parts|bb_code>`
  - Run only the selected analysis method(s) chosen from the options listed above. If no `analysis-method` is provided, all analysis methods will run.

  - `key_signature`: calculate key signature
  - `mode`: extract mode from key signature
  - `tonic`: extract tonic from key signature
  - `time_signature`: extract time signature from score
  - `number_of_parts`: calculate number of parts in the score
  - `bb_code`: generate Breathnach code from incipit

##### Select specific derivative method(s)

- `--derivative-method <pdf_download|featured_image|midi_audio_full|incipit_audio|abc_notation>`
  - Run only the selected derivative method chosen from the options listed above. If no `derivative-method` is provided, all derivative methods run.

  - `pdf_download`: Generate PDF score
  - `featured_image`: Generate incipit SVG
  - `midi_audio_full`: Generate full score MIDI file
  - `incipit_audio`: Generate incipit audio file (MP3)
  - `abc_notation`: Create ABC Notation version of the score

##### Credential prompts

- `--prompt-soundslice`
  - Prompt for Soundslice credentials for the current run (overrides any `.env` values).
- `--prompt-aws`
  - Prompt for AWS credentials for the current run (overrides any `.env` values).

## 8. Logs / troubleshooting

Preflight/setup warnings are printed to the Terminal and also written to:
`~/Library/Logs/Port/`.


If you need support, please send the newest log file from that folder to support at `hello [at] atlanticarts [dot] net`.

Tip: If Port fails early or skips outputs, run:\
`port doctor`. This quickly highlights missing external tools or missing bundled assets.

## Appendix I: Metadata Schema

Port reads and writes metadata CSV files using a strict schema. Input CSVs:

- MUST contain the required unique identifier column: `slug`
- MUST NOT contain extra columns outside the schema (hard error)
- MAY omit some schema columns (Port will add them when writing output)

Field categories:

- **PRESERVE:** Port will not overwrite these fields if they already exist in the metadata table.
- **OVERWRITE:** Port may write or update these fields during processing.
- **CONSTANT:** Port always writes a fixed constant value to this field.

### Fields (in output order)

#### `slug`
- Description: Unique identifier field. Provided by ITMA.
- Type: PRESERVE

#### `title`
- Description: Item title. Provided by ITMA.
- Type: PRESERVE

#### `federated_search_term`
- Description: Modified/simplified content from `Title` field. Provided by ITMA.
- Type: PRESERVE

#### `alternative_title`
- Description: Item alt title. Provided by ITMA.
- Type: PRESERVE

#### `composer`
- Description: Score composer. Provided by ITMA.
- Type: PRESERVE

#### `tune_type`
- Description: Tune type (`Reel`, `Jig`, etc.). Provided by ITMA.
- Type: PRESERVE

#### `related_entries`
- Description: Related items in ITMA’s holdings. Provided by ITMA.
- Type: PRESERVE

#### `explore_tag`
- Description: Content is always `Port`.
- Type: CONSTANT
- Constant value: `Port`

#### `collection_tag`
- Description: Stores collection tag string, which is auto-derived by Port from collection root directory path.
- Type: OVERWRITE

#### `source`
- Description: Stores collection name as defined in ITMA catalogue. Provided by ITMA.
- Type: PRESERVE

#### `key_signature`
- Description: Holds key detected by the Music21 Krumhansl-Schmuckler key detection algorithm.
- Type: OVERWRITE

#### `mode`
- Description: Holds mode populated from key signature.
- Type: OVERWRITE

#### `tonic`
- Description: Holds tonic populated from key signature.
- Type: OVERWRITE

#### `time_signature`
- Description: Time signature as encoded in the score. Populated via Port’s time signature extraction.
- Type: OVERWRITE

#### `number_of_parts`
- Description: Number of structural parts in the score. Populated via Port’s part-counting heuristic, which calculates the number of double and final barlines in the score.
- Type: OVERWRITE

#### `abc_notation`
- Description: ABC Notation encoding of the score. Populated via the `abc_xml_converter` Python library’s ABC conversion.
- Type: OVERWRITE

#### `bb_code`
- Description: Breandán Breathnach code (8-value scale-degree sequence representing the melodic contour of the incipit). Populated via Port’s custom Breathnach code generation algorithm.
- Type: OVERWRITE

#### `featured_image`
- Description: Holds AWS path to the incipit SVG file.
- Type: OVERWRITE

#### `image_alt_text`
- Description: Content is always `Musical Notation`.
- Type: CONSTANT
- Constant value: `Musical Notation`

#### `summary`
- Description: Provided by ITMA, string formatted per `from <collection name>`.
- Type: PRESERVE

#### `main_textbox`
- Description: Provided by ITMA.
- Type: PRESERVE

#### `soundslice_iframe`
- Description: Holds Soundslice scorehash, populated via Soundslice API.
- Type: OVERWRITE

#### `score_track_title`
- Description: Performance MP3 track title. Provided by ITMA.
- Type: PRESERVE

#### `score_track_mp3`
- Description: Holds AWS URI for performance MP3 file. URI mirrors local file name and directory structure for any provided performance MP3 file stored in `<collection_root>/<collection_root>_performance_mp3/` subfolder.
- Type: OVERWRITE

#### `score_track_rights`
- Description: Content is always `In Copyright`.
- Type: CONSTANT
- Constant value: `In Copyright`

#### `score_track_catalog_url`
- Description: Provided by ITMA, online catalogue link for performance MP3 file.
- Type: PRESERVE

#### `score_track2_title`
- Description: Slow MP3 track title. Provided by ITMA.
- Type: PRESERVE

#### `score_track2_mp3`
- Description: Holds AWS URI for slow MP3 file. URI mirrors local file name and directory structure for a slow MP3 file provided in `<collection_root>/<collection_root>_slow_mp3/` subfolder.
- Type: OVERWRITE

#### `score_track2_rights`
- Description: Content is always `In Copyright`.
- Type: CONSTANT
- Constant value: `In Copyright`

#### `score_track2_catalog_url`
- Description: Provided by ITMA, online catalogue link for slow MP3 file.
- Type: PRESERVE

#### `video_url`
- Description: Provided by ITMA, YouTube embed code for video.
- Type: PRESERVE

#### `video_title`
- Description: Provided by ITMA, catalogue title field content for video.
- Type: PRESERVE

#### `video_catalog_url`
- Description: Provided by ITMA, online catalogue link for video file.
- Type: PRESERVE

#### `pdf_download`
- Description: Holds AWS URI for score PDF file created using Music21 and LilyPond.
- Type: OVERWRITE

#### `midi_audio_full`
- Description: Holds AWS URI for score MIDI file created using Music21.
- Type: OVERWRITE

#### `incipit_audio`
- Description: Holds AWS URI for incipit MP3 file created using FluidSynth, FFmpeg, and GeneralUser-GS soundfont.
- Type: OVERWRITE

#### `musicxml`
- Description: Holds AWS URI for a remote copy of an input MusicXML file. URI mirrors local file name and directory structure for any provided input MusicXML file stored in `<collection_root>/<collection_root>_xml/` subfolder.
- Type: OVERWRITE



