"""This file holds unit tests for score.py."""

# built-in imports
import music21.key
import os
import pytest
import re
import secrets
import shutil
# external imports
from moto import mock_aws
from music21 import stream
from pathlib import Path
# local imports
from aws_utils import create_s3_bucket, check_s3_file_exists
from score import Score, sync_to_s3
import score as score_module

# Get cwd
BASE_DIR = Path(__file__).resolve().parent
# relative path to test data
test_data_dir = (
        BASE_DIR / "Test_data_for_Port" /
        "100684" / "morrison_tutor_xml"
                 )

happy_testfile = test_data_dir / 'morrison_tutor_1.xml'
sad_testfile = test_data_dir / '100684_001.mxl'

# ==============================================================================
# UNIT TESTS (Logic & Analysis)
# ==============================================================================

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
    abc_path = default_score.convert_score_to_abc()

    # Verify we got a Path back and the file exists
    assert isinstance(abc_path, Path)
    assert abc_path.exists()
    assert abc_path.suffix == ".abc"
    assert abc_path.stat().st_size > 0

    # Verify basic ABC structure (X: is the reference number, K: is the key)
    abc_content = abc_path.read_text(encoding="utf-8")
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
    """Test converting incipit to a cropped SVG file"""

    # Define an output path in the temp directory
    output_svg = tmp_path / "incipit_test.svg"

    # Run conversion
    svg_path = default_score.convert_incipit_to_svg(output_path=output_svg)

    assert svg_path is not None
    assert svg_path.exists()
    assert svg_path.suffix == '.svg'
    assert svg_path.stat().st_size > 0


def test_convert_incipit_to_mp3(tmp_path, default_score):
    """Test converting incipit to an MP3 file"""

    # Define an output path in the temp directory
    output_mp3 = tmp_path / "incipit_test.mp3"

    # Run conversion
    mp3_path = default_score.convert_incipit_to_mp3(output_path=output_mp3)

    # Verify we got a Path back and the file exists on disk
    assert mp3_path is not None
    assert mp3_path.exists()
    assert mp3_path.suffix == '.mp3'
    # Check that the file has actual data
    assert mp3_path.stat().st_size > 0

@mock_aws
def test_copy_musicxml_file_to_aws(tmp_path):
    """Test uploading Score to AWS."""
    # Setup mock environment
    bucket_name = "scores.itma.ie"
    create_s3_bucket(bucket_name)

    # Create a nested local dir structure under a temp collection root
    collection_root = tmp_path / "ITMA_Collection"
    sub_folder = collection_root / "Morrison_Tutor"
    sub_folder.mkdir(parents=True)

    # Copy the established test MusicXML file into that structure
    target_xml = sub_folder / happy_testfile.name
    shutil.copy(happy_testfile, target_xml)

    # Instantiate Score object and run mock upload
    score_obj = Score(target_xml)
    s3_uri = score_obj.copy_musicxml_file_to_aws(collection_root=collection_root)

    # Verify the S3 key is the same as the (local) relative path
    expected_key = f"Morrison_Tutor/{happy_testfile.name}"
    assert check_s3_file_exists(bucket_name, expected_key) is True

    # Verify the returned S3 URI matches the uploaded object location
    assert str(s3_uri) == f"s3://{bucket_name}/{expected_key}"


