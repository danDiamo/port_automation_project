"""
This file contains Python functions that use the boto3 library to connect to
Amazon AWS in Python using CLI credentials.

Note: Much of this functionality may be replaced by direct calls
to the AWS CLI.
"""

import boto3
import os

from dotenv import load_dotenv
from botocore.exceptions import ClientError
from typing import List


# Securely load AWS credentials from .env file
load_dotenv()
AWS_REGION = os.getenv("AWS_DEFAULT_REGION")


def _s3_resource():
    """
    Small helper to hardcode use of boto3.resource (i.e. not boto3.client)
    and the 'eu-west-1' AWS region.
    """
    # Hardcode our region in case it's not given in .env
    region = AWS_REGION or "eu-west-1"
    return boto3.resource("s3", region_name=region)


def create_s3_bucket(bucket_name: str) -> str:
    """Creates an S3 bucket and returns its name."""

    s3 = _s3_resource()

    try:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": AWS_REGION},
        )
    except ClientError as e:
        # If the bucket exists, return its name along with the error.
        error_code = (e.response or {}).get("Error", {}).get("Code", "")
        if error_code == "BucketAlreadyOwnedByYou":
            return bucket_name
        raise

    return bucket_name


def check_s3_file_exists(bucket_name: str, object_key: str) -> bool:
    """Checks if an S3 object exists."""
    s3 = _s3_resource()
    obj = s3.Object(bucket_name, object_key)

    try:
        # if load() does not find an object, boto3 raises ClientError.
        obj.load()
        # If no exception was raised, the object exists.
        return True
    except ClientError as e:
        # Return False for "file not found" style errors.
        error_code = (e.response or {}).get("Error", {}).get("Code", "")
        if error_code in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}:
            return False
        raise


def list_s3_objects(bucket_name: str) -> List[str]:
    """Lists all objects in an S3 bucket."""
    s3 = _s3_resource()
    bucket = s3.Bucket(bucket_name)

    keys: List[str] = []
    try:
        # Attempting to iterate will check if the bucket exists
        # a ClientError should be raised if it does not.
        for obj_summary in bucket.objects.all():
            keys.append(obj_summary.key)
    except ClientError:
        raise

    return keys


def upload_file_to_s3(bucket_name: str, file_path: str, root_dir: str = None):
    """
    Uploads a file to an S3 bucket.
    Allows the user to preserve local directory structure in the form of
    AWS prefixes.
    """

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"The local file {file_path} does not exist.")

    s3 = _s3_resource()

    if root_dir:
        # Calculate relative path:
        object_key = os.path.relpath(file_path, root_dir).replace("\\", "/")
    else:
        object_key = os.path.basename(file_path)

    try:
        with open(file_path, "rb") as f:
            s3.Object(bucket_name, object_key).put(Body=f)
    except ClientError:
        raise

def download_file_from_s3(bucket_name: str, object_key: str) -> bytes:
    """Downloads an object from an S3 bucket efficiently."""
    s3 = _s3_resource()
    obj = s3.Object(bucket_name, object_key)

    try:
        response = obj.get()
        return response["Body"].read()
    except ClientError as e:
        # Handle "file not found" errors during the get() attempt.
        error_code = (e.response or {}).get("Error", {}).get("Code", "")
        if error_code in {"404", "NoSuchKey"}:
            raise FileNotFoundError(
                f"S3 object '{object_key}' not found in bucket '{bucket_name}'"
            )
        raise