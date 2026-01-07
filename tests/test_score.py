"""This file holds unit tests for the Score class."""
import music21.key
import pytest

from music21 import stream
from pathlib import Path
from score import Score

# Get cwd
BASE_DIR = Path(__file__).resolve().parent
# relative path to test data
test_data_dir = (
        BASE_DIR / "Test_data_for_Port" /
        "100684" / "morrison_tutor_xml"
                 )

happy_testfile = test_data_dir / 'morrison_tutor_1.xml'
sad_testfile = test_data_dir / '100684_001.mxl'

@pytest.fixture(autouse=True)
def default_score():
    """Provides a standard User instance for testing."""
    return Score(happy_testfile)

def test_score_path_(default_score):
    # Test init of Score object using happy_testfile
    assert isinstance(default_score, Score)

def test_loading_score_content_(default_score):
    default_score.read_score_content_to_music21_stream()
    assert isinstance(default_score.score_content, stream.Stream)

def test_key_signature_detection_():
    default_score = Score(happy_testfile)
    default_score.read_score_content_to_music21_stream()
    key_sig = default_score.detect_key_signature()
    assert isinstance(key_sig, music21.key.Key)



