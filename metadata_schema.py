"""metadata_schema.py holds the Port metadata schema, which is used to
validate and enforce field names in input & output metadata CSVs."""

# Port metadata schema
METADATA_FIELDS = (
    'slug',  # Unique identifier field. Provided by ITMA.
    'title',  # provided by ITMA
    'federated_search_term',  # Duplicate content from 'Title' field in
    # this field.
    'alternative_title',  # Provided by ITMA
    'composer',  # provided by ITMA
    'tune_type',  # Provided by ITMA.
    'related_entries',  # Provided by ITMA
    'explore_tag',  # May not be included?
    'collection_tag',  # TODO: Populate. Content is always 'Port' string
    'key_signature',  # TODO: populate from Score.extract_key_signature.
    # Output format is subject to change
    'mode',  # TODO: populate from Score.extract_mode_from_key_signature.
    # Output format is subject to change
    'tonic',
    # TODO: populate from Score.extract_tonic_from_key_signature.
    # Output format is subject to change
    'time_signature',  # TODO: Populate from Score.extract_time_signature
    'number_of_parts',  # TODO: Populate from Score.count_number_of_parts
    'abc_notation',  # TODO: Populate from Score.convert_score_to_abc
    # output stored in Score.abc attribute.
    'bb_code',  # TODO: populate from Score.create_breathnach_codes
    # Output format is subject to change
    'featured_image',  # TODO: Populate with AWS path to the incipit svg
    # file, as returned by Score.convert_incipit_to_svg
    'image_alt_text',  # TODO: Populate. Content is always
    # 'Musical Notation' string
    'summary',  # Provided by ITMA: 'from [Collection name]'
    'main_textbox',  # provided by ITMA
    'soundslice_iframe',  # TODO: populate embed link for Soundslice iframe
    # Note: first we need to add Soundslice integration to the Score class
    'score_track_title',  # Provided by ITMA (catalogue title field)
    'score_track_mp3',  # Provided by ITMA. AWS path to performance mp3
    # file, if provided.
    'score_track_rights',  # TODO: Populate. Content is always
    # 'In Copyright' string
    'score_track_catalog_url',  # Provided by ITMA, online catalogue link
    'score_track2_title',  # Provided by ITMA (slow recording title field)
    'score_track2_mp3',  # # Provided by ITMA (path to slow recording mp3
    # file on AWS)
    'score_track2_rights',  # TODO: Populate. Content is always
    # 'In Copyright' string
    'score_track2_catalog_url',  # Provided by ITMA, online catalogue link
    'video_url',  # Provided by ITMA, Youtube embed code
    'video_title',  # Provided by ITMA (catalogue title field)
    'video_catalog_url',  # Provided by ITMA
    'pdf_download',  # TODO: Populate with AWS path to score PDF file,
    # as returned by Score.convert_score_to_pdf()
    'midi_audio_full',  # TODO: populate with path to MIDI audio file,
    # as returned by Score.write_score_to_midi
    'incipit_audio',  # TODO: populate with path to mp3 file, , as returned by
    # Score.convert_incipit_to_mp3
    'musicxml'  # TODO: AWS path to MusicXML file, as returned by
    # Score.copy_musicxml_file_to_aws
)

PRESERVE_FIELDS = {
    # we do not edit or overwrite content in these fields
    "slug",
    "alternative_title",
    "composer",
    "summary",
    "main_textbox",
    "score_track_title",
    "score_track_catalog_url",
    "score_track2_title",
    "score_track2_catalog_url",
    "video_url",
    "video_title",
    "video_catalog_url",
    "related_entries",
    "tune_type"
}

OVERWRITE_FIELDS = {
    # pipeline-enforced constants
    "image_alt_text",
    "collection_tag",
    "explore_tag",
    "score_track_rights",
    "score_track2_rights",
    # editable but only under strictly-enforced conditions (only if empty)
    "title",
    "federated_search_term",
    # pipeline-generated assets / embeds
    "featured_image",
    "pdf_download",
    "soundslice_iframe",
    "midi_audio_full",
    "incipit_audio",
    "musicxml",
    "score_track_mp3",
    "score_track2_mp3",

    # pipeline-derived analysis / representations
    "key_signature",
    "mode",
    "tonic",
    "time_signature",
    "number_of_parts",
    "abc_notation",
    "bb_code"
}

CONSTANTS = {
    # these fields simply hold constant values, which are auto-populated via
    # our pipeline
    "image_alt_text": "Musical Notation",
    "explore_tag": "Port",
    "score_track_rights": "In Copyright",
    "score_track2_rights": "In Copyright",
}

# confirm the sets above have no overlap
assert (
        set(METADATA_FIELDS) == set(CONSTANTS) | PRESERVE_FIELDS |
        OVERWRITE_FIELDS
)
