"""Product Knowledge & company operating context (Task 035).

Versioned persistence of the EXISTING ``CompanyOperatingModel`` (structured, validated,
authoritative for its fields) and versioned, operator-authored Knowledge documents
(text reference data, UNTRUSTED), retrievable through the bounded, company-scoped
``KnowledgeContextReader``. Knowledge is data: it never grants permissions, defines
tools, workflows or policies, executes anything, or reaches an Agent prompt (no Agent
consumes it in this version).

    errors / limits / documents / chunking / operating_context   pure domain
    context      KnowledgeReference, CompanyContextBundle, KnowledgeContextReader
    contracts    repository protocols (implemented by app.persistence.knowledge)
    actions / permissions / handlers / service   governed Product API
    reader       RepositoryKnowledgeContextReader

Depends only on the standard library, Pydantic, ``app.company``, ``app.context``,
``app.governance``, ``app.execution`` and ``app.observability.contracts``.
"""
