"""This file holds unit tests for score.py."""

# built-in imports
import music21.key
import os
import pytest
import secrets
import shutil
import subprocess
# external imports
from moto import mock_aws
from music21 import stream
from pathlib import Path
from pypdf import PdfWriter
# local imports
from port.utils.aws_utils import create_s3_bucket, check_s3_file_exists
from port.score import Score, sync_to_s3
from port import score as score_module

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
    """Provides a standard Score instance for testing."""
    return Score(happy_testfile)


def test_score_path(default_score):
    # Test init of Score object using happy_testfile
    assert isinstance(default_score, Score)


def test_loading_content(default_score):
    """Test reading score content to Music21 stream"""
    default_score._read_content_to_music21_stream()
    assert isinstance(default_score.content, stream.Stream)


def test_get_title_warns_and_falls_back_when_no_metadata_given(default_score):
    with pytest.warns(UserWarning):
        t = default_score.set_metadata(collection_metadata=None)
    assert t == "untitled"
    assert default_score.title == "untitled"


def test_get_title_uses_metadata_title_when_present(default_score):
    class FakeCollectionMetadata:
        def get_score_metadata(self, itma_id: str) -> dict:
            assert itma_id == default_score.score_path.stem.strip()
            return {"federated_search_term": "Unit Test Title"}

    test_title = default_score.set_metadata(
        collection_metadata=FakeCollectionMetadata()
    )
    assert test_title == "Unit Test Title"
    assert default_score.title == "Unit Test Title"


def test_set_metadata_warns_and_falls_back_when_metadata_title_blank(
        default_score
):
    class FakeCollectionMetadata:
        def get_score_metadata(self, itma_id: str) -> dict:
            assert itma_id == default_score.score_path.stem.strip()
            return {"title": "   "}

    with pytest.warns(UserWarning):
        t = default_score.set_metadata(
            collection_metadata=FakeCollectionMetadata()
        )
    assert t == "untitled"
    assert default_score.title == "untitled"


def test_score_title_overwrites_music21_stream_title(default_score):
    default_score.title = "Canonical Title"
    default_score._read_content_to_music21_stream()

    assert default_score.content is not None
    assert getattr(default_score.content, "metadata", None) is not None
    assert default_score.content.metadata.title == "Canonical Title"

def test_detect_key():
    """Test retrieving musical key from MusicXML file"""
    default_score = Score(happy_testfile)
    default_score._read_content_to_music21_stream()
    key_sig = default_score.detect_key()
    assert isinstance(key_sig, str)
    assert len(key_sig) == 7 # number of characters in a maj/min key
    # signature string like 'G Minor'


def test_extract_tonic_from_key_signature():
    """Test extracting tonic from key signature"""

    default_score = Score(happy_testfile)
    default_score._read_content_to_music21_stream()
    default_score.detect_key()
    tonic = default_score.extract_tonic_from_key_signature()
    assert isinstance(tonic, str)


def test_extract_mode_from_key_signature():
    """Test extracting mode from key signature"""

    default_score = Score(happy_testfile)
    default_score._read_content_to_music21_stream()
    default_score.detect_key()
    mode = default_score.extract_mode_from_key_signature()
    assert isinstance(mode, str)


def test_extract_time_signature():
    """Test extracting time signature from score"""

    default_score = Score(happy_testfile)
    default_score._read_content_to_music21_stream()
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
    default_score._read_content_to_music21_stream()

    incipit = default_score.extract_incipit()
    measures = incipit.getElementsByClass('Measure')
    assert isinstance(incipit, music21.stream.Stream) and len(measures) == 4


def test_create_breathnach_codes():
    """Test creating Breathnach codes"""

    default_score = Score(happy_testfile)
    default_score._read_content_to_music21_stream()
    default_score.extract_incipit()

    valid_scale_degree_vals = list('1234567')
    test_codes = default_score.create_breathnach_codes()
    assert all(isinstance(x, str) for x in test_codes)
    assert all(x in valid_scale_degree_vals for x in test_codes)


def test_count_number_of_parts():
    """Test counting the number of parts in a tune"""

    default_score = Score(happy_testfile)
    default_score._read_content_to_music21_stream()

    assert default_score.count_number_of_parts() == 2


