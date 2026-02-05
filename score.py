"""This file holds a 'Score' class (with helper functions), modeling a single
digital music score."""

# TODO: type hinting

# built-in imports
import copy
import inspect
import os
import platform
import shutil
import sys
import subprocess
import tempfile
import warnings
import secrets
from collections import Counter
from functools import wraps
from pathlib import Path

# external imports
import music21
import pandas as pd
from dotenv import load_dotenv
from abc_xml_converter import convert_xml2abc
from music21 import analysis, bar, key, meter, note, chord
from music21.analysis.discrete import SimpleWeights
from pandas.core.config_init import max_cols
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
            self.read_content_to_music21_stream()
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
                upload_file_to_s3(
                    bucket_name=bucket_name,
                    file_path=str(filepath),
                    root_dir=str(self.collection_root)
                )
                object_key = filepath.relative_to(
                    self.collection_root).as_posix()
                # return AWS filepath as str
                return f"s3://{bucket_name}/{object_key}"

            except Exception as e:
                warnings.warn(f"S3 Sync failed for {filepath}: {e}")
                return filepath

        return filepath

    return wrapper

class Score:
    # TODO: repr / title

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
    key_signature -- music21 KeySignature object representing the key given
    in the score.
    alt_key_signature -- music21 KeySignature object representing the key
    detected via the Krumhansl-Schmuckler algorithm.
    _keys_flag -- private boolean indicating whether the
    algorithmically-detected key matches the key given in the score
    metadata_path -- path to a csv file containing metadata for the score.
    abc -- ABC notation representation of the score.
    """

    DEFAULT_TIME_SIG = "4/4"

    # shared cache to track Soundslice folder IDs for all Score class instances
    _soundslice_folder_id_cache: dict[str, int] = {}

    def __init__(self, score_path, collection_root=None):

        """
        Initializes Score object.

        Args:
            score_path -- path to a MusicXML music score file.
        """

        self.score_path = score_path
        # allow user to define a collection root directory
        # TODO: Consider getting attr below from Collection class in the
        #  future?
        self.collection_root = Path(
            collection_root) if collection_root else None
        # ensure that score_path points to a MusicXML file
        self._validate_score_file()
        self.content = None
        self.incipit = None
        self.key_signature = None
        self.alt_key_signature = None
        self._keys_flag = True
        self.abc = None
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
        per: [root dir_name]_[file_extension suffix]
        """
        if not self.collection_root:
            # if no collection root is defined, use the location of the score
            # file instead and don't create subfolders.
            return self.score_path.with_suffix(extension)

        # create subfolders
        subfolder_name = f"{self.collection_root.name}_{extension.strip('.')}"
        output_dir = self.collection_root / subfolder_name
        output_dir.mkdir(exist_ok=True)

        return output_dir / self.score_path.with_suffix(extension).name

    def read_content_to_music21_stream(self):
        """Reads content from MusicXML file into a music21 Stream object."""
        self.content = music21.converter.parse(self.score_path)

    @_load_score_content
    def _detect_key_signature_algorithmically(self):

        """
        Detects key via Music21-s built-in Krumhansl-Schmuckler algorithm,
        using 'Simple Weights' by Craig Sapp (Humdrum Toollit).
        """

        # TODO: write output to csv in 'detected_key' column

        # Flatten the stream to ensure the algorithm can access all notes
        notes = self.content.flatten().notes

        if len(notes) == 0:
            warnings.warn(
                f"Score {self.score_path.name} contains no notes. "
                "Key detection skipped.",
                UserWarning
            )
            return None

        try:
            # Detect key using Krumhansl-Schmuckler algorithm with Craig Sapp's
            # simple weights applied
            key_analysis = SimpleWeights(notes)
            detected_key = key_analysis.getSolution(notes)
        except Exception as e:
            warnings.warn(f"Algorithmic key detection failed: {e}",
                          UserWarning)
            return None

        # handle cases where no key was detected
        if detected_key is None:
            return None

        # save key signature as instance attr
        self.alt_key_signature = detected_key
        # return human-readable version
        return str(detected_key)

    @_load_score_content
    def _read_key_signature_from_score(self):
        """Get any Key Signatures provided within the score"""

        # TODO: write output to csv

        content = self.content.recurse()
        key_sigs = [ks for ks in content.getElementsByClass(key.KeySignature)]
        # handle cases were no key signatures were given in the score
        if not key_sigs:
            warnings.warn("No key signature detected.", UserWarning)
            return None
        # Return only the first key signature object encoded in the score
        extracted_key = key_sigs[0]

        # If we have a Music21 KeySignature, convert to Key object
        if not isinstance(extracted_key, key.Key):
            implied_key = extracted_key.asKey()
            if implied_key:
                extracted_key = implied_key

        # save key signature as instance attr
        self.key_signature = extracted_key
        # return human-readable version
        return str(extracted_key)

    @_load_score_content
    def find_key_signature(self):

        """
        Compares key signature encoded in the score vs
        algorithmically detected key signature. Makes note of cases where the
        two values are not in agreement.
        """

        key_signature = self._read_key_signature_from_score()
        detected_key = self._detect_key_signature_algorithmically()

        if key_signature != detected_key:
            self._keys_flag = False
        else:
            self._keys_flag = True

        return self.key_signature

    @_load_score_content
    def extract_tonic_from_key_signature(self):

        # TODO: write output to csv

        """
        Extracts the tonic pitch name from the key signature returned by
        the two methods above.
        """

        # detect key if not already loaded
        if self.key_signature is None:
            self.find_key_signature()
        # Check that key signature was successfully detected
        if self.key_signature is None:
            raise ValueError(
                f"Could not extract tonic for {self.score_path.name}: "
                "No key signature detected or encoded."
            )

        return self.key_signature.tonic.name

    @_load_score_content
    def extract_mode_from_key_signature(self):
        """Extracts the mode from the score's key signature property"""

        # TODO: write output to csv

        # detect key if not already loaded
        if self.key_signature is None:
            self.find_key_signature()
        # Check that key signature was successfully detected
        if self.key_signature is None:
            raise ValueError(
                f"Cannot extract mode for {self.score_path.name}: "
                "No key signature information could be found or detected."
            )

        return self.key_signature.mode

    @_load_score_content
    def extract_time_signature(self):
        """Extracts the time signature from the score"""

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
        """Extracts a 4-bar incipit from the score"""

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
            Helper to identify and skip any pick-ups improperly encoded as
            first bar in the MusicXML-Music21 converion process.
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

        # TODO: store output in ScoreMetadata  field
        # TODO: format ouput per 'XXXX_XXXX'

        # read / extract incipit
        if self.incipit is None:
            self.extract_incipit()
        # copy the incipit
        incipit = copy.deepcopy(self.incipit)

        # handle chords:
        incipit_chords = incipit.flatten().getElementsByClass('Chord')
        for c in incipit_chords:
            root_note = note.Note(c.root())
            # set the duration of the  note to match the original chord
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
            self.find_key_signature()

        key_sig = self.key_signature
        if key_sig is None:
            incipit_notes = incipit.flatten().notes
            if incipit_notes:
                key_analysis = SimpleWeights(incipit_notes)
                key_sig = key_analysis.getSolution(incipit_notes)

        if key_sig is None:
            raise ValueError(
                f"Cannot extract key signature for {self.score_path.name}"
            )

        # Ensure we have a Key object (which has a .mode) rather than
        # just a KeySignature
        if key_sig is not None and not isinstance(key_sig, key.Key):
            key_sig = key_sig.asKey()

        # Check that we have a mode defined in the key signature
        # (incl. maj/min tonality along with all 'church modes')
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
            if n.isNote and n.beatStrength >= 0.5:
                scale_degree = diatonic_scale.getScaleDegreeFromPitch(
                    n.pitch)
                # in case Music21 returns None for accidentals on the beat
                if scale_degree is not None:
                    accented_notes.append(scale_degree)

        return accented_notes

    @_load_score_content
    def count_number_of_parts(self):

        """
        Applies a simple heuristic: double barlines & final barlines are
        taken as indicators of part structure; their occurrences in the
        score are counted, giving the number of parts.

        This is not foolproof and a manual pass may be required after
        running this function.
        """

        #  TODO: output to metadata

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
    def write_score_to_midi(self, out_path=None, stream=None):
        """write music21 stream to MIDI file"""

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
            # expand repeats (i.e.: ensure MIDI output reflects all
            # repeat markers in the score) and write to disk
            score = stream.expandRepeats()
            score.write('midi', fp=str(out_path))
            return out_path

        except Exception as e:
            # Raise error if write operation fails
            raise RuntimeError(
                f"Failed to write MIDI for {self.score_path.name}. "
                f"Error: {e}"
            ) from e

    @sync_to_s3
    def convert_score_to_abc(self, output_path=None):
        """Reads xml file content as text and converts to ABC Notation"""

        #  Note: parsing multi-part XML scores to extract top line and write to
        #  ABC is beyond project scope as currently defined.

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
        Converts the score to a PDF using LilyPond utilities.
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

    @staticmethod
    def _check_lilypond():
        """
        Checks if LilyPond is installed.
        Returns True if found, False otherwise.
        """
        # Explicitly passing a string 'lilypond' for Windows compatibility
        if shutil.which(str('lilypond')) is None:
            warnings.warn(
                "LilyPond not found on system. PDF conversion is unavailable.",
                UserWarning
            )
            return False
        return True

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
            self.write_score_to_midi(out_path=temp_midi, stream=self.incipit)

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

    def create_soundslice_slice_and_get_embed_url(
            self,
            *,
            collection_metadata,
            itma_id: str,
            _folder_id: int | None = None,
    ) -> str:
        """
        Creates a slice in the collection's Soundslice folder, adds MusicXML,
        populates title from metadata (lookup by unique id / slug), and returns
        the Soundslice embed URL string.

        Internal optimization:
            If _folder_id is provided, no list_folders() calls are made.
            This is safe for parallel processing.

        Note: artist is intentionally an empty string for now. This may
        change if passthrough audio is confirmed as a requirement.
        """

        # validate slug
        slug = str(itma_id).strip()
        if not slug:
            raise ValueError("slug must be a non-empty string.")
        # validate collection_metadata
        if not hasattr(collection_metadata, "get_score_metadata"):
            raise TypeError(
                "collection_metadata arg must be CollectionMetadata object."
            )
        # load & read score metadata
        score_metadata = collection_metadata.get_score_metadata(itma_id)
        score_name = str(score_metadata.get("title") or "").strip()
        if not score_name:
            raise RuntimeError(f"Title field is missing/blank for {slug}.")

        if not self.collection_root:
            raise RuntimeError("Collection root directory must be set to "
                               "proceed.")
        # set Soundslice folder name to mirror name of local collection root
        # dir.
        folder_name = self.collection_root.name
        # load API credentials
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
                    # Fail fast EXCEPT for the expected race case.
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
                artist='',
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