def test_create_soundslice_slice_and_get_embed_url_unit(tmp_path, monkeypatch):
    """Soundslice unit test: no network. Verifies mock client calls & returned
    embed URL."""

    # Create a temp collection folder and copy a real MusicXML file into it
    collection_root = tmp_path / "Test_Collection"
    collection_root.mkdir()
    test_score_path = collection_root / happy_testfile.name
    shutil.copy(happy_testfile, test_score_path)
    #
    test_score = Score(test_score_path, collection_root=collection_root)

    class FakeCollectionMetadata:
        def get_score_metadata(self, itma_id: str) -> dict:
            assert itma_id == "unit-slug"
            return {"title": "Unit Test Title"}

    class FakeConstants:
        # Mocks Soundslice constants
        EMBED_STATUS_ON_ALLOWLIST = 999

    calls = {"create_slice": [], "upload": []}

    class FakeClient:
        # Mocks Soundslice client connections
        def __init__(self, application_id: str, password: str):
            # Ensure unit test does not use real env creds
            assert application_id == "APPLICATION_ID_PLACEHOLDER"
            assert password == "PASSWORD_PLACEHOLDER"

        def create_slice(self, **kwargs):
            calls["create_slice"].append(kwargs)
            return {
                "scorehash": "scorehash_123",
                "embed_url": "/slices/scorehash_123/embed/",
            }

        def upload_slice_notation(self, *, scorehash: str, fp):
            chunk = fp.read(32)
            assert scorehash == "scorehash_123"
            assert isinstance(chunk, (bytes, bytearray))
            assert len(chunk) > 0
            calls["upload"].append({"scorehash": scorehash})

        def list_folders(self):
            raise AssertionError(
                "list_folders() should not be called when _folder_id is provided"
            )

        def create_folder(self, name: str):
            raise AssertionError(
                "create_folder() should not be called when _folder_id is provided"
            )

    # use pytest's built-in mocking
    monkeypatch.setattr(
        score_module,
        "get_soundslice_credentials_from_env",
        lambda: (
            "APPLICATION_ID_PLACEHOLDER",
            "PASSWORD_PLACEHOLDER",
        ),
    )

    monkeypatch.setattr(score_module, "Client", FakeClient)
    monkeypatch.setattr(score_module, "Constants", FakeConstants)

    # run
    url = test_score.create_soundslice_slice_and_get_embed_url(
        collection_metadata=FakeCollectionMetadata(),
        itma_id="unit-slug",
        _folder_id=123,
    )

    # Asserts
    assert url == "https://www.soundslice.com/slices/scorehash_123/embed/"
    assert len(calls["create_slice"]) == 1
    assert len(calls["upload"]) == 1

    kwargs = calls["create_slice"][0]
    assert kwargs["name"] == "Unit Test Title"
    assert kwargs["artist"] == ""  # intentionally left blank
    assert kwargs["folder_id"] == 123
    assert kwargs["has_shareable_url"] is True
    assert kwargs["can_print"] is True
    assert kwargs["embed_status"] == FakeConstants.EMBED_STATUS_ON_ALLOWLIST

# ==============================================================================
# INTEGRATION TESTS (AWS & File System)
# ==============================================================================

@mock_aws
def test_sync_to_s3_logic(tmp_path):
    """Verifies the decorator correctly identifies and uploads a returned Path."""
    bucket_name = "scores.itma.ie"
    create_s3_bucket(bucket_name)

    # Define a temporary collection root
    root = tmp_path / "ITMA"
    root.mkdir()
    local_file = root / "folder" / "test.txt"
    local_file.parent.mkdir()
    local_file.write_text("data")

    # Create a mock class to test the decorator in isolation
    class MockScore:
        def __init__(self, root):
            self.collection_root = root

        @sync_to_s3
        def mock_method(self):
            return local_file

    tester = MockScore(root)
    tester.mock_method()

    # Assert S3 has saved the file at the correct relative path
    assert check_s3_file_exists(bucket_name, "folder/test.txt") is True


@mock_aws
def test_abc_conversion_syncs_to_s3(tmp_path, default_score):
    """Verifies writing Score method outputs to S3 & capturing S3 paths."""
    create_s3_bucket("scores.itma.ie")

    # Update default_score to have a collection_root
    default_score.collection_root = default_score.score_path.parent

    # Run conversion (should return S3 URI when collection_root is set)
    abc_uri = default_score.convert_score_to_abc()

    # Local output path convention is:
    # {collection_root.name}_abc/<filename>.abc
    expected_key = (
        f"{default_score.collection_root.name}_abc/"
        f"{default_score.score_path.with_suffix('.abc').name}"
    )

    assert abc_uri == f"s3://scores.itma.ie/{expected_key}"
    assert check_s3_file_exists("scores.itma.ie", expected_key) is True


