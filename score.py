"""This file holds a 'Score' class (with helper functions), modeling a single
digital music score."""

# TODO: type hinting
# TODO: move helper functions to a separate file?

# built-in imports
import copy
import os
import platform
import re
import shutil
import sys
import subprocess
import tempfile
import warnings
import xml.etree.ElementTree as ET
from functools import wraps
from pathlib import Path
from typing import Any

# external imports
import music21
from dotenv import load_dotenv
from abc_xml_converter import convert_xml2abc
from music21 import bar, key, meter, note
from music21.analysis.discrete import SimpleWeights
from soundsliceapi import Client, Constants

# local imports
from aws_utils import upload_file_to_s3
from soundslice_utils import get_soundslice_credentials_from_env

# Load .env to access API credentials
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
            bucket_name = "scores.itma.ie"
            try:
                s3_root = self.collection_root.parent
                upload_file_to_s3(
                    bucket_name=bucket_name,
                    file_path=str(filepath),
                    root_dir=str(s3_root)
                )
                object_key = filepath.relative_to(s3_root).as_posix()
                # return AWS filepath as str
                return f"s3://{bucket_name}/{object_key}"

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
        pass

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

    def _read_content_to_music21_stream(self):
        """
        Reads content from MusicXML file into a music21 Stream object
        and updates/sets Stream title according to self.title attr
        """
        self.content = music21.converter.parse(self.score_path)
        self._add_title_to_music21_stream()

    def _resolve_title(
            self,
            *,
            collection_metadata: Any | None = None,
            itma_id: str | None = None,
    ) -> str:
        """
        Resolve 'title' field content from collection metadata:
         - Lookup score title by ITMA id val stored in 'slug' metadata field.
        - Fall back to '[untitled]' and warn the user if no title is given or
        detected.
        """
        itma_id = (itma_id or self.score_path.stem or "").strip()

        getter = (
            getattr(collection_metadata, "get_score_metadata", None)
            if collection_metadata is not None
            else None
        )

        title: str = "[untitled]"

        # If we have metadata, try to look up title, otherwise throw error.
        if callable(getter):
            if not itma_id:
                raise ValueError(
                    f"Cannot load title for score {itma_id!r}: ITMA id slug is"
                    " blank/empty."
                )

            try:
                row = getter(itma_id)
            except KeyError as e:
                raise KeyError(
                    f"Metadata lookup failed: no record found for score {
                    itma_id!r}."
                ) from e

            if isinstance(row, dict):
                candidate = str(row.get("title") or "").strip()
                if candidate:
                    title = candidate
        else:
            # If no metadata is available, fallback to '[untitled]'
            if not itma_id:
                warnings.warn(
                    "No itma_id available; using fallback title '[untitled]'.",
                    UserWarning,
                )

        # Warn only for the "title missing" case, not for lookup failures.
        if title == "[untitled]":
            warnings.warn(
                f"No title defined for score {itma_id!r}; "
                "Setting title = '[untitled]'.",
                UserWarning,
            )

        self.title = title
        return title

    def get_title(
            self,
            *,
            custom_title: str | None = None,
            collection_metadata: Any | None = None,
            itma_id: str | None = None,
            has_metadata: bool | None = None,
    ) -> str:
        """
        Assign canonical title:
        Use custom_title if provided, otherwise call Score._resolve_title()

        Note re: metadata_present:
            Optional. Indicates whether our input score has input metadata
            or not.
            This allows us improve/standardize warning messages for
            "blank title" fallback cases in processing.py flow control module.
        """
        if has_metadata is None:
            has_metadata = collection_metadata is not None

        if custom_title is not None:
            candidate = str(custom_title).strip()
            self.title = candidate
            return candidate

        if has_metadata is False:
            itma_id_clean = (itma_id or self.score_path.stem or "").strip()
            warnings.warn(
                f"No metadata is available for score {itma_id_clean!r}; "
                "Setting title = '[untitled]'.",
                UserWarning,
            )
            self.title = "[untitled]"
            return self.title

        return self._resolve_title(collection_metadata=collection_metadata,
                              itma_id=itma_id)

    def _add_title_to_music21_stream(self) -> None:
        """
        Ensure the music21 stream has a Metadata object at offset 0 (
        per Music21 docs, this is where title info is written) and overwrite
        any content at that location with title info from self.title attr.
        This ensures our derivatives will display the canonical title.
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
                "Cannot extract incipit: either this score is empty or it is "
                "not loading correctly."
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


        first_bar = topline.measure(1)
        if _is_incomplete_bar(first_bar):
            # Take the next 4 bars to ensure we have an accurate incipit.
            incipit = topline.measures(2, 5)
        else:
            incipit = topline.measures(1, 4)

        self.incipit = incipit
        return incipit

    @_load_score_content
    def create_breathnach_codes(self):

        """
        Creates Breathnach code. These codes are diatonic scale degree
        sequences representing rhythmically-emphasised notes in the incipit.
        """

        # read / extract incipit
        if self.incipit is None:
            self.extract_incipit()
        # copy the incipit
        incipit = copy.deepcopy(self.incipit)

        # handle chords:
        incipit_chords = incipit.flatten().getElementsByClass('Chord')
        for c in incipit_chords:
            root_note = note.Note(c.root())
            # set the duration of the note to match the original chord
            root_note.duration = c.duration
            # replace the original chord in the stream with the new note
            c.activeSite.replace(c, root_note)

        # handle rests by filling the most recent note, even if we have
        # multiple successive rests
        last_note = None
        for el in incipit.flatten().notesAndRests:
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
                f"Cannot extract key signature for {self.score_path.name}"
            )

        # Ensure we have a Key object (which can store mode info) rather than
        # just a KeySignature
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

        # Remove expressions and articulation
        for n in incipit.recurse().notes:
            n.expressions = []

        accented_notes = []
        # filter to retain accented notes only
        # Extract scale degrees for accented notes and store in list
        for n in incipit.flatten().notes:
            # beatStrength >= 0.5 filters to retain only notes on accented
            # beats
            if n.isNote and n.beatStrength > 0.5:
                scale_degree = diatonic_scale.getScaleDegreeFromPitch(
                    n.pitch)
                # in case Music21 returns None for accidentals on the beat
                if scale_degree is not None:
                    accented_notes.append(str(scale_degree))

        breathnach_code = "".join(accented_notes)

        return breathnach_code

    @_load_score_content
    def count_number_of_parts(self):

        """
        Applies a simple heuristic: double barlines & final barlines are
        taken as indicators of part structure; their occurrences in the
        score are counted, giving the number of parts.

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
        """Write music21 stream to MIDI file"""

        # Default to full score content if no stream is provided
        # This allows us to pass both incipit (stream) and full score to
        # this method as needed.
        if stream is None:
            stream = self.content

        # If no path is given, create a temporary one
        if out_path is None:
            out_path = self._get_output_path('.mid')
        else:
            out_path = Path(out_path)

        try:
            return self._write_midi(out_path=out_path, stream=stream)
        except Exception as e:
            raise RuntimeError(
                f"Failed to write MIDI for {self.score_path.name}. Error: {e}"
            ) from e

    def _write_midi(self, *, out_path: str | Path, stream=None) -> Path:
        """
        Internal helper for writing MIDI files.
        Never syncs to S3 (handled separately by @sync_to_s3).
        """
        if stream is None:
            stream = self.content

        out_path = Path(out_path)
        score = stream.expandRepeats()
        score.write("midi", fp=str(out_path))
        return out_path

    @sync_to_s3
    def convert_score_to_abc(self, output_path=None):
        """Reads xml file content as text and converts to ABC Notation"""

        #  Note: parsing multi-part XML scores to extract top line and write to
        #  ABC is beyond project scope as currently defined.

        # Also note: Music21 has ABC-writing capability but the current
        # implementation is very picky about score/stream formatting and is
        # not compatible in practice with the types of MusicXML-derived
        # scores we are working with. Accordingly, we use convert_xml2abc as
        # a workaround. This also gives better performance than using Music21.

        if output_path is None:
            output_path = self._get_output_path('.abc')
        else:
            output_path = Path(output_path)

        try:
            # Use pathlib to read the xml content as text
            xml_content = self.score_path.read_text(encoding='utf-8')
            # convert to ABC notation
            abc_content = convert_xml2abc(
                file_to_convert=xml_content,
                output_directory='',
                bars_per_line=4,
                file_to_convert_is_txt=True
            )

            output_path.write_text(abc_content, encoding='utf-8')
            self.abc = abc_content
            return output_path

        except Exception as e:
            # Chaining the exception to preserve the original error from
            # convert_xml2abc
            raise RuntimeError(
                f"Failed to convert {self.score_path.name} to ABC notation. "
                f"Internal Error: {e}"
            ) from e

    @sync_to_s3
    @_load_score_content
    def convert_score_to_pdf(self, output_path=None):
        """
        Converts the score to a PDF using LilyPond CLI.
        Handles OS-specific command differences (Windows vs macOS/Linux).
        """

        if not self._check_lilypond():
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

        try:
            # Convert MusicXML to .ly
            # On Windows, musicxml2ly is often packaged as a script that needs
            # the shell or full path
            xml2ly_cmd = [
                'musicxml2ly',
                '--language=english',
                '--no-stem-directions',
                '-o',
                str(ly_path),
                str(self.score_path)
            ]

            subprocess.run(
                xml2ly_cmd,
                check=True,
                capture_output=True,
                text=True,
                shell=is_windows
                # Windows needs shell=is_windows to find scripts in PATH
            )

            # Strip labels + BPM from the generated .ly before compiling.
            if ly_path.exists():
                ly_text = ly_path.read_text(encoding="utf-8", errors="replace")
                ly_text = self._sanitize_lilypond_source(
                    ly_text,
                    suppress_header=False,
                    title=self.title
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

            # Cleanup tmp dir
            if ly_path.exists():
                os.remove(ly_path)

            return output_path

        except subprocess.CalledProcessError as e:
            # Cleanup even if it fails
            if ly_path.exists():
                os.remove(ly_path)
            raise RuntimeError(
                f"PDF Conversion failed for {self.score_path.name}. "
                f"Stderr: {e.stderr}"
            ) from e

    @sync_to_s3
    @_load_score_content
    def convert_incipit_to_svg(self, output_path=None):
        """
        Converts the score incipit to a cropped SVG file using the LilyPond CLI
        and musicxml2ly utility. Optimized for bulk processing and OS-agnostic.
        """

        if not self._check_lilypond():
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
            self.incipit.write('xml', fp=str(temp_xml))

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

            # Strip labels + BPM from the generated .ly before compiling SVG.
            if ly_path.exists():
                ly_text = ly_path.read_text(encoding="utf-8", errors="replace")
                ly_text = self._sanitize_lilypond_source(
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
            # auto-appends ".cropped.svg" to the output filename"
            cropped_svg = (
                    output_path.parent / f"{output_path.stem}.cropped.svg")
            if cropped_svg.exists():
                if output_path.exists():
                    os.remove(output_path)
                os.rename(cropped_svg, output_path)

                # Add padding around the tightly-cropped SVG.
                # Adjust margins to taste.
                self._pad_svg_file(
                    output_path,
                    pad_top=1,
                    pad_right=0,
                    pad_bottom=0,
                    pad_left=0,
                )

            return output_path

        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"SVG Conversion failed for incipit of {self.score_path.name}. "
                f"Stderr: {e.stderr}"
            ) from e
        finally:
            # Clean up all temp files
            for p in [temp_xml, ly_path]:
                if p and p.exists():
                    os.remove(p)

            # Remove non-cropped SVG if LilyPond generated one
            standard_svg = output_path.with_suffix('.svg')
            if standard_svg.exists() and standard_svg != output_path:
                os.remove(standard_svg)

    @staticmethod
    def _check_lilypond():
        """
        Checks if LilyPond is installed. Returns True if found,
        False otherwise.
        """
        # Explicitly passing a string 'lilypond' for Windows compatibility
        if shutil.which(str('lilypond')) is None:
            warnings.warn(
                "LilyPond not found on system. PDF conversion is unavailable.",
                UserWarning
            )
            return False
        return True

    @staticmethod
    def _sanitize_lilypond_source(
            ly_text: str, *,
            suppress_header: bool = False,
            title: str | None = None,
    ) -> str:
        """
        Remove instrument/voice labels (e.g. "Violin") and tempo/BPM markings
        from an temp LilyPond (.ly) file, which is created during the PDF
        export process.

        If suppress_header=True, remove header/title output (useful for
        incipit SVGs where we want to keep musical content only).
        """
        # Remove explicit tempo markup and tempo settings
        ly_text = re.sub(r"(?m)^\s*\\tempo\b.*$\n?", "", ly_text)
        ly_text = re.sub(
            r"(?m)^\s*\\set\s+Score\.tempoWholesPerMinute\s*=\s*.*$\n?",
            "",
            ly_text,
        )

        # Remove instrument name assignments
        ly_text = re.sub(
            r"(?m)^\s*\\set\s+(Staff|Voice)\.("
            r"shortInstrumentName|instrumentName)\s*=\s*.*$\n?", "",
            ly_text,
        )

        # Also handle: \new Staff \with { instrumentName = "Violin" ... }
        # Only strip these specific properties, leaving other \with settings
        # intact.
        ly_text = re.sub(
            r"(?s)(\\with\s*\{.*?)(\bshortInstrumentName\s*=\s*.*?)(.*?\})",
            r"\1\3",
            ly_text,
        )
        ly_text = re.sub(
            r"(?s)(\\with\s*\{.*?)(\binstrumentName\s*=\s*.*?)(.*?\})",
            r"\1\3",
            ly_text,
        )

        # Always suppress these header fields
        # (we do NOT want them in PDF or SVG).
        ly_text = re.sub(
            r'(?m)^\s*(subtitle|subsubtitle|piece)\s*=\s*".*"\s*$\n?',
            "",
            ly_text,
        )

        ly_text = ly_text.rstrip() + "\n\n"

        if suppress_header:
            # suppress *all* header output (title + composer + lyricist etc.)
            # for svg output
            ly_text += r"""
    \header {
      title = ##f
      subtitle = ##f
      subsubtitle = ##f
      piece = ##f
      composer = ##f
      poet = ##f
      arranger = ##f
      opus = ##f
      tagline = ##f
    }
    """.lstrip()
        else:
            # PDFs: keep header block, but:
            # populate title from metadata, suppress subtitle, allow composer
            # + poet (lyricist) to pass through unchanged
            if title is None or not str(title).strip():
                raise ValueError(
                    "PDF export requires score title to be "
                    "provided for display in LilyPond header."
                )

            safe_title = str(title).strip().replace(
                "\\", "\\\\").replace('"','\\"'
                                      )

            # Ensure there is a header block
            if not re.search(r"(?s)\\header\s*\{", ly_text):
                ly_text += r"""
    \header {
    }
    """.lstrip()

            # Suppress tagline (either overwrite or insert)
            if re.search(r"(?m)^\s*tagline\s*=", ly_text):
                ly_text = re.sub(
                    r"(?m)^\s*tagline\s*=.*$",
                    "  tagline = ##f",
                    ly_text,
                )
            else:
                ly_text = re.sub(
                    r"(?s)(\\header\s*\{)",
                    r"\1\n  tagline = ##f",
                    ly_text,
                    count=1,
                )

            # Force title (overwrite or insert). Do not modify composer/poet.
            if re.search(r"(?m)^\s*title\s*=", ly_text):
                ly_text = re.sub(
                    r'(?m)^\s*title\s*=.*$',
                    f'  title = "{safe_title}"',
                    ly_text,
                )
            else:
                ly_text = re.sub(
                    r"(?s)(\\header\s*\{)",
                    rf'\1\n  title = "{safe_title}"',
                    ly_text,
                    count=1,
                )

        # Enforce predictable page + line behavior.
        if not re.search(r"(?s)\\paper\s*\{", ly_text):
            ly_text += r"""
    \paper {
      ragged-last = ##t
      ragged-last-bottom = ##t
      indent = 0\mm
      short-indent = 0\mm
      left-margin = 12\mm
      right-margin = 12\mm
    }
    """.lstrip()

        # Disable engravers responsible for printing these elements.
        ly_text += r"""
    \layout {
      \context { \Staff \remove Instrument_name_engraver }
      \context { \Score \remove Metronome_mark_engraver }
    }
    """.lstrip()

        return ly_text

    @staticmethod
    def _pad_svg_file(
            svg_path: Path,
            *,
            pad_top: float = 12.0,
            pad_right: float = 12.0,
            pad_bottom: float = 12.0,
            pad_left: float = 12.0,
    ) -> None:
        """
        Add whitespace padding around an SVG by expanding its viewBox.
        Padding units are in SVG user units (the viewBox coordinate system).
        """
        svg_path = Path(svg_path)
        if not svg_path.exists():
            return

        data = svg_path.read_text(encoding="utf-8", errors="replace")
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return

        view_box = root.get("viewBox")
        if not view_box:
            return

        parts = view_box.replace(",", " ").split()
        if len(parts) != 4:
            return

        try:
            min_x, min_y, vb_w, vb_h = (
                float(parts[0]),
                float(parts[1]),
                float(parts[2]),
                float(parts[3]),
            )
        except ValueError:
            return

        new_min_x = min_x - pad_left
        new_min_y = min_y - pad_top
        new_vb_w = vb_w + pad_left + pad_right
        new_vb_h = vb_h + pad_top + pad_bottom
        root.set("viewBox",
                 f"{new_min_x:g} {new_min_y:g} {new_vb_w:g} {new_vb_h:g}")

        # If there's a clipPath with a rect sized to the old bounds,
        # expand it so padding doesn't clip the drawing.
        for clip in root.iter():
            if not clip.tag.endswith("clipPath"):
                continue
            for el in list(clip):
                if not el.tag.endswith("rect"):
                    continue
                el.set("x", f"{new_min_x:g}")
                el.set("y", f"{new_min_y:g}")
                el.set("width", f"{new_vb_w:g}")
                el.set("height", f"{new_vb_h:g}")

        svg_path.write_text(
            ET.tostring(root, encoding="unicode", method="xml"),
            encoding="utf-8",
        )

    @sync_to_s3
    @_load_score_content
    def convert_incipit_to_mp3(self, output_path=None):
        """
        Converts the incipit to an MP3 file using FluidSynth (via CLI) and the
        GeneralUser GS SoundFont. Optimised for speed via fast-rendering flags.
        """

        # make sure FluidSynth is installed
        if not self._check_fluidsynth():
            raise RuntimeError(
                "FluidSynth not found. MP3 conversion unavailable."
            )
        # make sure our SoundFont is available
        soundfont_path = self._ensure_soundfont_exists()

        # set up input and paths
        if self.incipit is None:
            self.extract_incipit()

        if output_path is None:
            output_path = self._get_output_path('.mp3')
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
                raise RuntimeError(
                    "FluidSynth failed to create temporary WAV file.")

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
            raise RuntimeError(
                f"MP3 Conversion failed for {self.score_path.name}. "
                f"Stderr: {e.stderr.decode() if e.stderr else 'Unknown error'}"
            ) from e
        finally:
            # Cleanup all intermediate files
            for p in [temp_midi, temp_wav]:
                if p.exists():
                    os.remove(p)

    @staticmethod
    def _check_fluidsynth():
        """
        Checks if FluidSynth is installed.
        Returns True if found, False otherwise.
        """
        if shutil.which('fluidsynth') is None:
            warnings.warn(
                "FluidSynth not found on system. "
                "MP3 conversion is unavailable.",
                UserWarning
            )
            return False
        return True

    @staticmethod
    def _check_ffmpeg():
        """
        Checks if FFmpeg is installed.
        Returns True if found, False otherwise.
        """
        if shutil.which(str('ffmpeg')) is None:
            warnings.warn(
                "FFmpeg not found on system. MP3 conversion is unavailable.",
                UserWarning
            )
            return False
        return True

    def _ensure_soundfont_exists(self):
        """
        Ensures the GeneralUser-GS.sf2 SoundFont is available in the assets
        dir. If not, runs the setup script to download it.
        """

        project_root = Path(__file__).parent
        assets_dir = project_root / "assets"
        soundfont_path = assets_dir / "GeneralUser-GS.sf2"

        if not soundfont_path.exists():
            warnings.warn("SoundFont not found. Attempting to download...",
                          UserWarning)
            setup_soundfont = project_root / "setup_general_user_gs.py"

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

    def copy_musicxml_file_to_aws(self, collection_root: Path) -> str:
        """
        Uploads the MusicXML score to the 'scores.itma.ie' S3 bucket,
        preserving the local directory structure relative to the collection
        root dir.

        Returns:
            S3 URI to the uploaded object (e.g. s3://bucket/prefix/file.xml)
        """
        # Hardcoded bucket as per ITMA requirements
        bucket_name = "scores.itma.ie"

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
            raise RuntimeError(
                f"Failed to copy {self.score_path.name} to AWS: {e}"
            ) from e

    def create_soundslice_slice(
            self,
            *,
            collection_metadata,
            itma_id: str,
            title: str | None = None,
            _folder_id: int | None = None,
    ) -> str:
        """
        Create a slice in the collection's Soundslice folder, adds MusicXML,
         and return the Soundslice embed URL string.

        If _folder_id is provided, no list_folders() calls are made t the
        Soundslice API (safe for parallel processing).
        """

        # validate score id
        itma_id = str(itma_id).strip()
        if not itma_id:
            raise ValueError("ITMA id must be a non-empty string.")
        # try to resolve score title
        if not self.title:
            self._resolve_title(collection_metadata=collection_metadata,
                               itma_id=itma_id)

        score_name = str(title or self.title or "").strip() or "[untitled]"

        if not self.collection_root:
            raise RuntimeError(
                "Collection root directory must be set to proceed.")

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
                        raise RuntimeError(
                            f"Failed to create Soundslice folder"
                            f" '{folder_name}': {e}"
                        ) from e

                folder_id = _find_folder_id()

            if folder_id is None:
                raise RuntimeError(
                    f"Failed to resolve Soundslice folder id for "
                    f"'{folder_name}'."
                )

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

            # get embed url
            embed_path = new_slice.get("embed_url")
            if not embed_path:
                raise RuntimeError("Soundslice API did not return embed_url.")

            return f"https://www.soundslice.com{embed_path}"

        except Exception as e:
            raise RuntimeError(
                f"Failed to create Soundslice slice for "
                f"{self.score_path.name}: {e}"
            ) from e
