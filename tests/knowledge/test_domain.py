"""Knowledge domain (Task 035): document validation, deterministic chunking, operating
model publication rules, query bounds and the context bundle contract. Pure; no I/O."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.company.operating_model import CompanyOperatingModel
from app.knowledge.chunking import chunk_text
from app.knowledge.context import (
    PRECEDENCE,
    CompanyContextBundle,
    ContextAuthority,
    KnowledgeContextReader,
    StructuredContext,
    TrustClassification,
    query_terms,
    validate_query,
)
from app.knowledge.documents import (
    ContentType,
    KnowledgeCategory,
    content_hash,
    validate_body,
    validate_category,
    validate_content,
    validate_content_type,
    validate_title,
)
from app.knowledge.errors import (
    KnowledgeRepositoryError,
    KnowledgeValidationError,
)
from app.knowledge.errors import (
    KnowledgeValidationReason as R,
)
from app.knowledge.limits import (
    MAX_BODY_BYTES,
    MAX_BODY_CHARS,
    MAX_CHUNK_CHARS,
    MAX_CHUNKS_PER_VERSION,
    MAX_QUERY_CHARS,
    MAX_QUERY_TERMS,
    MAX_RESULTS,
)
from app.knowledge.operating_context import (
    company_uuid,
    configuration_hash,
    prepare_configuration,
    rebuild_version,
    stored_document,
)
from app.knowledge.reader import RepositoryKnowledgeContextReader
from tests.company.factories import operating_model_data

COMPANY = "00000000-0000-4000-8000-00000000c0a1"
INSTALLED = frozenset({"operations"})
NOW = datetime(2031, 1, 1, tzinfo=UTC)


def configuration(**overrides):
    data = operating_model_data(**overrides)
    data.pop("company_id")
    data.pop("version")
    return data


def reason(call) -> R:
    with pytest.raises(KnowledgeValidationError) as info:
        call()
    return info.value.reason


# ----- documents ----------------------------------------------------------------------------


def test_categories_lifecycle_and_content_types_are_fixed() -> None:
    assert [c.value for c in KnowledgeCategory] == [
        "sop", "policy", "pricing", "returns", "shipping", "supplier", "general"]  # fmt: skip
    assert [c.value for c in ContentType] == ["text/plain", "text/markdown"]
    for unsupported in ("application/pdf", "text/html", "image/png", "TEXT/PLAIN", "", None):
        assert reason(lambda u=unsupported: validate_content_type(u)) is R.CONTENT_TYPE_UNSUPPORTED
    assert reason(lambda: validate_category("secrets")) is R.CATEGORY_UNSUPPORTED


def test_title_and_body_validation() -> None:
    assert validate_title("  Returns   policy \n v2 ") == "Returns policy v2"
    for bad in ("", "   ", "x" * 201, 5, None, "bad\x07title"):
        assert reason(lambda b=bad: validate_title(b)) is R.TITLE_INVALID
    assert validate_body("a\r\nb\rc") == "a\nb\nc"
    for bad in ("", " \n\t ", "nul\x00byte", "bell\x07", 3):
        assert reason(lambda b=bad: validate_body(b)) is R.BODY_INVALID
    assert reason(lambda: validate_body("x" * (MAX_BODY_CHARS + 1))) is R.BODY_TOO_LARGE
    # The byte bound (4 bytes per character at most) is a second, never-looser guard.
    assert len(validate_body("\U0001f600" * MAX_BODY_CHARS).encode()) == MAX_BODY_BYTES


def test_content_hash_is_deterministic_and_content_sensitive() -> None:
    a = validate_content("T", "text/plain", "body")
    assert a.content_hash == content_hash("T", "text/plain", "body")
    assert len(a.content_hash) == 64
    assert a.content_hash != validate_content("T", "text/markdown", "body").content_hash
    assert a.content_hash != validate_content("T2", "text/plain", "body").content_hash


# ----- chunking ------------------------------------------------------------------------------


def test_chunking_is_deterministic_bounded_and_offset_exact() -> None:
    body = "\n\n".join([f"# Section {i}\n\n" + ("word " * 150).strip() for i in range(12)])
    first, second = chunk_text(body), chunk_text(body)
    assert first == second and len(first) > 1
    assert [c.index for c in first] == list(range(len(first)))
    for chunk in first:
        assert 0 < len(chunk.content) <= MAX_CHUNK_CHARS
        assert 0 <= chunk.start < chunk.end <= len(body)
        assert body[chunk.start:chunk.start + 9] in chunk.content  # fmt: skip
    # Every word of the body is in exactly the chunk order of the body.
    assert " ".join(" ".join(c.content.split()) for c in first).split() == body.split()


def test_headings_start_sections_and_long_runs_are_cut() -> None:
    chunks = chunk_text("intro " * 100 + "\n\n# Heading\n\nsection text")
    assert chunks[-1].content.startswith("# Heading")
    run = "x" * (MAX_CHUNK_CHARS * 2 + 5)
    assert [len(c.content) for c in chunk_text(run)] == [MAX_CHUNK_CHARS, MAX_CHUNK_CHARS, 5]


def test_chunk_count_is_bounded() -> None:
    body = "\n\n".join("y" * MAX_CHUNK_CHARS for _ in range(MAX_CHUNKS_PER_VERSION + 1))
    assert reason(lambda: chunk_text(body)) is R.TOO_MANY_CHUNKS


# ----- operating model -----------------------------------------------------------------------


def test_publication_reuses_company_operating_model_and_trusted_identity() -> None:
    prepared, digest = prepare_configuration(configuration(), COMPANY, INSTALLED)
    assert "version" not in prepared and "company_id" not in prepared
    assert digest == configuration_hash(prepared)
    model = CompanyOperatingModel.model_validate(stored_document(prepared, COMPANY, 3))
    assert model.version == 3 and model.company_id == UUID(COMPANY)
    # Canonical: equivalent input yields the same hash.
    shuffled = dict(reversed(list(configuration().items())))
    assert prepare_configuration(shuffled, COMPANY, INSTALLED)[1] == digest


def test_company_id_and_version_in_input_are_refused() -> None:
    assert (
        reason(
            lambda: prepare_configuration(
                {**configuration(), "company_id": COMPANY}, COMPANY, INSTALLED
            )
        )
        is R.COMPANY_ID_NOT_ALLOWED
    )
    assert (
        reason(lambda: prepare_configuration({**configuration(), "version": 7}, COMPANY, INSTALLED))
        is R.VERSION_NOT_ALLOWED
    )


def test_invalid_trusted_identity_fails_closed() -> None:
    for bad in ("test-deployment-company", "", "not-a-uuid"):
        assert reason(lambda b=bad: company_uuid(b)) is R.COMPANY_IDENTITY_INVALID
        assert reason(lambda b=bad: prepare_configuration(configuration(), b, INSTALLED)) is (
            R.COMPANY_IDENTITY_INVALID
        )


def test_invalid_models_and_payloads_are_refused() -> None:
    for bad in ([], "x", None, {**configuration(), "unknown": 1},
                {**configuration(), "reporting": {}}):  # fmt: skip
        assert reason(lambda b=bad: prepare_configuration(b, COMPANY, INSTALLED)) is (
            R.OPERATING_MODEL_INVALID
        )


def test_capabilities_must_be_installed_in_this_build() -> None:
    for agents in (["finance"], ["operations", "marketing"], ["growth"]):
        payload = configuration(capabilities={"enabled_agents": agents})
        assert reason(lambda p=payload: prepare_configuration(p, COMPANY, INSTALLED)) is (
            R.CAPABILITY_NOT_INSTALLED
        )
    # Operating intent only: an empty list is valid and never disables an Agent.
    prepare_configuration(configuration(capabilities={"enabled_agents": []}), COMPANY, INSTALLED)


def test_operating_model_size_is_bounded() -> None:
    escalations = [
        {"id": f"e{i}", "name": "n" * 100, "severity": "warning", "condition_key": "order.late",
         "threshold": {"kind": "count", "value": 5}}
        for i in range(400)
    ]  # fmt: skip
    payload = configuration(escalations=escalations)
    assert reason(lambda: prepare_configuration(payload, COMPANY, INSTALLED)) in (
        R.OPERATING_MODEL_TOO_LARGE,
        R.OPERATING_MODEL_INVALID,
    )


def _row(**overrides):
    prepared, digest = prepare_configuration(configuration(), COMPANY, INSTALLED)
    row = {"company_id": COMPANY, "version": 2, "model": stored_document(prepared, COMPANY, 2),
           "content_hash": digest, "created_by_actor_id": "op", "created_at": NOW}  # fmt: skip
    row.update(overrides)
    return row


def test_stored_versions_are_strictly_revalidated() -> None:
    assert rebuild_version(_row()).model.version == 2
    good = _row()
    corrupt = [
        {"version": 3},  # pointer / row version mismatch
        {"content_hash": "0" * 64},  # tampered content
        {"company_id": "00000000-0000-4000-8000-00000000c0b2"},  # foreign company
        {"model": {**good["model"], "reporting": {}}},  # no longer a valid model
        {"model": {**good["model"], "extra": True}},
        {"model": "not-json-object"},
    ]
    for override in corrupt:
        with pytest.raises(KnowledgeRepositoryError) as info:
            rebuild_version(_row(**override))
        assert str(info.value) == "knowledge state unavailable"


# ----- query and context contract -------------------------------------------------------------


def test_query_terms_are_bounded_word_tokens() -> None:
    assert query_terms("Return  window? 'return' & | ! :* (refund)") == (
        "return",
        "window",
        "refund",
    )
    many = " ".join(f"t{i}" for i in range(50))
    assert len(query_terms(many)) == MAX_QUERY_TERMS
    for operator in ("'", "|", "&", "!", ":", "(", ")", "\\", ";"):
        assert not any(operator in term for term in query_terms(f"a{operator}b {operator}"))


def test_query_validation() -> None:
    assert validate_query("  hello   world ", 3).text == "hello world"
    for bad in ("", "   ", "x" * (MAX_QUERY_CHARS + 1), None, 5, "a\x00b"):
        assert reason(lambda b=bad: validate_query(b)) is R.QUERY_INVALID
    for bad in (0, MAX_RESULTS + 1, -1, True, "3", 2.0):
        assert reason(lambda b=bad: validate_query("ok", b)) is R.LIMIT_INVALID


def test_precedence_and_trust_are_explicit_and_fixed() -> None:
    assert [p.value for p in PRECEDENCE] == [
        "security_permissions_policy", "product_runtime_contracts",
        "structured_operating_model", "knowledge_document_references",
    ]  # fmt: skip
    bundle = CompanyContextBundle(structured=StructuredContext(available=False),
                                  retrieved_at=NOW)  # fmt: skip
    assert bundle.precedence == PRECEDENCE
    assert bundle.references_trust is TrustClassification.UNTRUSTED_REFERENCE
    assert bundle.structured.authority is ContextAuthority.STRUCTURED_OPERATING_MODEL
    assert [t.value for t in TrustClassification] == ["untrusted_reference"]


def test_reader_implements_the_contract() -> None:
    from tests.support.knowledge_fakes import InMemoryKnowledgeRepository

    repository = InMemoryKnowledgeRepository()
    assert isinstance(RepositoryKnowledgeContextReader(repository, repository),
                      KnowledgeContextReader)  # fmt: skip