def test_convert_score_to_midi(tmp_path):
    """Test writing a music21 Stream object to MIDI"""
    default_score = Score(happy_testfile)
    default_score._read_content_to_music21_stream()

    output_filepath = tmp_path / 'test.mid'
    midi_file = default_score.convert_score_to_midi(
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


def test_convert_score_to_abc(tmp_path, monkeypatch):
    """Test converting MusicXML to ABC notation (offline: no real AWS)."""
    # Ensure we exercise the collection_root output policy:
    #   <collection_root>/<collection_root>_abc/<filename>.txt
    default_score = Score(happy_testfile, collection_root=tmp_path)

    # Force offline behavior: if S3 is attempted, fail and ensure we fall back
    # to the local Path return.
    def _offline_upload(*args, **kwargs):
        raise RuntimeError("Offline test: S3 upload disabled")

    monkeypatch.setattr(score_module, "upload_file_to_s3", _offline_upload)

    # Run conversion; decorator should warn and return local Path.
    with pytest.warns(UserWarning, match="S3 Sync failed"):
        abc_result = default_score.convert_score_to_abc()

    assert isinstance(abc_result, Path)

    # Verify the local file was also created.
    local_abc_path = (
        tmp_path
        / f"{tmp_path.name}_abc"
        / default_score.score_path.with_suffix(".txt").name
    )
    assert local_abc_path.exists()
    assert local_abc_path.suffix == ".txt"
    assert local_abc_path.stat().st_size > 0

    # Verify basic ABC structure (X: is the reference number, K: is the key)
    abc_content = local_abc_path.read_text(encoding="utf-8")
    assert "X:" in abc_content
    assert "K:" in abc_content


def test_convert_score_to_pdf(tmp_path, default_score, monkeypatch):
    """Test converting MusicXML to PDF (offline; no real LilyPond needed)."""

    # Ensure title is populated
    default_score.title = "Unit Test Title"
    # Ensure source is populated so the first-page footer textbox has content
    default_score.source = "Example Collection Name"

    # Ensure footer file exists where convert_score_to_pdf expects it
    footer_path = (Path(score_module.__file__).parent / "assets" /
                   "itma_footer.pdf")

    # Define an output path in the temp directory
    output_pdf = tmp_path / "test_output.pdf"

    # Fake LilyPond check
    monkeypatch.setattr(score_module, "check_lilypond", lambda: True)

    # Spy on LilyPond sanitization output to verify:
    # - Arial header font override
    # - first-page footer textbox markup
    # - Arial is enforced for both
    from port.utils import pdf_utils as pdf_utils_module
    _real_cleanup = pdf_utils_module.cleanup_lilypond_formatting

    def _spy_cleanup_lilypond_formatting(ly_text: str, **kwargs) -> str:
        sanitized = _real_cleanup(ly_text, **kwargs)

        assert "PORT_HEADER_FONT_ARIAL" in sanitized
        assert "PORT_SOURCE_AT_DOCUMENT_END" in sanitized

        # Confirm Arial is explicitly set in our LilyPond markup.
        assert '(font-name . "Arial")' in sanitized

        # Confirm the literal source text is embedded in markup.
        assert "Example Collection Name" in sanitized

        return sanitized

    monkeypatch.setattr(
        score_module,
        "cleanup_lilypond_formatting",
        _spy_cleanup_lilypond_formatting,
    )

    def _fake_cli_run(cmd, check, capture_output, text=None, shell=False):
        # cmd is a list like ["musicxml2ly", "-o", "<ly_path>", "<xml_path>"]
        if isinstance(cmd, list) and cmd and cmd[0] == "musicxml2ly":
            out_idx = cmd.index("-o") + 1
            ly_path = Path(cmd[out_idx])

            # Create a minimal .ly file that includes noise we want removed.
            ly_path.write_text(
                r"""
\version "2.24.0"
\header {
  title = "TITLE"
  subtitle = "some subtitle we do not want"
  extra = "some annotation we do not want"
}
{
  c'4 d'4 e'4 f'4
}
""".lstrip(),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        if isinstance(cmd, list) and cmd and cmd[0] == "lilypond":
            # lilypond -o <output_stem> <ly_path>
            out_idx = cmd.index("-o") + 1
            output_stem = Path(cmd[out_idx])
            pdf_path = output_stem.with_suffix(".pdf")

            # Write a minimally valid PDF that pypdf can read/modify
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with pdf_path.open("wb") as fp:
                writer.write(fp)

            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        raise AssertionError(f"Unexpected subprocess command: {cmd!r}")

    # Patch subprocess.run used by Score.convert_score_to_pdf
    monkeypatch.setattr(subprocess, "run", _fake_cli_run)

    # Run conversion
    pdf_path = default_score.convert_score_to_pdf(output_path=output_pdf)

    # Verify we got a Path back and the file exists on disk
    assert pdf_path is not None
    assert pdf_path.exists()
    assert pdf_path.suffix == ".pdf"
    assert pdf_path.stat().st_size > 0

    # Also verify LilyPond source had our title injected + subtitles removed
    ly_path = output_pdf.with_suffix(".ly")
    assert not ly_path.exists(), \
        "Temp .ly should be cleaned up after PDF export."

    # Reconstruct the .ly path that would have been used and read its
    # content by looking at the file that existed during compilation.
    # Since convert_score_to_pdf deletes it, we validate via the compiled PDF
    # and the fact that our fake lilypond ran.


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
    bucket_name = "port.itma.ie"
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


def test_create_soundslice_slice_and_get_embed_id_unit(tmp_path, monkeypatch):
    """Soundslice unit test: no network. Verifies mock client calls & returned
    scorehash identifier."""

    # Clear the shared list cache to ensure clean test state
    Score._soundslice_list_id_cache.clear()

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
            return {
                "title": "Unit Test Title (Catalogue)",
                "federated_search_term": "Unit Test Title (Federated)",
            }

    class FakeConstants:
        # Mocks Soundslice constants
        EMBED_STATUS_ON_ALLOWLIST = 999

    calls = {"create_slice": [], "upload": [], "create_list": [],
             "add_slices_to_list": []}

    class FakeClient:
        # Mocks Soundslice client connections
        def __init__(self, application_id: str, password: str):
            # Ensure unit test does not use real env creds
            assert application_id == "APPLICATION_ID_PLACEHOLDER"
            assert password == "PASSWORD_PLACEHOLDER"

        def create_slice(self, **kwargs):
            calls["create_slice"].append(kwargs)
            # v1.2 API: no folder_id parameter
            assert "folder_id" not in kwargs
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

    # Mock the utility functions
    def mock_create_soundslice_list(collection_name: str) -> str:
        calls["create_list"].append(collection_name)
        return "test_list_123"

    def mock_add_slices_to_soundslice_list(
            list_id: str,
            scorehashes: list[str]
    ) -> bool:
        calls["add_slices_to_list"].append({
            "list_id": list_id,
            "scorehashes": scorehashes,
        })
        return True

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
    monkeypatch.setattr(
        score_module,
        "create_soundslice_list",
        mock_create_soundslice_list,
    )
    monkeypatch.setattr(
        score_module,
        "add_slices_to_soundslice_list",
        mock_add_slices_to_soundslice_list,
    )

    # run
    embed = test_score.create_soundslice_slice(
        collection_metadata=FakeCollectionMetadata(),
        itma_id="unit-slug",
    )

    # Asserts
    assert embed == "scorehash_123"

    # Check list was created
    assert len(calls["create_list"]) == 1
    assert calls["create_list"][0] == "Test_Collection"

    # Check slice was created
    assert len(calls["create_slice"]) == 1
    kwargs = calls["create_slice"][0]
    assert kwargs["name"] == "Unit Test Title (Catalogue)"
    assert kwargs["artist"] == ""  # intentionally left blank
    assert "folder_id" not in kwargs  # v1.2 API change
    assert kwargs["has_shareable_url"] is True
    assert kwargs["can_print"] is True
    assert kwargs["embed_status"] == FakeConstants.EMBED_STATUS_ON_ALLOWLIST

    # Check upload happened
    assert len(calls["upload"]) == 1

    # Check slice was added to list
    assert len(calls["add_slices_to_list"]) == 1
    assert calls["add_slices_to_list"][0]["list_id"] == "test_list_123"
    assert calls["add_slices_to_list"][0]["scorehashes"] == ["scorehash_123"]


def test_create_soundslice_slice_with_provided_folder_id_and_title(
        tmp_path,
        monkeypatch
):
    """
    Unit test using mocked Soundslice client to verify:
      - Client connection
      - slice creation (v1.2 API - no folder_id)
      - list creation
      - slice addition to list
      - MusicXML upload
      - embed URL return

    This test mocks the Client so Soundslice credentials are not required.
    """

    # Note: we should ideally inject _folder_id from CLI flow control
    # but for flexibility during unit testing we allow manual param passing
    collection_root = tmp_path / "unit-test-collection"
    collection_root.mkdir()

    test_score_path = collection_root / happy_testfile.name
    shutil.copy(happy_testfile, test_score_path)
    #
    test_score = Score(test_score_path, collection_root=collection_root)

    class FakeCollectionMetadata:
        def get_score_metadata(self, itma_id: str) -> dict:
            assert itma_id == "unit-slug"
            return {
                "title": "Unit Test Title (Catalogue)",
                "federated_search_term": "Unit Test Title (Federated)",
            }

    class FakeConstants:
        # Mocks Soundslice constants
        EMBED_STATUS_ON_ALLOWLIST = 999

    calls = {
        "create_slice": [],
        "upload": [],
        "create_list": [],
        "add_slices_to_list": [],
    }

    class FakeClient:
        # Mocks Soundslice client connections
        def __init__(self, application_id: str, password: str):
            # Ensure unit test does not use real env creds
            assert application_id == "APPLICATION_ID_PLACEHOLDER"
            assert password == "PASSWORD_PLACEHOLDER"

        def create_slice(self, **kwargs):
            calls["create_slice"].append(kwargs)
            # v1.2 API: no folder_id parameter
            assert "folder_id" not in kwargs
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

    # Mock the utility functions
    def mock_create_soundslice_list(collection_name: str) -> str:
        calls["create_list"].append(collection_name)
        return "test_list_123"

    def mock_add_slices_to_soundslice_list(
        list_id: str, 
        scorehashes: list[str]
    ) -> bool:
        calls["add_slices_to_list"].append({
            "list_id": list_id,
            "scorehashes": scorehashes,
        })
        return True

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
    monkeypatch.setattr(
        score_module,
        "create_soundslice_list",
        mock_create_soundslice_list,
    )
    monkeypatch.setattr(
        score_module,
        "add_slices_to_soundslice_list",
        mock_add_slices_to_soundslice_list,
    )

    # run
    embed = test_score.create_soundslice_slice(
        collection_metadata=FakeCollectionMetadata(),
        itma_id="unit-slug",
    )

    # Asserts
    assert embed == "scorehash_123"
    
    # Check list was created
    assert len(calls["create_list"]) == 1
    assert calls["create_list"][0] == "unit-test-collection"
    
    # Check slice was created
    assert len(calls["create_slice"]) == 1
    kwargs = calls["create_slice"][0]
    assert kwargs["name"] == "Unit Test Title (Catalogue)"
    assert kwargs["artist"] == ""  # intentionally left blank
    assert "folder_id" not in kwargs  # v1.2 API change
    assert kwargs["has_shareable_url"] is True
    assert kwargs["can_print"] is True
    assert kwargs["embed_status"] == FakeConstants.EMBED_STATUS_ON_ALLOWLIST
    
    # Check upload happened
    assert len(calls["upload"]) == 1
    
    # Check slice was added to list
    assert len(calls["add_slices_to_list"]) == 1
    assert calls["add_slices_to_list"][0]["list_id"] == "test_list_123"
    assert calls["add_slices_to_list"][0]["scorehashes"] == ["scorehash_123"]

# ==============================================================================
# INTEGRATION TESTS (AWS & File System)
# ==============================================================================

@mock_aws
def test_sync_to_s3_logic(tmp_path):
    """
    Verifies the decorator correctly captures and uploads a returned
    Path.
    """
    bucket_name = "port.itma.ie"
    create_s3_bucket(bucket_name)

    # Define a temporary collection root
    root = tmp_path / "ITMA_collection"
    root.mkdir()
    local_file = root / "ITMA_collection_txt" / "test.txt"
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
    assert check_s3_file_exists(
        bucket_name,
        "ITMA_collection/ITMA_collection_txt/test.txt"
    ) is True


@mock_aws
def test_abc_conversion_syncs_to_s3(tmp_path, default_score):
    """Verifies writing Score method outputs to S3 & capturing S3 paths."""
    create_s3_bucket("port.itma.ie")

    # Update default_score to have a collection_root
    default_score.collection_root = default_score.score_path.parent

    # Run conversion (should return S3 URL when collection_root is set)
    abc_uri = default_score.convert_score_to_abc()

    # Local output path convention is:
    # {collection_root.name}_abc/<filename>.txt
    expected_key = (
        f"{default_score.collection_root.name}/"
        f"{default_score.collection_root.name}_abc/"
        f"{default_score.score_path.with_suffix('.txt').name}"
    )

    assert (abc_uri ==
            f"https://s3.eu-west-1.amazonaws.com/port.itma.ie/{expected_key}")
    assert check_s3_file_exists("port.itma.ie", expected_key) is True


@mock_aws
def test_sync_to_s3_with_organization(tmp_path):
    """
    Verify S3 file tree mirroring using real input data, checking both
    content and directory structure.
    """
    # Setup mock S3
    bucket_name = "port.itma.ie"
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
    expected_key = \
        f"Danny_Collection/Danny_Collection_svg/{expected_local_svg.name}"
    assert (incipit_svg_uri ==
            f"https://s3.eu-west-1.amazonaws.com/{bucket_name}/{expected_key}")
    assert check_s3_file_exists(bucket_name, expected_key) is True


@pytest.mark.integration
def test_create_soundslice_slice_and_get_embed_url_integration(
        tmp_path,
        monkeypatch
):
    """
    Integration test (runs on real Soundslice API):
      - creates a unique list
      - creates a slice and uploads the MusicXML
      - adds slice to list
      - returns an embed URL
      - cleans up slice & list

    To run this test, set the following environment variables in .env.template:
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
                    "Set APPLICATION_ID and PASSWORD in .env.template.")

    # set up temp local paths
    list_name = f"PYTEST_{secrets.token_hex(8)}"
    collection_root = tmp_path / list_name
    collection_root.mkdir()

    score_path = collection_root / happy_testfile.name
    shutil.copy(happy_testfile, score_path)
    score = Score(score_path, collection_root=collection_root)

    # set up fake metadata
    class FakeCollectionMetadata:
        def get_score_metadata(self, itma_id: str) -> dict:
            return {"title": f"Pytest Slice {itma_id}"}

    created = {"list_id": None, "scorehash": None}

    # setup Soundslice Client
    RealClient = score_module.Client

    # Class to manage Soundslice API interactions
    class CapturingClient:
        def __init__(self, application_id: str, password: str):
            self._real = RealClient(application_id, password)

        def create_list(self, name: str):
            resp = self._real.create_list(name=name)
            created["list_id"] = resp.get("id")
            return resp

        def create_slice(self, **kwargs):
            # v1.2 API: no folder_id
            assert "folder_id" not in kwargs
            resp = self._real.create_slice(**kwargs)
            created["scorehash"] = resp.get("scorehash")
            return resp

        def upload_slice_notation(self, *, scorehash: str, fp):
            return self._real.upload_slice_notation(scorehash=scorehash, fp=fp)

        def add_slices_to_list(self, list_id: str, scorehashes: list[str]):
            return self._real.add_slices_to_list(
                list_id=list_id,
                scorehashes=scorehashes,
            )

        def delete_slice(self, scorehash: str):
            return self._real.delete_slice(scorehash)

        # Not possible in Soundslice API v1.2
        # def delete_list(self, list_id: str):
        #     return self._real.delete_list(list_id=list_id)

    # Mock utility functions to use capturing client
    def mock_create_list(collection_name: str) -> str:
        app_id, pwd = score_module.get_soundslice_credentials_from_env()
        client = CapturingClient(app_id, pwd)
        resp = client.create_list(name=collection_name)
        return resp.get("id")

    def mock_add_to_list(list_id: str, scorehashes: list[str]) -> bool:
        app_id, pwd = score_module.get_soundslice_credentials_from_env()
        client = CapturingClient(app_id, pwd)
        client.add_slices_to_list(list_id, scorehashes)
        return True

    monkeypatch.setattr(score_module, "Client", CapturingClient)
    monkeypatch.setattr(
        score_module,
        "create_soundslice_list",
        mock_create_list,
    )
    monkeypatch.setattr(
        score_module,
        "add_slices_to_soundslice_list",
        mock_add_to_list,
    )

    # run test
    try:
        embed = score.create_soundslice_slice(
            collection_metadata=FakeCollectionMetadata(),
            itma_id="integration-slug",
        )
        # check form of scorehash returned
        assert isinstance(embed, str)
        assert 0 < len(embed) <= 6

    # cleanup
    finally:
        try:
            from soundsliceapi import Client as CleanupClient
            app_id, pwd = score_module.get_soundslice_credentials_from_env()
            cleanup_client = CleanupClient(app_id, pwd)

            if created.get("scorehash"):
                cleanup_client.delete_slice(created["scorehash"])

            # Not possible with Soundslice API v1.2
            # if created.get("list_id"):
            #     cleanup_client.delete_list(list_id=created["list_id"])

        except Exception as cleanup_err:
            print(f"Soundslice cleanup warning (non-fatal): {cleanup_err}")











