"""
This file holds unit tests for the aws_utils module. Our use of the AWS boto3
Python library means we no longer need to connect to AWS via the CLI app.

Note: much of this content may ultimately be superseded by direct calls to the
AWS CLI.
"""

from aws_utils import (
    check_s3_object_exists,
    create_s3_bucket,
    download_object_from_s3,
    list_s3_objects,
    upload_file_to_s3,
)

import os
import pytest

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws


# Set dummy credentials
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"


def _s3_resource():
    """Test helper: create an S3 resource in the required region."""
    return boto3.resource("s3", region_name="eu-west-1")


# --- Happy Path Tests ---

@mock_aws
def test_create_s3_bucket_happy_path():
    """Test creation of a new S3 bucket."""
    bucket_name = create_s3_bucket(bucket_name="test-bucket")
    assert bucket_name == "test-bucket"


@mock_aws
def test_check_s3_object_exists_happy_path():
    """Test checking if an S3 object exists."""
    s3 = _s3_resource()
    create_s3_bucket("test-bucket")
    # create mock object
    s3.Object("test-bucket", "folder/example.txt").put(Body=b"hello")

    assert check_s3_object_exists("test-bucket", "folder/example.txt") is True


@mock_aws
def test_list_s3_objects_happy_path():
    """Test listing all objects in an S3 bucket."""
    s3 = _s3_resource()
    create_s3_bucket("test-bucket")
    # create mock objects
    s3.Object("test-bucket", "a.txt").put(Body=b"a")
    s3.Object("test-bucket", "b.txt").put(Body=b"b")
    # list and sort bucket contents
    keys = list_s3_objects("test-bucket")
    assert sorted(keys) == ["a.txt", "b.txt"]


@mock_aws
def test_upload_file_to_s3_happy_path(tmp_path):
    """Test uploading an object to an S3 bucket."""
    s3 = _s3_resource()
    create_s3_bucket("test-bucket")
    # create a temp test file on the fly using pytest's 'tmp_path' fixture
    local_file = tmp_path / "example.txt"
    local_file.write_text("uploaded!", encoding="utf-8")
    # Upload using filename obj key
    upload_file_to_s3("test-bucket", str(local_file))
    assert check_s3_object_exists("test-bucket", "example.txt") is True
    # read file content from s3 & check it matches
    body = s3.Object("test-bucket", "example.txt").get()["Body"].read()
    assert body == b"uploaded!"


@mock_aws
def test_upload_file_to_s3_with_root_dir(tmp_path):
    """Verify upload_file_to_s3 preserves directory structure relative to root_dir."""
    create_s3_bucket("test-bucket")

    # Create a nested structure: root/collection/tune.xml
    root_dir = tmp_path / "root"
    sub_dir = root_dir / "collection"
    sub_dir.mkdir(parents=True)
    local_file = sub_dir / "tune.xml"
    local_file.write_text("music content", encoding="utf-8")

    # Upload using root_dir
    upload_file_to_s3("test-bucket", str(local_file), root_dir=str(root_dir))

    # Assert S3 key uses forward slashes and preserves the 'collection' folder
    assert check_s3_object_exists("test-bucket", "collection/tune.xml") is True


@mock_aws
def test_download_object_from_s3_happy_path():
    """Test downloading an object from an S3 bucket."""
    s3 = _s3_resource()
    create_s3_bucket("test-bucket")
    # create mock object
    s3.Object("test-bucket", "data.bin").put(Body=b"\x00\x01\x02")
    # dowload and check content
    data = download_object_from_s3("test-bucket", "data.bin")
    assert data == b"\x00\x01\x02"


# --- Unhappy Path Tests ---

@mock_aws
def test_create_s3_bucket_unhappy_path_already_owned():
    """Test handling of an existing S3 bucket."""
    first_obj = create_s3_bucket("test-bucket")
    # below should not raise an error -- will be caught by create_s3_bucket()
    second_obj = create_s3_bucket("test-bucket")
    assert first_obj == second_obj == "test-bucket"


@mock_aws
def test_download_object_from_s3_unhappy_path_no_such_key():
    """Test file download when an S3 object does not exist."""
    # create empty bucket
    create_s3_bucket("test-bucket")

    # Updated to expect FileNotFoundError instead of ClientError
    with pytest.raises(FileNotFoundError):
        # attempt to download object from empty bucket
        download_object_from_s3("test-bucket", "test.txt")


@mock_aws
def test_list_s3_objects_unhappy_path_no_such_bucket():
    """Test behaviour of aws_utils.list_s3_objects when an S3 bucket does not
    exist."""
    with pytest.raises(ClientError):
        # list contents of non-existent bucket
        list_s3_objects("bucket-does-not-exist")


@mock_aws
def test_upload_file_to_s3_unhappy_path_no_such_bucket(tmp_path):
    """Test file upload when an S3 bucket does not exist."""
    # create tmp local file & write content
    local_file = tmp_path / "example.txt"
    local_file.write_text("content", encoding="utf-8")

    with pytest.raises(ClientError):
        # attempt to upload to non-existent bucket
        upload_file_to_s3("bucket-does-not-exist", str(local_file))


@mock_aws
def test_download_object_from_s3_unhappy_path_no_such_key():
    """Test file download when an S3 object does not exist."""
    # create empty bucket
    create_s3_bucket("test-bucket")

    # Use FileNotFoundError to match the refactored aws_utils.py
    with pytest.raises(FileNotFoundError):
        download_object_from_s3("test-bucket", "non-existent-key.txt")


@mock_aws
def test_upload_file_to_s3_fails_if_local_file_missing():
    """Verify upload fails gracefully if local file doesn't exist."""
    create_s3_bucket("test-bucket")
    with pytest.raises(FileNotFoundError):
        upload_file_to_s3("test-bucket", "non_existent_file.xml")
