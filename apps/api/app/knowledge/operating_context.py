"""Versioned persistence semantics of the EXISTING ``CompanyOperatingModel`` (Task 035).

The canonical structured operating context stays ``app.company.operating_model``; it is
reused, never duplicated. This module only adds:

* trusted publication: the caller supplies the configuration WITHOUT ``company_id`` or
  ``version``; the company comes from the trusted actor (its canonical UUID string, else
  fail closed) and the version is assigned by the repository;
* the v1 capability rule: a model may declare only Agent capabilities installed in this
  build (``ProductAgentCatalog``); future enum values stay in the model, unpublishable.
  ``capabilities`` is company operating INTENT: it never builds, registers or enables an
  Agent (Agent Management / ``AgentConfiguration`` stays authoritative);
* canonical serialization and a SHA-256 configuration hash (identity excluded: the
  configuration never carries ``company_id`` or ``version``, which are re-attached from
  trusted sources when a version is stored);
* strict re-validation of every stored version (malformed data fails closed).
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from app.company.operating_model import CompanyOperatingModel
from app.knowledge.documents import ActorId, CompanyId, ContentHash
from app.knowledge.errors import (
    KnowledgeRepositoryError,
    KnowledgeValidationError,
    KnowledgeValidationReason,
)
from app.knowledge.limits import MAX_OPERATING_MODEL_BYTES

R = KnowledgeValidationReason
_FROZEN = ConfigDict(frozen=True, extra="forbid")
_PLACEHOLDER_VERSION = 1
_IDENTITY = {"company_id", "version"}


class OperatingModelVersion(BaseModel):
    """One immutable, re-validated operating-model version of one company."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    company_id: CompanyId
    version: int = Field(ge=1)
    model: CompanyOperatingModel
    content_hash: ContentHash
    created_by_actor_id: ActorId
    created_at: AwareDatetime


class OperatingModelVersionSummary(BaseModel):
    model_config = _FROZEN

    version: int = Field(ge=1)
    content_hash: ContentHash
    created_at: AwareDatetime
    current: bool


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)  # fmt: skip


def company_uuid(company_id: str) -> UUID:
    """The trusted company identity as the canonical UUID ``CompanyOperatingModel``
    requires (no second identity system: an invalid identity fails closed)."""
    try:
        return UUID(company_id)
    except (ValueError, TypeError, AttributeError):
        raise KnowledgeValidationError(R.COMPANY_IDENTITY_INVALID) from None


def configuration_hash(configuration: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical configuration (identity excluded): integrity and duplicate
    detection only, never authorization."""
    return hashlib.sha256(canonical_json(dict(configuration)).encode()).hexdigest()


def prepare_configuration(
    payload: object, company_id: str, installed_agents: frozenset[str]
) -> tuple[dict[str, Any], str]:
    """Validate a submitted configuration through the EXISTING ``CompanyOperatingModel``
    and return its canonical JSON form (without ``company_id`` and ``version``) and hash."""
    if not isinstance(payload, Mapping):
        raise KnowledgeValidationError(R.OPERATING_MODEL_INVALID)
    if "company_id" in payload:
        raise KnowledgeValidationError(R.COMPANY_ID_NOT_ALLOWED)
    if "version" in payload:
        raise KnowledgeValidationError(R.VERSION_NOT_ALLOWED)
    company = company_uuid(company_id)
    try:
        model = CompanyOperatingModel.model_validate(
            {**payload, "company_id": str(company), "version": _PLACEHOLDER_VERSION}
        )
    except (ValidationError, TypeError, ValueError):
        raise KnowledgeValidationError(R.OPERATING_MODEL_INVALID) from None
    if {c.value for c in model.capabilities.enabled_agents} - installed_agents:
        raise KnowledgeValidationError(R.CAPABILITY_NOT_INSTALLED)
    configuration = model.model_dump(mode="json", exclude=_IDENTITY)
    if len(canonical_json(configuration).encode()) > MAX_OPERATING_MODEL_BYTES:
        raise KnowledgeValidationError(R.OPERATING_MODEL_TOO_LARGE)
    return configuration, configuration_hash(configuration)


def stored_document(
    configuration: Mapping[str, Any], company_id: str, version: int
) -> dict[str, Any]:
    """The persisted JSON of one version: the full model, with the trusted company (as its
    canonical UUID) and the assigned version re-attached."""
    if _IDENTITY & set(configuration):
        raise ValueError("a configuration never carries identity")
    return {**configuration, "company_id": str(company_uuid(company_id)), "version": version}


def rebuild_version(row: Mapping[str, Any]) -> OperatingModelVersion:
    """Strictly re-validate a stored version (corrupt or inconsistent data fails closed)."""
    try:
        document = row["model"]
        model = CompanyOperatingModel.model_validate(document)
        configuration = model.model_dump(mode="json", exclude=_IDENTITY)
        if (
            model.version != row["version"]
            or model.company_id != UUID(str(row["company_id"]))
            or configuration_hash(configuration) != row["content_hash"]
        ):
            raise ValueError("inconsistent operating model version")
        return OperatingModelVersion(
            company_id=row["company_id"], version=row["version"], model=model,
            content_hash=row["content_hash"], created_by_actor_id=row["created_by_actor_id"],
            created_at=row["created_at"],
        )  # fmt: skip
    except (ValidationError, TypeError, ValueError, KeyError):
        raise KnowledgeRepositoryError() from None
