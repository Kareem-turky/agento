"""Explicit Knowledge limits (Task 035). Every Knowledge payload is bounded."""

MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 50_000  # one document version (text/plain or text/markdown)
MAX_BODY_BYTES = 200_000  # UTF-8 bytes of one body
MAX_CHUNK_CHARS = 1_200  # one retrievable chunk (and therefore one excerpt)
MAX_CHUNKS_PER_VERSION = 256
MAX_QUERY_CHARS = 256
DEFAULT_RESULTS = 5
MAX_RESULTS = 10
MAX_TOTAL_RETURNED_CHARS = 12_000  # all excerpts of one retrieval together
MAX_DOCUMENTS_LISTED = 200
MAX_OPERATING_MODEL_BYTES = 64_000  # canonical JSON of one operating-model version
MAX_EXCERPT_CHARS = MAX_CHUNK_CHARS  # one excerpt is at most one chunk
MAX_VERSIONS_LISTED = 200  # one version history page (newest first)
MAX_QUERY_TERMS = 16  # distinct query terms used for retrieval
