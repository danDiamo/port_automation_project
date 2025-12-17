"""Initial sandbox file for unstructured ITMA project testing & exploration"""

import boto3
import music21
import os
import pathlib
import soundsliceapi

from music21 import converter
from pathlib import Path
from soundsliceapi import Client


# # Test ITMA's legacy Soundslice API access
# # Credentials obtained from ITMA
# APPLICATION_ID = "PfEtlXawwgfUwpdKUIvjxgTXLVnFdRHR"
# PASSWORD = "w`oT6w}J*E4c@#9CaR}x$<xeJ;8k2LRW"
# client = Client(APPLICATION_ID, PASSWORD)
# # Testing connection: list number of folders available on Soundslice:
# folders_count = len(client.list_folders())
# print(f"ITMA's Soundslice endpoint contains {folders_count} folders")
# # List a subset of sample Soundslice folders:
# print('Sample folders include:')
# sample_folders = client.list_folders()[:10]
# for folder in sample_folders:
#     print(folder['name'])


# # Test ITMA's legacy AWS API access
# # Done: Install AWS CLI tools
# # Credentials obtained from ITMA
# # Done: Test credentials
# # AWS_Access_Key_ID = 'AKIAVQFIMVNAFI7AHEJD'
# # AWS_Secret_Access_Key = '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'
# # TODO: Update credentials
# # TODO: Test AWS boto3 Python API
# Create S3 client
s3_client = boto3.client('s3')
s3_resource = boto3.resource('s3')
for bucket in s3_resource.buckets.all():
    print(bucket.name)
# List S3 buckets
# response = s3_client.list_buckets()
# print(response)
# for bucket in response['Buckets']:
#     print(f"Bucket Name: {bucket['Name']}")


# # Checking Celine's pathlib usage.
# WORKSPACE = Path(os.getcwd())
# project_name = 'test_project'
# OUTPUT_PATH = WORKSPACE / '..' / 'output' / project_name
# print(OUTPUT_PATH)

# # Test basic Music21 functionality for reading MusicXML inputps:
# # 1: Metadata extraction
# def extract_musicxml_metadata(musicxml_file_path):
#
#     try:
#         score = converter.parse(musicxml_file_path)
#
#         if score.metadata:
#             print("\nAll Metadata Properties:")
#             for prop in score.metadata.all():
#                 if prop is not None:
#                     print(f"{prop[0]}: {prop[1]}")
#
#         for element in score.recurse().getElementsByClass('Key'):
#             print(element.tonic, element.mode)
#
#
#
#         return None
#
#     except Exception as e:
#         print(f"Error processing MusicXML file: {e}")
#         return None
#
# test_dir = ('/Users/dannydiamond/itma/paddy_obrien_collection/'
#             'itma_resources/Test_data_for_Port/100684/morrison_tutor_xml')
# test_file = 'morrison_tutor_1.xml'
# test_file_path = test_dir + '/' + test_file
# extracted_data = extract_musicxml_metadata(test_file_path)
#
# # 2. Key spelling: maj / minor & roots only.
# # could do more via a (simple) custom function? Chat to James & Treasa, if time allows.
#
# # Evaluate Andy Dickson metadata toolkit -- is it compatible with MusicXML? No, ABC only.