@mock_aws
def test_sync_to_s3_with_organization(tmp_path):
    """
    Verify S3 file tree mirroring using real input data, checking both
    content and directory structure.
    """
    # Setup mock S3
    bucket_name = "scores.itma.ie"
    create_s3_bucket(bucket_name)

    # Setup a mock collection in tmp_path
    collection_root = tmp_path / "Danny_Collection"
    collection_root.mkdir()

    # Copy our test file into the temp collection
    target_xml = collection_root / happy_testfile.name
    shutil.copy(happy_testfile, target_xml)

    # Instantiate Score and run conversion
    score = Score(target_xml, collection_root=collection_root)
    incipit_svg_uri = score.convert_incipit_to_svg()

    # check local directory structure & file contents
    expected_local_dir = collection_root / "Danny_Collection_svg"
    expected_local_svg = (expected_local_dir /
                          target_xml.with_suffix(".svg").name
                          )
    assert expected_local_svg.parent == expected_local_dir
    assert expected_local_svg.exists()
    assert expected_local_svg.stat().st_size > 0  # check file is not empty

    # check S3 mirroring matches the local relative dir structure
    expected_key = f"Danny_Collection_svg/{expected_local_svg.name}"
    assert incipit_svg_uri == f"s3://{bucket_name}/{expected_key}"
    assert check_s3_file_exists(bucket_name, expected_key) is True


@pytest.mark.integration
def test_create_soundslice_slice_and_get_embed_url_integration(
        tmp_path,
        monkeypatch
):
    """
    Integration test (runs on real Soundslice API):
      - creates a unique folder
      - creates a slice and uploads the MusicXML
      - returns an embed URL
      - cleans up slice & folder

    To run this test, set the following environment variables in .env:
      RUN_SOUNDSLICE_INTEGRATION_TESTING=y
      APPLICATION_ID, PASSWORD
    """

    if os.getenv("RUN_SOUNDSLICE_INTEGRATION_TESTING") != "y":
        pytest.skip(
            "Set RUN_SOUNDSLICE_INTEGRATION_TESTING=y"
            " to run Soundslice integration tests."
        )

    if not os.getenv("APPLICATION_ID") or not os.getenv("PASSWORD"):
        pytest.skip("Missing Soundslice credentials. "
                    "Set APPLICATION_ID and PASSWORD in .env.")

    # set up temp local paths
    folder_name = f"PYTEST_{secrets.token_hex(8)}"
    collection_root = tmp_path / folder_name
    collection_root.mkdir()

    score_path = collection_root / happy_testfile.name
    shutil.copy(happy_testfile, score_path)
    score = Score(score_path, collection_root=collection_root)

    # set up fake metadata
    class FakeCollectionMetadata:
        def get_score_metadata(self, itma_id: str) -> dict:
            return {"title": f"Pytest Slice {itma_id}"}

    created = {"folder_id": None, "scorehash": None}

    #setup Soundslice Client
    RealClient = score_module.Client

    # Class to manage Soundslice API interactions
    class CapturingClient:
        def __init__(self, application_id: str, password: str):
            self._real = RealClient(application_id, password)

        def list_folders(self):
            return self._real.list_folders()

        def create_folder(self, name: str):
            return self._real.create_folder(name=name)

        def create_slice(self, **kwargs):
            created["folder_id"] = kwargs.get("folder_id")
            resp = self._real.create_slice(**kwargs)
            created["scorehash"] = resp.get("scorehash")
            return resp

        def upload_slice_notation(self, *, scorehash: str, fp):
            return self._real.upload_slice_notation(scorehash=scorehash, fp=fp)

        def delete_slice(self, scorehash: str):
            return self._real.delete_slice(scorehash)

        def delete_folder(self, *, folder_id: int):
            return self._real.delete_folder(folder_id=folder_id)

    monkeypatch.setattr(score_module, "Client", CapturingClient)

    # run test
    try:
        url = score.create_soundslice_slice_and_get_embed_url(
            collection_metadata=FakeCollectionMetadata(),
            itma_id="integration-slug",
            _folder_id=None,
        )

        assert isinstance(url, str)
        assert url.startswith("https://www.soundslice.com")
        assert "/embed" in url

        assert created["scorehash"], \
            "Did not capture scorehash from create_slice response."
        assert created["folder_id"], \
            "Did not capture folder_id from create_slice call."

    # cleanup
    finally:
        try:
            from soundsliceapi import Client as CleanupClient
            app_id, pwd = score_module.get_soundslice_credentials_from_env()
            cleanup_client = CleanupClient(app_id, pwd)

            if created.get("scorehash"):
                cleanup_client.delete_slice(created["scorehash"])

            if created.get("folder_id"):
                cleanup_client.delete_folder(
                    folder_id=int(created["folder_id"])
                )

        except Exception as cleanup_err:
            print(f"Soundslice cleanup warning (non-fatal): {cleanup_err}")











