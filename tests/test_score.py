"""This file holds unit tests for the Score class."""
import re

import music21.key
import os
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

def test_detect_key_signature_algorithmically():
    """Test our usage of Music21's algorithmic key signature detection"""
    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    detected_key_sig = None
    # read key signature(s) & handle error if none are present
    try:
        detected_key_sig = (
            default_score._detect_key_signature_algorithmically()
        )
    except ValueError as e:
        print(f"Error: {e}")
    # check type of return value
    assert isinstance(detected_key_sig, str)
    # check string formatting
    assert len(detected_key_sig.split()) == 2
    # check value assigned to detected_key_sig attr has been updated:
    assert default_score.alt_key_signature is not None
    # check type of detected_key_sig attr
    assert isinstance(default_score.alt_key_signature, music21.key.Key)


def test_read_key_signature_from_score():
    """Test reading key signature(s) directly from MusicXML file"""
    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    key_sig = None
    # read key signature(s) & handle error if none are present
    try:
        key_sig = default_score._read_key_signature_from_score()
    except ValueError as e:
        print(f"Error: {e}")
    # check type of return value
    assert isinstance(key_sig, str)
    # check string formatting
    assert len(key_sig.split()) == 2
    # check value assigned to key_signature attr has been updated:
    assert default_score.key_signature != None
    # check type of key_signature attr
    assert isinstance(default_score.key_signature, music21.key.Key)

def test_find_key_signature():
    """Test finding key signature(s) in MusicXML file"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()
    key_sig = default_score.find_key_signature()
    assert isinstance(key_sig, music21.key.Key)

def test_extract_tonic_from_key_signature():
    """Test extracting tonic from key signature"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()
    default_score._detect_key_signature_algorithmically()
    tonic = default_score.extract_tonic_from_key_signature()
    assert isinstance(tonic, str)

def test_extract_mode_from_key_signature():
    """Test extracting mode from key signature"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()
    default_score._detect_key_signature_algorithmically()
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

    time_sig_elements = time_sig.split('/')
    assert (len(time_sig_elements) == 2
            and time_sig_elements[0].isdigit()
            and time_sig_elements[1].isdigit())

def test_extract_incipit():
    """Test extracting incipit from score"""

    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    incipit = default_score.extract_incipit()
    measures = incipit.getElementsByClass('Measure')
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

def test_write_score_to_midi(tmp_path):
    """Test writing a music21 Stream object to MIDI"""
    default_score = Score(happy_testfile)
    default_score.read_content_to_music21_stream()

    output_filepath = tmp_path / 'test.mid'
    midi_file = default_score.write_score_to_midi(
        out_path= output_filepath)

    assert os.path.exists(output_filepath)
    assert os.path.getsize(midi_file) > 0

    # Use our Score class to compare the opening pitch sequence of the newly
    # generated MIDI file with the original MusicXML score
    try:
        new_score = music21.converter.parse(output_filepath)
        new_score_intro_pitches = [
            note.pitch for note in new_score.recurse().notes][:10]

        default_score_expanded = default_score.content.expandRepeats()
        default_score_intro_pitches = [
            note.pitch for note in default_score_expanded.recurse().notes][:10]

        assert new_score_intro_pitches == default_score_intro_pitches

    except Exception as e:
        pytest.fail(f"Could not parse the generated MIDI file: {e}")


def test_convert_score_to_abc(tmp_path):
    """Test converting MusicXML to ABC notation"""
    default_score = Score(happy_testfile)

    # Test XML-ABC conversion
    abc_content = default_score.convert_score_to_abc()
    # Verify we got a string back
    assert isinstance(abc_content, str)
    assert len(abc_content) > 0

    # Verify basic ABC structure (X: is the reference number, K: is the key)
    assert "X:" in abc_content
    assert "K:" in abc_content

def test_convert_score_to_pdf(tmp_path, default_score):
    """Test converting MusicXML to PDF"""

    # Define an output path in the temp directory
    output_pdf = tmp_path / "test_output.pdf"
    # Run conversion
    pdf_path = default_score.convert_score_to_pdf(output_path=output_pdf)

    # Verify we got a Path back and the file exists on disk
    assert pdf_path is not None
    assert pdf_path.exists()
    assert pdf_path.suffix == '.pdf'
    assert pdf_path.stat().st_size > 0

def test_convert_incipit_to_svg(tmp_path, default_score):
    """Test converting a 4-bar incipit to a cropped SVG file"""

    # Define an output path in the temp directory
    output_svg = tmp_path / "incipit_test.svg"

    # Run conversion
    svg_path = default_score.convert_incipit_to_svg(output_path=output_svg)

    assert svg_path is not None
    assert svg_path.exists()
    assert svg_path.suffix == '.svg'
    assert svg_path.stat().st_size > 0

    











