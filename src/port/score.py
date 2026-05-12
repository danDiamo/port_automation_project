"""This file holds a 'Score' class (with helper functions), modeling a single
digital music score."""

# TODO: type hinting

# built-in imports
import copy
import os
import platform
import shutil
import sys
import subprocess
import tempfile
import warnings
from functools import wraps
from pathlib import Path
from typing import Any

# external imports
import music21
from dotenv import load_dotenv
from music21 import bar, key, meter, note
from soundsliceapi import Client, Constants

# local imports
from .utils.aws_utils import upload_file_to_s3
from .utils.pdf_utils import (
    apply_pdf_footer_to_all_pages_in_score,
    build_export_score_for_lilypond,
    check_lilypond,
    cleanup_lilypond_formatting,
    pad_svg_file
)
from .utils.soundslice_utils import get_soundslice_credentials_from_env

# Load .env.template to access API credentials
load_dotenv()


def _load_score_content(func):
    """Decorator function to ensure that MusicXML score content is loaded"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.content is None:
            self._read_content_to_music21_stream()
        return func(self, *args, **kwargs)

    return wrapper


def sync_to_s3(func):
    """Decorator to upload file outputs to S3."""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # Run any method from the Score class that returns a Path
        filepath = func(self, *args, **kwargs)

        # If a user-defined local root directory is given, mirror in S3
        if (hasattr(self, 'collection_root') and self.collection_root and
                filepath):
            bucket_name = "port.itma.ie"
            try:
                s3_root = self.collection_root.parent
                upload_file_to_s3(
                    bucket_name=bucket_name,
                    file_path=str(filepath),
                    root_dir=str(s3_root)
                )
                object_key = filepath.relative_to(s3_root).as_posix()
                # return AWS filepath as str
                region = (os.getenv(
                    "AWS_DEFAULT_REGION") or "").strip() or "eu-west-1"
                url = (f"https://s3.{region}.amazonaws.com/"
                       f"{bucket_name}/{object_key}")
                return url

            except Exception as e:
                warnings.warn(f"S3 Sync failed for {filepath}: {e}")
                return filepath

        return filepath

    return wrapper


class Score:
    # TODO: custom repr via title?

    """
    Score class object represents a digital music score encoded as a MusicXML
    file. A Score object can be instantiated via the 'score_path' argument,
    which must point to a single MusicXML file. Tune objects can be created
    individually or can be automatically instantiated in bulk at corpus-level
    when a Collection object is instantiated.

    Attributes:

    score_path -- path to a MusicXML music score file.
    collection_root -- local root directory for storing output files.
    content -- music21 Stream object representing the musical content.
    incipit -- music21 Stream object representing the 4-bar incipit.
    key_signature -- key signature encoded/detected in the score.
    metadata_path -- path to a csv file containing metadata for the score.
    abc -- ABC notation representation of the score.
    title -- canonical title of the score.
    composer -- composer name.
    tune_type -- type of tune (jig, reel, hornpipe, etc).
    source -- source of the score (collection name).
    """

    # fallback time sig to avoid blank entries, implemented per ITMA's
    # requirements
    DEFAULT_TIME_SIG = "4/4"

    # shared cache to track Soundslice folder IDs for all Score class
    # instances
    _soundslice_folder_id_cache: dict[str, int] = {}

    def __init__(self, score_path, collection_root=None):

        """
        Initializes Score object.

        Args:
            score_path -- path to a MusicXML music score file.
            collection_root -- local root directory.
        """

        self.score_path = score_path
        # allow user to define a collection root directory
        self.collection_root = Path(
            collection_root) if collection_root else None
        # ensure that score_path points to a MusicXML file
        self._validate_score_file()
        self.content = None
        self.incipit = None
        self.key_signature = None
        self.abc = None
        self.title = None
        self.composer = None
        self.tune_type = None
        self.source = None

    def _validate_score_file(self):
        """
        Private helper method to validate that score_path points to a
        MusicXML file.
        """
        # first, check that score_path points to a file
        if not self.score_path.is_file():
            raise FileNotFoundError(f"{self.score_path} is not a valid file.")
        # if so, check that the file extension is supported
        allowed_extensions = {".xml", ".musicxml"}
        if self.score_path.suffix.lower() not in allowed_extensions:
            raise ValueError(
                f"Unsupported file type: {self.score_path.suffix}"
            )

    def _get_output_path(self, extension):
        """
        Helper to generate local output subdirectories by appending the
        appropriate file extension suffix to the collection root directory name
        per: <collection_root>_<file_extension suffix>

        E.G.
            collection_root = "my_collection"
            extension = ".mp3"
            output_path = "my_collection/my_collection_mp3"
        """
        if not self.collection_root:
            # if no collection root is defined, use the location of the score
            # file instead and don't create subfolders.
            return self.score_path.with_suffix(extension)

        # create subfolders
        subfolder_name = f"{self.collection_root.name}_{extension.strip('.')}"
        output_dir = self.collection_root / subfolder_name
        output_dir.mkdir(parents=True, exist_ok=True)

        return output_dir / self.score_path.with_suffix(extension).name

    def _get_incipit_mp3_output_path(self) -> Path:
        """
        Build the default output path for a generated incipit MP3.

        - If collection_root is set, write to:
        <collection_root>/<collection_root.name>_incipit_mp3/<stem>_incipit.mp3
        - Otherwise, write next to the source score file as:
        <score_dir>/<stem>_incipit.mp3
        """
        stem = str(self.score_path.stem or "").strip() or "untitled"
        filename = f"{stem}_incipit.mp3"

        if not self.collection_root:
            return self.score_path.with_name(filename)

        output_dir = (
                self.collection_root / f"{self.collection_root.name}_incipit_mp3"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / filename

    def _read_content_to_music21_stream(self):
        """
        Reads content from MusicXML file into a music21 Stream object
        and updates/sets Stream title according to self.title attr
        """
        self.content = music21.converter.parse(self.score_path)
        self._add_title_to_music21_stream()

    def _get_score_metadata(
            self,
            *,
            collection_metadata: Any | None = None,
            itma_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Get a metadata row as a dict (or return None if metadata is
        unavailable).

        Raises:
            ValueError if lookup is attempted but itma_id is missing.
            KeyError if lookup is attempted but no record exists.
        """
        getter = getattr(collection_metadata, "get_score_metadata", None) \
            if collection_metadata is not None else None
        if not callable(getter):
            return None

        itma_id = (itma_id or self.score_path.stem or "").strip()
        if not itma_id:
            raise ValueError(
                "Cannot load metadata for score: ITMA id slug is blank/empty."
            )

        row = getter(itma_id)
        return dict(row) if isinstance(row, dict) else None

    def set_metadata(
            self,
            *,
            custom_title: str | None = None,
            collection_metadata: Any | None = None,
            itma_id: str | None = None,
            has_metadata: bool | None = None,
    ) -> str:
        """
        Assign canonical title:
        Use custom_title if provided, otherwise look up in metadata,
        if available.
        Also populates composer, tune_type and source attrs when metadata is
        available.

        If custom_title is provided, metadata lookup is not performed and
        composer/tune_type/source are not populated. This is based on our
         expected use cases where custom titles are only used for occasional
         ad-hoc processing of files without metadata.

        Note re: has_metadata flag:
            Optional flag. Indicates whether our input score has input metadata
            or not. Allows us improve/standardize warning messages for
            "blank title" fallback cases in processing.py flow control module.

        Note re: title info lookup:
            - We prefer `federated_search_term` as the canonical title field.
            - Fall back to 'title` if `federated_search_term` is
            missing/blank.
        """
        if has_metadata is None:
            has_metadata = collection_metadata is not None

        if custom_title is not None:
            candidate = str(custom_title).strip()
            self.title = candidate
            # Custom-title mode: do not populate tune type or composer
            self.composer = None
            self.tune_type = None
            self.source = None
            return candidate

        if has_metadata is False:
            itma_id_clean = (itma_id or self.score_path.stem or "").strip()
            warnings.warn(
                f"No metadata is available for score {itma_id_clean!r}; "
                "Setting title = 'untitled'.",
                UserWarning,
            )
            self.title = "untitled"
            self.composer = None
            self.tune_type = None
            self.source = None
            return self.title

        # Use a single metadata row lookup to populate title, composer &
        # tune_type
        score_metadata = self._get_score_metadata(
            collection_metadata=collection_metadata,
            itma_id=itma_id,
        )

        # Populate self.title from federated_search_term
        title = (
                score_metadata.get("federated_search_term") or
                score_metadata.get("title")
        )
        clean_title = self._cleanup_metadata(title)
        if clean_title:
            self.title = clean_title
        else:
            # Warn and fall back to 'untitled'
            itma_id = (itma_id or self.score_path.stem or "").strip()
            warnings.warn(
                f"No title defined for score {itma_id!r}; "
                "Setting title = 'untitled'.",
                UserWarning,
            )
            self.title = "untitled"

        # Set composer
        composer = score_metadata.get("composer")
        self.composer = self._cleanup_metadata(composer)

        # Set source
        self.source = self._cleanup_metadata(score_metadata.get("source"))

        # Tune type: warn if missing in case there's an issue with input
        # metadata
        tune_type = score_metadata.get("tune_type")
        self.tune_type = self._cleanup_metadata(tune_type)

        return self.title

    @staticmethod
    def _cleanup_metadata(raw_metadata: Any | None) -> str | None:
        """
        Clean metadata from external sources.
        Returns a string, or None if blank/invalid.
        """
        if raw_metadata is None:
            return None
        cleaned_metadata = str(raw_metadata).strip()
        if not cleaned_metadata or cleaned_metadata.lower() == "nan":
            return None
        return cleaned_metadata

    def _add_title_to_music21_stream(self) -> None:
        """
        Ensure the music21 stream has a Metadata object at offset 0 (
        per Music21 docs, this is where title info is written) and overwrite
        any content at that location with title info from self.title attr.
        This ensures that any derivatives created via Music21 will display the
        canonical title.
        """
        if self.content is None or not self.title:
            return

        md = getattr(self.content, "metadata", None)

        # If metadata is missing, create it and insert it at score metadata
        # idx 0 per m21 docs
        if md is None:
            md = music21.metadata.Metadata()
            self.content.insert(0, md)

        # Ensure that stream metadata points at the Metadata object above
        self.content.metadata = md

        # overwrite any existing title
        self.content.metadata.title = str(self.title)

    @_load_score_content
    def detect_key(self):

        """
        Detects key via Music21-s built-in Krumhansl-Schmuckler algorithm.
        """

        detected_key = None

        try:
            detected_key = self.content.analyze("key")
            if detected_key is not None:
                self.key_signature = detected_key
                return str(detected_key)
        except Exception as e:
            warnings.warn(
                f"Music21 key analysis failed for {self.score_path.name}: {e}",
                UserWarning,
            )

        self.key_signature = detected_key

        return None if detected_key is None else str(detected_key)

    @_load_score_content
    def extract_tonic_from_key_signature(self):
        """
        Extracts the tonic pitch name from the detected key signature.
        """
        # detect key if not already loaded
        if self.key_signature is None:
            self.detect_key()
        # Check that key signature was successfully detected
        if self.key_signature is None:
            warnings.warn(
                f"Could not extract tonic for {self.score_path.name}: "
                "No key detected.",
                UserWarning,
            )
            return None

        tonic = getattr(self.key_signature, "tonic", None)
        if tonic is None:
            warnings.warn(
                f"Could not extract tonic for {self.score_path.name}: "
                "Key was detected but tonic is undefined.",
                UserWarning,
            )
            return None

        return str(tonic.name)

    @_load_score_content
    def extract_mode_from_key_signature(self):
        """
        Extracts the mode from the detected key signature.
        """
        # detect key if not already loaded
        if self.key_signature is None:
            self.detect_key()
        # Check that key signature was successfully detected
        if self.key_signature is None:
            warnings.warn(
                f"Cannot extract mode for {self.score_path.name}: "
                "No key signature detected or encoded.",
                UserWarning,
            )
            return None

        mode = getattr(self.key_signature, "mode", None)
        if not mode:
            warnings.warn(
                f"Cannot extract mode for {self.score_path.name}: "
                "Key was detected but mode is unavailable.",
                UserWarning,
            )
            return None

        return str(mode)

    @_load_score_content
    def extract_time_signature(self):
        """Extracts the time signature from the score."""

        all_time_signatures = self.content[meter.TimeSignature]
        # Make sure at least one time signature was found
        if all_time_signatures:
            # Read the first time signature and return in human-readable format
            time_signature = all_time_signatures[0]
            return time_signature.ratioString

        # otherwise return 4/4 as default value
        # 4/4 could be a default global constant
        else:
            return self.DEFAULT_TIME_SIG

    @_load_score_content
    def extract_incipit(self):
        """Extracts a 4-bar incipit from the score."""

        content = self.content
        #  check score is not empty
        if not content:
            raise ValueError(
                f"Cannot extract incipit for score {self.score_path.name}: "
                f"either this score is empty or it is not loading correctly."
            )

        # return first 4 bars of top melody line
        topline = content.parts[0]

        def _is_incomplete_bar(bar: music21.stream.Measure) -> bool:
            """
            Helper to identify and skip any pick-up bars encoded as
            first bar in the MusicXML-Music21 conversion process.
            Returns True if bar is shorter than the duration indicated in the
             time signature.
            """
            if bar is None:
                return False

            time_sig = bar.timeSignature or bar.getContextByClass(
                meter.TimeSignature)
            if time_sig is None:
                # Keep the bar if we don't know the time sig
                return False

            expected_qL = time_sig.barDuration.quarterLength
            actual_qL = bar.duration.quarterLength

            return actual_qL < expected_qL

        # For cases where measures aren't correctly encoded in topline
        all_bars = list(
            topline.recurse().getElementsByClass(music21.stream.Measure))
        if not all_bars:
            raise ValueError(
                f"Cannot extract incipit for score {self.score_path.name}: "
                f"top part contains no measures. "
                f"Please inspect score and re-run."
            )

        start_idx = 1 if _is_incomplete_bar(all_bars[0]) else 0
        selected_bars = all_bars[start_idx:start_idx + 4]

        # Capture original time signature to anchor beatStrength vals.
        incipit_time_sig = (
                selected_bars[0].timeSignature
                or selected_bars[0].getContextByClass(meter.TimeSignature)
        )
        if incipit_time_sig is None:
            score_ts = content.recurse().getElementsByClass(
                meter.TimeSignature)
            incipit_time_sig = score_ts[0] if score_ts else None
        # set time sig to 4/4 in unlikely even of none being provided in the
        # score
        if incipit_time_sig is None:
            incipit_time_sig = meter.TimeSignature(self.DEFAULT_TIME_SIG)

        # Slice by offsets (quarterLength) and rebuild measures.
        start_offset = float(selected_bars[0].offset)
        end_offset = float(
            selected_bars[-1].offset + selected_bars[-1].duration.quarterLength
        )

        # Build a new Part to ensure robust bar structure for incipit &
        # derivatives
        incipit = music21.stream.Part()
        # copy in the time sig
        incipit.insert(0.0, copy.deepcopy(incipit_time_sig))

        # Copy in the key signature from the topline
        incipit_key_sig = (
                selected_bars[0].keySignature
                or selected_bars[0].getContextByClass(key.KeySignature)
        )
        if incipit_key_sig is None:
            # Try to find key sig anywhere in the content
            score_ks = content.recurse().getElementsByClass(key.KeySignature)
            incipit_key_sig = score_ks[0] if score_ks else None

        # Insert the key signature at the start of the incipit
        if incipit_key_sig is not None:
            incipit.insert(0.0, copy.deepcopy(incipit_key_sig))

        # Copy incipit content into new part & recalculate offsets.
        for el in topline.flatten().notesAndRests:
            o = float(el.offset)
            if start_offset <= o < end_offset:
                incipit.insert(o - start_offset, copy.deepcopy(el))

        # Re-make bars to avoid "skipped measure" issues in
        # musicxml2ly.
        incipit.makeMeasures(inPlace=True)

        # Renumber bars (and mark as explicit)
        for idx, bar in enumerate(
                incipit.recurse().getElementsByClass(music21.stream.Measure),
                start=1
        ):
            bar.number = idx
            bar.implicit = False

        self.incipit = incipit
        return incipit

    @_load_score_content
    def create_breathnach_codes(self):

        """
        Creates Breathnach code. These codes are 8-value diatonic scale degree
        sequences representing rhythmically-emphasised notes in the incipit.
        """

        # read / extract incipit
        if self.incipit is None:
            self.extract_incipit()
        # copy the incipit
        incipit = copy.deepcopy(self.incipit)

        # Remove expressions and articulation
        for n in incipit.recurse().notes:
            n.articulations = []
            n.expressions = []

        # Collect grace notes
        grace_notes = []
        for n in incipit.recurse().notes:
            if n.duration.isGrace:
                grace_notes.append(n)

        # Remove any detected grace notes from the score
        for gn in grace_notes:
            # Find the bar or voice containing the grace note & remove it
            grace_note_location = gn.activeSite
            if grace_note_location is not None:
                grace_note_location.remove(gn)

        # handle chords:
        incipit_chords = incipit.recurse().getElementsByClass('Chord')
        for c in incipit_chords:
            root_note = note.Note(c.root())
            # set the duration of the note to match the original chord
            root_note.duration = c.duration
            # replace the original chord in the stream with the new note
            c.activeSite.replace(c, root_note)

        # handle rests by filling the most recent note, even if we have
        # multiple successive rests
        last_note = None
        for el in incipit.recurse().notesAndRests:
            if isinstance(el, note.Note):
                # Store last detected note
                last_note = el
            elif isinstance(el, note.Rest) and last_note is not None:
                # use stored note
                new_element = copy.deepcopy(last_note)
                # Keep the original rest's duration
                new_element.duration = el.duration
                # Replace the rest
                el.activeSite.replace(el, new_element)

        # get key signature and scale
        if self.key_signature is None:
            self.detect_key()

        key_sig = self.key_signature

        if key_sig is None:
            raise ValueError(
                f"Cannot extract key signature for score"
                f" {self.score_path.name}. Please inspect input score and "
                f"re-run."
            )

        # Ensure we have a Key object (which can store mode info) rather than
        # just a KeySignature (sharps and flats only)
        if key_sig is not None and not isinstance(key_sig, key.Key):
            key_sig = key_sig.asKey()

        # Check that our Key has a mode defined in the key signature
        current_mode = getattr(key_sig, 'mode', None)

        if not current_mode:
            warnings.warn(
                f"Could not determine scale for {self.score_path.name}. "
                "Breathnach code cannot be generated.",
                UserWarning
            )
            return None

        diatonic_scale = key_sig.getScale(current_mode)

        accented_notes = []
        # filter to retain accented notes only
        # Extract scale degrees for accented notes and store in list
        # Compute beatStrength within bar context.
        for bar in incipit.recurse().getElementsByClass(
                music21.stream.Measure):
            for n in bar.notes:
                if (n.isNote and n.beatStrength is not None
                        and n.beatStrength >= 0.5):
                    scale_degree, _accent = (
                        diatonic_scale.getScaleDegreeAndAccidentalFromPitch(
                            n.pitch)
                    )
                    if scale_degree is not None:
                        accented_notes.append(str(scale_degree))

        if len(accented_notes) < 8:
            warnings.warn(
                f"Breathnach code could not be fully populated for"
                f" {self.score_path.name}: insufficient accented notes "
                f"detected. Please check score and/or output manually.",
                UserWarning
            )

        if len(accented_notes) > 8:
            warnings.warn(
                f"Breathnach code for {self.score_path.name} exceeds maximum "
                f"length (8 scale degree values). Output code has been "
                f"automatically truncated. Please check score "
                f"and/or output manually.",
                UserWarning
            )

        # format output as a string & force max length of 8 scale degree values
        breathnach_code = "".join(accented_notes[:8])

        # Normalize empty string output to None so we never "blank out" an
        # existing bb_code value with an empty string during
        # partial processing runs.
        if not breathnach_code:
            return None

        return breathnach_code

    @_load_score_content
    def count_number_of_parts(self):

        """
        Applies a simple heuristic: double barlines & final barlines are
        taken as indicators of part structure; their occurrences in the
        score are counted, outputting the number of parts.

        This is not foolproof and a manual pass may be required after
        running this function.
        """

        score = self.content

        # derive part structure by identifying & counting double & final
        # barlines
        barlines = score.recurse().getElementsByClass(bar.Barline)
        part_structure = [
            b for b in barlines if b.type in ('double', 'final')
        ]
        # count parts (total parts = number of barline markers)
        num_parts = len(part_structure)

        # return 1 if no additional parts detected
        return max(1, num_parts)

    @sync_to_s3
    @_load_score_content
    def convert_score_to_midi(self, out_path=None, stream=None):
        """Write music21 stream to MIDI file """

        # Default to full score content if 'stream' is not provided
        # This allows us to pass both incipit (stream) and full score to
        # this method as needed.
        if stream is None:
            stream = self.content

        # If no path is given, create a temporary one
        if out_path is None:
            out_path = self._get_output_path('.mid')
        else:
            out_path = Path(out_path)

        # write output
        try:
            return self._write_midi(out_path=out_path, stream=stream)
        except Exception as e:
            warnings.warn(
                f"Unable to write MIDI for score {self.score_path.name} due "
                "to structural issues/incompatibilities. "
                "Skipping this processing step. "
                "Please inspect score and re-run."
                f"\n{e}",
                UserWarning,
            )

    def _write_midi(self, *, out_path: str | Path, stream=None) -> Path:
        """
        Internal helper for writing MIDI files.
        """
        if stream is None:
            stream = self.content

        out_path = Path(out_path)
        score = stream.expandRepeats()
        score.write("midi", fp=str(out_path))
        return out_path

    @sync_to_s3
    def convert_score_to_abc(self, output_path=None):
        """
        Reads xml file content as text and converts to ABC Notation.

        - Write ABC notation as plain text ('.txt').
        - Keep the output directory label as '_abc' (local + S3) so paths stay
            stable and descriptive even though the file extension is '.txt'.
        """

        #  Note: parsing multi-part XML scores to extract top line and write to
        #  ABC is beyond project scope as currently defined.

        # Also note: Music21 has ABC-writing capability, but the current
        # implementation is very picky about score/stream formatting and is
        # not compatible in practice with the types of MusicXML-derived
        # scores we are working with. Accordingly, we use convert_xml2abc as
        # a workaround. This also gives better performance than using Music21.

        if output_path is None:
            output_dir = (self.collection_root /
                          f"{self.collection_root.name}_abc")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / self.score_path.with_suffix(".txt").name
        else:
            output_path = Path(output_path)

        try:
            # Import lazily to avoid argparse/sys.argv side-effects.
            from abc_xml_converter import convert_xml2abc
            # Use pathlib to read the xml content as text
            xml_content = self.score_path.read_text(encoding='utf-8')
            # Some third-party tools parse sys.argv internally (argparse) and
            # choke on Port's CLI flags. Sandbox argv for this call.
            old_argv = sys.argv
            try:
                sys.argv = [old_argv[0]]

                # convert to ABC notation
                abc_content = convert_xml2abc(
                    file_to_convert=xml_content,
                    output_directory='',
                    bars_per_line=4,
                    file_to_convert_is_txt=True
                )
            finally:
                sys.argv = old_argv

            output_path.write_text(abc_content, encoding='utf-8')
            self.abc = abc_content
            return output_path

        except Exception as e:
            # preserve the original error from convert_xml2abc
            warnings.warn(
                f"Unable to create ABC Notation for score"
                f" {self.score_path.name}. "
                "Skipping this processing step. "
                "Please inspect score and re-run."
                f"\n{e}",
                UserWarning,
            )

    @sync_to_s3
    @_load_score_content
    def convert_score_to_pdf(self, output_path=None):
        """
        Converts the score to a PDF using LilyPond CLI.
        Handles OS-specific commands (Windows & Mac-compatible).

        Uses Music21 -> MusicXML export to reduce bar dropping
        in musicxml2ly. Simple inputs proceed; complex inputs fail fast if any
        structural remapping cannot be guaranteed.
        """

        if not check_lilypond():
            raise RuntimeError(
                "LilyPond not found. PDF conversion unavailable."
            )

        # setup output path
        if output_path is None:
            output_path = self._get_output_path('.pdf')
        else:
            output_path = Path(output_path)

        # setup tmp path for .ly files
        ly_path = output_path.with_suffix('.ly')
        # make Windows-compatible before passing to CLI
        is_windows = platform.system() == "Windows"
        # set tmp MusicXML file path
        temp_xml = Path(
            tempfile.gettempdir()) / f"temp_fullscore_{os.getpid()}.xml"

        try:
            score_for_export = build_export_score_for_lilypond(
                score_stream=self.content,
                default_time_sig_str=self.DEFAULT_TIME_SIG,
                score_label=self.score_path.name,
            )
            score_for_export.write("xml", fp=str(temp_xml))

            xml2ly_cmd = [
                'musicxml2ly',
                '--language=english',
                '--no-stem-directions',
                '-o',
                str(ly_path),
                str(temp_xml),
            ]
            subprocess.run(
                xml2ly_cmd,
                check=True,
                capture_output=True,
                text=True,
                shell=is_windows
                # Windows needs shell=is_windows to find scripts in PATH
            )

            # Sanitize intermediate .ly before compiling PDF.
            if ly_path.exists():
                ly_text = ly_path.read_text(encoding="utf-8", errors="replace")
                ly_text = cleanup_lilypond_formatting(
                    ly_text,
                    suppress_header=False,
                    title=self.title,
                    composer=self.tune_type,  # swapped: was self.composer
                    poet=self.composer,  # swapped: was self.tune_type
                    source=self.source
                )
                ly_path.write_text(ly_text, encoding="utf-8")

            # Compile LilyPond to PDF
            output_stem = str(output_path.with_suffix(''))
            lily_cmd = [
                'lilypond',
                '-s',
                '-o',
                output_stem,
                str(ly_path)
            ]
            subprocess.run(
                lily_cmd,
                check=True,
                capture_output=True,
                text=True,
                shell=is_windows
            )

            # Apply footer
            footer_pdf = Path(__file__).parent / "assets" / "itma_footer.pdf"
            apply_pdf_footer_to_all_pages_in_score(
                pdf_path=output_path,
                footer_pdf_path=footer_pdf,
            )

            # Cleanup tmp dir
            if ly_path.exists():
                os.remove(ly_path)

            return output_path


        except subprocess.CalledProcessError as e:
            # Cleanup even if it fails
            if ly_path.exists():
                os.remove(ly_path)

            stderr = e.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

            warnings.warn(
                f"PDF conversion failed for score {self.score_path.name}. "
                "Skipping this processing step. "
                "Please inspect score and re-run."
                f"LilyPond/musicxml2ly error output:\n{stderr}",
                UserWarning,
            )
            return None

        finally:
            if temp_xml.exists():
                os.remove(temp_xml)

    @sync_to_s3
    @_load_score_content
    def convert_incipit_to_svg(self, output_path=None):
        """
        Converts the score incipit to a cropped SVG file using the LilyPond CLI
        and musicxml2ly utility. Optimized for bulk processing and OS-agnostic.
        """

        if not check_lilypond():
            raise RuntimeError(
                "LilyPond not found. SVG conversion is unavailable.")

        # set up input and paths
        if self.incipit is None:
            self.extract_incipit()
        if output_path is None:
            output_path = self._get_output_path('.svg')
        else:
            output_path = Path(output_path)

        # explicitly make Windows-compatible
        is_windows = platform.system() == "Windows"

        # Create a temporary MusicXML file for the 4-bar incipit
        temp_xml = Path(
            tempfile.gettempdir()) / f"temp_incipit_{os.getpid()}.xml"
        # set up output paths
        ly_path = output_path.with_suffix('.ly')
        output_stem = str(output_path.with_suffix(''))

        try:
            # Export the incipit to XML
            tmp_score = music21.stream.Score()
            tmp_score.insert(0.0, copy.deepcopy(self.incipit))
            tmp_score.write('xml', fp=str(temp_xml))

            # Convert temporary XML to .ly using musicxml2ly
            xml2ly_cmd = [
                'musicxml2ly',
                '--language=english',
                '--no-stem-directions',
                '-o',
                str(ly_path),
                str(temp_xml)
            ]
            subprocess.run(
                xml2ly_cmd,
                check=True,
                capture_output=True,
                text=True,
                shell=is_windows
            )

            # Use _sanitize_lilypond_source helper to format the intermediate
            # .ly file before compiling PDF.
            if ly_path.exists():
                ly_text = ly_path.read_text(encoding="utf-8", errors="replace")
                ly_text = cleanup_lilypond_formatting(
                    ly_text,
                    suppress_header=True
                )
                ly_path.write_text(ly_text, encoding="utf-8")

            # Compile .ly to cropped SVG using lilypond
            lily_cmd = [
                'lilypond',
                '-dbackend=svg',
                '-dcrop',
                '-s',
                '-o', output_stem,
                str(ly_path)
            ]
            subprocess.run(
                lily_cmd,
                check=True,
                capture_output=True,
                text=True,
                shell=is_windows
            )

            # Handle LilyPond's cropped naming convention, which
            # auto-appends ".cropped.svg" to the output filename.
            cropped_svg = (
                    output_path.parent / f"{output_path.stem}.cropped.svg")
            if cropped_svg.exists():
                if output_path.exists():
                    os.remove(output_path)
                os.rename(cropped_svg, output_path)

                # Add padding around the tightly-cropped SVG.
                # Adjust margins to taste using pad_svg_file helper params.
                pad_svg_file(
                    output_path,
                    pad_top=1,
                    pad_right=0,
                    pad_bottom=0,
                    pad_left=0,
                )

            return output_path


        except subprocess.CalledProcessError as e:

            stderr = e.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            warnings.warn(

                f"Failed to generate incipit SVG for score"
                f" {self.score_path.name}. "
                "Skipping this processing step. "
                "Please inspect score and re-run."
                f"\nLilyPond/musicxml2ly error output:\n{stderr}",
                UserWarning,
            )
            return None
        finally:
            # Clean up all temp files
            for p in [temp_xml, ly_path]:
                if p and p.exists():
                    os.remove(p)

            # Remove non-cropped SVG if LilyPond generated one
            full_svg = output_path.with_suffix('.svg')
            if full_svg.exists() and full_svg != output_path:
                os.remove(full_svg)

    @sync_to_s3
    @_load_score_content
    def convert_incipit_to_mp3(self, output_path=None):
        """
        Converts the incipit to an MP3 file using FluidSynth (via CLI) and the
        GeneralUser GS SoundFont. Optimised for speed via fast-rendering flags.
        """

        # make sure FluidSynth is installed
        if shutil.which('fluidsynth') is None:
            raise RuntimeError(
                "FluidSynth not found. MP3 conversion unavailable."
            )
        # make sure ffmpeg is installed
        if shutil.which(str('ffmpeg')) is None:
            raise RuntimeError(
                "FFmpeg not found. MP3 conversion unavailable."
            )

        # make sure our SoundFont is available
        soundfont_path = self._check_soundfont()

        # set up input and paths
        if self.incipit is None:
            self.extract_incipit()

        if output_path is None:
            output_path = self._get_incipit_mp3_output_path()
        else:
            output_path = Path(output_path)

        # explicitly make Windows-compatible
        is_windows = platform.system() == "Windows"

        # Create temporary paths for intermediate outputs
        temp_dir = Path(tempfile.gettempdir())
        temp_midi = temp_dir / f"temp_incipit_{os.getpid()}.mid"
        temp_wav = temp_dir / f"temp_incipit_{os.getpid()}.wav"

        try:
            # Export the incipit to MIDI
            self._write_midi(out_path=temp_midi, stream=self.incipit)

            # Render MIDI to WAV via FluidSynth
            fs_cmd = [
                'fluidsynth',
                '-ni',
                '-F', str(temp_wav),
                str(soundfont_path),
                str(temp_midi)
            ]
            subprocess.run(
                fs_cmd,
                check=True,
                capture_output=True,
                shell=is_windows
            )

            if not temp_wav.exists():
                warnings.warn(
                    f"MP3 conversion failed for {self.score_path.name}: "
                    "Skipping this processing step. "
                    "Please inspect score and re-run.",
                    UserWarning,
                )
                return None

            # Convert WAV to MP3 via FFmpeg
            ffmpeg_cmd = [
                'ffmpeg',
                '-y',  # overwrite if re-running the script on the same score
                '-i', str(temp_wav),
                '-codec:a', 'libmp3lame',
                '-qscale:a', '2',  # High quality VBR
                str(output_path)
            ]
            subprocess.run(
                ffmpeg_cmd,
                check=True,
                capture_output=True,
                shell=is_windows
            )

            return output_path


        except subprocess.CalledProcessError as e:
            stderr = e.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

            warnings.warn(
                f"MP3 conversion failed for {self.score_path.name}. "
                "Skipping this processing step. "
                "Please inspect score and re-run."
                f"\nError output:\n{stderr if stderr else 'Unknown error'}",
                UserWarning,
            )
            return None

        finally:
            # Cleanup all intermediate files
            for p in [temp_midi, temp_wav]:
                if p.exists():
                    os.remove(p)

    def _check_soundfont(self):
        """
        Ensures the GeneralUser-GS.sf2 SoundFont is available in the './assets'
        dir. If not, runs the setup script to download it.
        """

        project_root = Path(__file__).parent
        assets_dir = project_root / "assets"
        soundfont_path = assets_dir / "GeneralUser-GS.sf2"

        if not soundfont_path.exists():
            warnings.warn("SoundFont not found. Attempting to download...",
                          UserWarning)
            setup_soundfont = project_root / "sondfont.py"

            if not setup_soundfont.exists():
                raise FileNotFoundError(
                    f"SoundFont setup helper not found at {setup_soundfont}")

            # Run the setup script
            subprocess.run(
                [sys.executable, str(setup_soundfont)],
                check=True,
                capture_output=True,
                text=True
            )

        if not soundfont_path.exists():
            raise RuntimeError("Failed to obtain SoundFont.")

        return soundfont_path

    def copy_musicxml_file_to_aws(self, collection_root: Path) -> str | None:
        """
        Uploads the MusicXML score to the 'port.itma.ie' S3 bucket,
        preserving the local directory structure relative to the collection
        root dir.

        Returns:
            S3 URI to the uploaded object (e.g. s3://bucket/prefix/file.xml)
        """
        # Hardcoded bucket as per ITMA requirements
        bucket_name = "port.itma.ie"

        try:
            # Mirror local directory structure relative to collection_root
            object_key = self.score_path.relative_to(
                collection_root).as_posix()

            upload_file_to_s3(
                bucket_name=bucket_name,
                file_path=str(self.score_path),
                root_dir=str(collection_root)
            )

            # return s3 path
            return f"s3://{bucket_name}/{object_key}"


        except Exception as e:
            warnings.warn(
                f"Failed to copy {self.score_path.name} to AWS. "
                "Skipping this processing step. "
                f"Error: {e}",
                UserWarning,
            )
            return None

    def create_soundslice_slice(
            self,
            *,
            collection_metadata,
            itma_id: str,
            title: str | None = None,
            _folder_id: int | None = None,
    ) -> str | None:
        """
        Create a slice in the collection's Soundslice folder, adds MusicXML,
        and return the Soundslice embed URL string.

        Returns:
            embed_id (scorehash) as a string, or None if creation/upload fails.

        If _folder_id is provided, no list_folders() calls are made to the
        Soundslice API (safe for parallel processing).
        """

        # validate score id
        itma_id = str(itma_id).strip()
        if not itma_id:
            warnings.warn(
                "Soundslice slice creation skipped: ITMA id is blank/empty.",
                UserWarning,
            )
            return None

        # Resolve Soundslice display title:
        # prioritises metadata title field, then score title (i.e. metadata
        # federated_search_term), then untitled
        soundslice_title: str | None = None

        if title is not None:
            soundslice_title = str(title).strip() or None
        else:
            if collection_metadata is not None:
                try:
                    row = self._get_score_metadata(
                        collection_metadata=collection_metadata,
                        itma_id=itma_id,
                    ) or {}
                    soundslice_title = self._cleanup_metadata(
                        row.get("title"))
                except Exception:
                    # None title will ultimately be auto-replaced by 'untitled'
                    soundslice_title = None

            if soundslice_title is None:
                # Ensure self.title is set
                if not self.title:
                    if collection_metadata is not None:
                        self.set_metadata(
                            collection_metadata=collection_metadata,
                            itma_id=itma_id,
                        )
                    else:
                        self.title = "untitled"

                soundslice_title = str(self.title or "").strip() or None

        score_name = soundslice_title or "untitled"

        if not self.collection_root:
            warnings.warn(
                f"Soundslice slice creation skipped for {self.score_path.name}: "
                "collection_root is not set.",
                UserWarning,
            )
            return None

        folder_name = self.collection_root.name
        application_id, password = get_soundslice_credentials_from_env()
        client = Client(application_id, password)

        # Manage folder id lookups via class-level cache
        folder_id: int | None = _folder_id
        if folder_id is None:
            folder_id = self._soundslice_folder_id_cache.get(folder_name)

        # Resolve folder ID; create Soundslice folder if needed
        if folder_id is None:
            def _find_folder_id() -> int | None:
                for f in client.list_folders():
                    if f.get("name") == folder_name:
                        fid = f.get("id")
                        return int(fid) if fid is not None else None
                return None

            folder_id = _find_folder_id()
            # manage folder creation for parallel processing
            if folder_id is None:
                try:
                    client.create_folder(name=folder_name)
                except Exception as e:
                    # Fail fast EXCEPT for the expected parallel race case.
                    msg = str(e).lower()
                    race_ok = any(
                        needle in msg
                        for needle in (
                            "already exists",
                            "already have",
                            "duplicate",
                            "conflict",
                            "409",
                        )
                    )
                    if not race_ok:
                        warnings.warn(
                            f"Failed to create Soundslice folder '{folder_name}'. "
                            "Skipping this processing step. "
                            f"Error: {e}",
                            UserWarning,
                        )
                        return None

                folder_id = _find_folder_id()

            if folder_id is None:
                warnings.warn(
                    f"Failed to resolve Soundslice folder id for '{folder_name}'. "
                    "Skipping this processing step.",
                    UserWarning,
                )
                return None

            self._soundslice_folder_id_cache[folder_name] = folder_id

        # create slice
        try:
            new_slice = client.create_slice(
                name=score_name,
                artist="",
                has_shareable_url=True,
                embed_status=Constants.EMBED_STATUS_ON_ALLOWLIST,
                can_print=True,
                folder_id=folder_id,
            )

            # upload MusicXML file
            scorehash = new_slice["scorehash"]
            with self.score_path.open("rb") as fp:
                client.upload_slice_notation(scorehash=scorehash, fp=fp)

            # get Soundslice 'scorehash' (embed id code)
            embed_id = new_slice.get("scorehash")
            if not embed_id:
                warnings.warn(
                    "Soundslice API did not return scorehash. "
                    "Skipping this processing step.",
                    UserWarning,
                )
                return None

            return embed_id

        except Exception as e:
            warnings.warn(
                f"Failed to create Soundslice slice for "
                f"{self.score_path.name}. "
                "Skipping this processing step. "
                f"Error: {e}",
                UserWarning,
            )
            return None
