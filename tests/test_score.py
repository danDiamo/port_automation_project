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

def test_score_path(default_score):
    # Test init of Score object using happy_testfile
    assert isinstance(default_score, Score)

def test_loading_content(default_score):
    """Test reading score content to Music21 stream"""
    # Load test class instance
    default_score.read_content_to_music21_stream()
    assert isinstance(default_score.content, stream.Stream)

def test_key_signature_detection():
    """Test our usage of Music21's algorithmic key signature detection"""
    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    detected_key_sig = None
    # read key signature(s) & handle error if none are present
    try:
        detected_key_sig = default_score.detect_key_signature()
    except ValueError as e:
        print(f"Error: {e}")
    # check type of encoded key signature
    assert isinstance(detected_key_sig, music21.key.Key)

def test_read_key_signature_from_score():
    """Test reading key signature(s) directly from MusicXML file"""
    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    encoded_key_sig = None
    # read key signature(s) & handle error if none are present
    try:
        encoded_key_sig = default_score.read_encoded_key_signature_from_score()
    except ValueError as e:
        print(f"Error: {e}")
    # check type of encoded key signature
    assert isinstance(encoded_key_sig, music21.key.Key)

def test_extract_tonic_from_key_signature():
    """Test extracting tonic from key signature"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()
    default_score.detect_key_signature()
    tonic = default_score.extract_tonic_from_key_signature()
    assert isinstance(tonic, str)

def test_extract_mode_from_key_signature():
    """Test extracting mode from key signature"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()
    default_score.detect_key_signature()
    mode = default_score.extract_mode_from_key_signature()
    assert isinstance(mode, str)

def test_extract_time_signature():
    """Test extracting time signature from score"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    time_sig = None
    try:
        time_sig = default_score.extract_time_signature()
    except ValueError as e:
        print(f"Error: {e}")

    time_sig = default_score.extract_time_signature()
    time_sig_elements = time_sig.split('/')
    assert (len(time_sig_elements) == 2
            and time_sig_elements[0].isdigit()
            and time_sig_elements[1].isdigit())

def test_extract_incipit():
    """Test extracting incipit from score"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    incipit = default_score.extract_incipit()
    melody = incipit.getElementsByClass('Part')[0]
    measures = melody.getElementsByClass('Measure')
    assert isinstance(incipit, music21.stream.Stream) and len(measures) == 4

def test_create_breathnach_codes():
    """Test creating Breathnach codes"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()
    default_score.extract_incipit()

    test_codes = default_score.create_breathnach_codes()
    assert all(isinstance(x, int) and 1 <= x <= 7 for x in test_codes)

def test_count_number_of_parts():
    """Test counting the number of parts in a tune"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    assert default_score.count_number_of_parts() == 2
    











