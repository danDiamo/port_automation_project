"""This file will hold a 'Score' Python class, modeling a single digital music
score"""

# TODO: error handling

import music21
import os
import pandas as pd

from collections import Counter
from music21 import analysis, bar, key, meter, note, chord
from pandas.core.config_init import max_cols


class Score:

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
    key -- music21 KeySignature object representing the key signature.
    metadata_path -- path to a csv file containing metadata for the score.
    
    """

    METADATA_FIELDS = [
        'Title',
        'Alternative_title',
        'Composer',
        'Tune_type',
        'number_of_parts'
    ]

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
        self.key = None     # TODO: make a property
        self.metadata_path = None
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

    def detect_key_signature(self):
        """Detects key via Music21-s built-in Krumhansl-Schmuckler algorithm"""

        # TODO: Discuss this implementation with ITMA

        # Detect key of each 4-bar 'half phrase' in the score
        key_analysis = analysis.floatingKey.KeyAnalyzer(self.content)
        key_analysis.windowSize = 8
        detected_keys = key_analysis.run()

        # Check that at least one Key object was actually returned
        if detected_keys is None:
            raise ValueError("No key signature detected.")

        # count and rank key signature occurrences
        ranked_key_sigs = Counter(detected_keys)
        max_count = ranked_key_sigs.most_common(1)[0][1]
        # identify the most frequently occurring key signature(s)
        top_ranked = [
            key for key, count in ranked_key_sigs.items() if count == max_count
        ]
        # Return the first occurring of the top-ranked key signature(s)
        self.key = top_ranked[0]
        return top_ranked[0]

    def read_encoded_key_signature_from_score(self):
        """Get any KeySignature objects provided within the score"""

        content = self.content.recurse()
        key_sigs = [ks for ks in content.getElementsByClass(key.KeySignature)]
        # handle cases were no key signatures were provided
        if not key_sigs:
            raise ValueError(
                f"This score does not contain any key signature objects. ")
        # Return only the first key signature object encoded in the score
        self.key = key_sigs[0]
        return key_sigs[0]

    def extract_tonic_from_key_signature(self):

        """
        Extracts the tonic pitch name from the key signature returned by
        the two
        methods above.
        """

        key = self.key
        if key is None:
            raise ValueError("Cannot extract tonic as this score does not "
                             "contain any signature information. Please "
                             "either detect and/or read key signature, "
                             "then retry.")
        # read tonic and return in human-readable format
        return key.tonic.name


    def extract_mode_from_key_signature(self):
        """Extracts the mode from the score's key signature property"""

        key = self.key
        if key is None:
            raise ValueError("Cannot extract mode as this score does not "
                             "contain any signature information. Please "
                             "either detect and/or read key signature, "
                             "then retry.")
        return key.mode

    def extract_time_signature(self):
        """Extracts the time signature from the score"""

        # TODO: Discuss implementation with ITMA -- only takes first time sig

        all_time_signatures = self.content[meter.TimeSignature]
        # Make sure at least one time signature was found
        if not all_time_signatures:
            raise ValueError(
                "This score does not contain any time signature objects."
            )

        # Read the first time signature and return in human-readable format
        time_signature = all_time_signatures[0]
        return time_signature.ratioString

    def extract_incipit(self):
        """Extracts a 4-bar incipit from the score"""

        # TODO: Discuss with ITMA: will score always be single-line melody?
        #  finish functionality here according to answer!

        content = self.content
        #  check score is not empty
        if not content:
            raise ValueError(
                "Cannot extract incipit: either this score is empty or it is "
                "not loading correctly."
            )
        # return first 4 bars
        incipit = content.measures(1, 4)
        self.incipit = incipit
        return incipit

    def create_breathnach_codes(self):

        """
        Creates Breathnach code. These codes are diatonic scale degree
        sequences representing rhythmically-emphasised notes in the incipit.
        """

        # TODO:
        #  test on other time signatures
        #  check with ITMA if we'll need to handle multi-part scores
        #  test handling of rests and chords

        incipit = self.incipit
        key_sig = incipit.analyze('key')
        diatonic_scale = key_sig.getScale()

        # Remove expressions and articulation
        for n in incipit.recurse().notes:
            n.expressions = []

        accented_notes = []
        # filter to retain accented notes only
        # Extract scale degrees for accented notes and store in list
        # Note: use of flatten vs recurse here may change as testing proceeds
        for n in incipit.flatten().notes:
                if n.isNote and n.beat == int(n.beat):
                    scale_degree = diatonic_scale.getScaleDegreeFromPitch(
                        n.pitch)
                    accented_notes.append(scale_degree)

        return accented_notes
    
    def count_number_of_parts(self):
        
        """
        Counts the number of parts in the tune by applying a simple 
        heuristic: double barlines are taken as indicators of part 
        structure.
        
        This is not foolproof and a manual pass may be required after 
        running this function.
        """
        score = self.content
        barlines = score.recurse().getElementsByClass(bar.Barline)

        # derive part structure by identifying double & final barlines
        part_structure = [
            b for b in barlines if b.type == 'double' or b.type == 'final'
                           ]
        # count parts (total = number of markers + 1)
        num_parts = len(part_structure)
        return num_parts

    def convert_score_to_midi(self):
        pass

    def convert_score_to_abc(self):
        pass

    def convert_score_to_pdf(self):
        pass

    def copy_musicxml_file_to_aws(self):
        pass

    def convert_incipit_to_midi(self):
        # may not need to be a stand-alone method (or be required at all)
        pass

    def convert_incipit_to_svg(self):
        # needs Lilypond installed as an external dependency
        pass

    def convert_incipit_to_lilypond(self):
        # may not be necessary?
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













