"""Knowledge errors: fixed messages and stable reason codes, never a submitted or stored
value."""

from enum import StrEnum


class KnowledgeValidationReason(StrEnum):
    """Stable, safe reasons a Knowledge input is refused (HTTP 422)."""

    TITLE_INVALID = "title_invalid"
    BODY_INVALID = "body_invalid"
    BODY_TOO_LARGE = "body_too_large"
    TOO_MANY_CHUNKS = "too_many_chunks"
    CONTENT_TYPE_UNSUPPORTED = "content_type_unsupported"
    CATEGORY_UNSUPPORTED = "category_unsupported"
    QUERY_INVALID = "query_invalid"
    LIMIT_INVALID = "limit_invalid"
    OPERATING_MODEL_INVALID = "operating_model_invalid"
    OPERATING_MODEL_TOO_LARGE = "operating_model_too_large"
    COMPANY_ID_NOT_ALLOWED = "company_id_not_allowed"
    VERSION_NOT_ALLOWED = "version_not_allowed"
    COMPANY_IDENTITY_INVALID = "company_identity_invalid"
    CAPABILITY_NOT_INSTALLED = "capability_not_installed"
    DUPLICATE_CONTENT = "duplicate_content"
    DOCUMENT_ARCHIVED = "document_archived"


class KnowledgeError(Exception):
    message = "knowledge operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class KnowledgeValidationError(KnowledgeError):
    message = "Invalid knowledge input"

    def __init__(self, reason: KnowledgeValidationReason) -> None:
        super().__init__()
        self.reason = KnowledgeValidationReason(reason)


class KnowledgeAccessDeniedError(KnowledgeError):
    message = "Forbidden"


class KnowledgeNotFoundError(KnowledgeError):
    message = "Knowledge document not found"


class KnowledgeVersionNotFoundError(KnowledgeError):
    message = "Version not found"


class KnowledgeOperationFailedError(KnowledgeError):
    """The governed write did not complete (nothing confirmed)."""

    message = "Knowledge operation did not complete"


class KnowledgeUnavailableError(KnowledgeError):
    message = "Knowledge unavailable"


class KnowledgeRepositoryError(Exception):
    """Durable Knowledge state could not be read or written, or is malformed (fixed
    message, never chained)."""

    def __init__(self) -> None:
        super().__init__("knowledge state unavailable")
