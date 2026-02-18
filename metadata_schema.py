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
    'explore_tag',  # Constant: content is always 'Port' string
    'collection_tag',  # Stores collection name tag, auto-derived from
    # collection root directory path
    'key_signature',  # holds key detected via Score.detect_key.
    'mode',  # holds mode populated via Score.extract_mode_from_key_signature.
    'tonic', # holds mode populated via Score.extract_tonic_from_key_signature.
    'time_signature',  # populated via Score.extract_time_signature
    'number_of_parts',  # populated via Score.count_number_of_parts
    'abc_notation',  # Populated via Score.convert_score_to_abc
    'bb_code',  # populated via Score.create_breathnach_codes
    'featured_image',  # holds AWS path to the incipit svg
    # file, as returned by Score.convert_incipit_to_svg
    'image_alt_text',  # Constant: Content is always 'Musical Notation' string
    'summary',  # Provided by ITMA: 'from [Collection name]'
    'main_textbox',  # provided by ITMA
    'soundslice_iframe',  # holds Soundslice scorehash
    'score_track_title',  # Provided by ITMA
    'score_track_mp3',  # Holds AWS path to performance mp3 file, if provided.
    'score_track_rights',  # Constant: Content is always 'In Copyright' string
    'score_track_catalog_url',  # Provided by ITMA, online catalogue link
    'score_track2_title',  # Provided by ITMA (slow recording title field)
    'score_track2_mp3',  # Holds AWS path to slow recording mp3 file
    'score_track2_rights',  # Constant: Content is always 'In Copyright' string
    'score_track2_catalog_url',  # Provided by ITMA, online catalogue link
    'video_url',  # Provided by ITMA, Youtube embed code
    'video_title',  # Provided by ITMA, catalogue title field
    'video_catalog_url',  # Provided by ITMA
    'pdf_download',  # Holds AWS path to score PDF file, as returned by
    # Score.convert_score_to_pdf()
    'midi_audio_full',  # Holds AWS path to MIDI audio file, as returned by
    # Score.write_score_to_midi
    'incipit_audio',  # Holds path to mp3 file, as returned by
    # Score.convert_incipit_to_mp3
    'musicxml'  # Holds AWS path to MusicXML file, as returned by
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
