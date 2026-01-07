"""This file will hold a 'Score' Python class, modeling a single digital music
score"""

import music21
import os
import pandas as pd

from music21 import key

class Score:

    """
    Score class object represents a digital music score encoded as a MusicXML
    file. A Score object can be instantiated via the 'score_path' argument,
    which must point to a single MusicXML file. Tune objects can be created
    individually or can be automatically instantiated in bulk at corpus-level
    when a Collection object is instantiated.

    Attributes:

    score_path -- path to a MusicXML music score file.
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
        self.score_content = None
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

    def read_score_content_to_music21_stream(self):
        """Reads content from MusicXML file into a music21 Stream object."""
        self.score_content = music21.converter.parse(self.score_path)

    def detect_key_signature(self):
        # detect key via Music21-s built-in Krumhansl-Schmuckler algorithm
        detected = self.score_content.analyze("key")
        return detected

    def read_key_signature_from_score(self):
        # get any KeySignature objects provided within the score
        # TODO: Compare & count keys returned to come up with a single output.

    def extract_tonic_from_key_signature(self):
        pass

    def extract_mode_from_key_signature(self):
        pass

    def extract_time_signature(self):
        pass

    def create_breathnach_codes(self):
        pass

    def convert_score_to_midi(self):
        pass

    def convert_score_to_abc(self):
        pass

    def convert_score_to_pdf(self):
        pass

    def copy_musicxml_file_to_aws(self):
        pass

    def extract_incipit_from_score(self):
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

    def extract_metadat(self):
        # TODO: call _extract_metadata_field for fields listed in
        #  METADATA_FIELDS class constant
        pass













