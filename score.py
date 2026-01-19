"""This file will hold a 'Score' Python class, modeling a single digital music
score"""

import copy
import inspect
import tempfile
import warnings

import music21
import os
import pandas as pd

from functools import wraps
from pathlib import Path

from abc_xml_converter import convert_xml2abc
from collections import Counter
from music21 import analysis, bar, key, meter, note, chord
from music21.analysis.discrete import SimpleWeights
from pandas.core.config_init import max_cols


class Score:

    # TODO: type hinting

    """
    Score class object represents a digital music score encoded as a MusicXML
    file. A Score object can be instantiated via the 'score_path' argument,
    which must point to a single MusicXML file. Tune objects can be created
    individually or can be automatically instantiated in bulk at corpus-level
    when a Collection object is instantiated.

    Attributes:

    score_path -- path to a MusicXML music score file.
    content -- music21 Stream object representing the musical content.
    incipit -- music21 Stream object representing the 4-bar incipit.
    metadata_path -- path to a csv file containing metadata for the score.
    abc -- ABC notation representation of the score.

    Properties:

    extracted_key -- music21 KeySignature object representing the key
    signature.
    
    """

    METADATA_FIELDS = [
        'Title',
        'Alternative_title',
        'Composer',
        'Tune_type',
    ]
    
    DEFAULT_TIME_SIG = "4/4"

    def __init__(self, score_path):

        """
        Initializes Score object.

        Args:
            score_path -- path to a MusicXML music score file.
        """

        self.score_path = score_path
        # ensure that score_path points to a MusicXML file
        self._validate_score_file()
        self.content = None
        self.incipit = None
        self.key_signature = None
        self.alt_key_signature = None
        self._keys_flag = True
        self.abc = None
        self.metadata_path = None
        self.metadata = None
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

    def read_content_to_music21_stream(self):
        """Reads content from MusicXML file into a music21 Stream object."""
        self.content = music21.converter.parse(self.score_path)

    def ensure_loaded(func):
        """Ensures score content is loaded"""
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.content is None:
                self.read_content_to_music21_stream()
            return func(self, *args, **kwargs)

        return wrapper

    @ensure_loaded
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

    @ensure_loaded
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

    @ensure_loaded
    def find_key_signature(self):

        """
        Compares key signature encoded in the score vs
        algorithmically detected key signature. Makes note of cases where the
        two values are not in agreement.
        """

        # TODO: Test

        key_signature = self._read_key_signature_from_score()
        detected_key = self._detect_key_signature_algorithmically()

        if key_signature != detected_key:
            self._keys_flag = False
        else:
            self._keys_flag = True

        return self.key_signature

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

    @ensure_loaded
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

    @ensure_loaded
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
        incipit = topline.measures(1, 4)

        self.incipit = incipit
        return incipit

    @ensure_loaded
    def create_breathnach_codes(self):

        """
        Creates Breathnach code. These codes are diatonic scale degree
        sequences representing rhythmically-emphasised notes in the incipit.
        """

        # TODO: write to csv

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

        diatonic_scale = key_sig.getScale()
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

    @ensure_loaded
    def count_number_of_parts(self):
        
        """
        Applies a simple heuristic: double barlines & final barlines are
        taken as indicators of part structure; their occurrences in the
        score are counted, giving the number of parts.
        
        This is not foolproof and a manual pass may be required after 
        running this function.
        """

        #  TODO: write output to csv

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

    @ensure_loaded
    def write_score_to_midi(self, out_path):
        """write music21 stream to MIDI file"""

        # TODO: write output to AWS

        score = self.content
        output = score.write('midi', fp=out_path)
        return output

    def convert_score_to_abc(self):
        """Reads xml file content as text and converts to ABC Notation"""

        # TODO: Write output to file with an appropriate filename

        #  Note: selecting the top line from mulit-part scores is beyond
        #  project scope.

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

            self.abc = abc_content
            return abc_content

        except Exception as e:
            warnings.warn(
                f"Failed to convert {self.score_path.name} to ABC: {e}"
            )

            return None

    def convert_score_to_pdf(self):
        pass

    def convert_incipit_to_midi(self):
        # may not need to be a stand-alone method (combine with method below?)
        pass

    def convert_midi_incipit_to_mp3(self):
        pass

    def convert_incipit_to_lilypond(self):
        # may not need to be a stand-alone method (combine with method below?)
        pass

    def convert_incipit_to_svg(self):
        # needs Lilypond installed as an external dependency
        pass

    def copy_musicxml_file_to_aws(self):
        # use as first step in AWS testing -- possibly make into a
        # input-agnostic helper method (or maybe just use BOTO3 setter?)
        pass

    def read_score_metadata_from_csv(self, metadata_path):
        self.metadata_path = metadata_path
        self.metadata = pd.read_csv(self.metadata_path)
        # TODO: Extract single row of metadata corresponding to the score
        #  being processed.
        pass

    def _extract_metadata_field(self, field_name):
        # private helper method acting on self.metadata DataFrame
        pass

    def extract_metadata(self):
        # TODO: call _extract_metadata_field for fields listed in
        #  METADATA_FIELDS class constant
        pass













