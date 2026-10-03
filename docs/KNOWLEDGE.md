# Product Knowledge & company operating context

Task 035 adds two kinds of company context to the Product:

- **The structured operating model.** The existing `CompanyOperatingModel`
  (`app/company/operating_model/`) gets versioned persistence. It is validated, structured
  and authoritative for the fields it defines (SLAs, escalation thresholds, KPIs,
  reporting, capability intent).
- **Knowledge documents.** These are operator-authored text documents (SOPs, policies,
  pricing, returns, shipping and supplier notes). They are versioned, chunked
  deterministically and retrievable through bounded, company-scoped full-text search. They
  are **untrusted reference data**.

Knowledge is data. It never grants a permission and never defines a tool, Workflow or
policy. It never executes anything and never reaches an Agent prompt. **No Agent consumes
Knowledge in this version.** There is no Knowledge tool, no Agent memory and no automatic
Knowledge creation.

## Precedence

Every retrieval result carries this fixed precedence, highest first:

```
security / permissions / policy
  > Product runtime contracts
  > structured CompanyOperatingModel          (authoritative for its fields)
  > Knowledge document references            (untrusted reference data)
```

A document may discuss an SLA or a refund rule. It never overrides the structured model,
and conflicting values are never merged. For example, suppose the model says
`ship_to_delivery_sla = 5 days` and a document claims "2 days". The bundle returns both,
separately, and the precedence states that the model wins
(`test_structured_model_and_conflicting_document_are_kept_separate`).

## The operating model: versioned persistence

| Table | Holds |
| --- | --- |
| `product.company_operating_model_versions` | Immutable versions (`company_id`, `version`, `model` JSON, SHA-256 `content_hash`, `created_by_actor_id`, `created_at`). Append-only, enforced by a trigger. |
| `product.company_operating_model_current` | One pointer row per company (`current_version`, `updated_at`). Its deferred foreign key points at an existing version. |

Publishing works as follows:

- **Trusted identity.** The payload is a configuration **without** `company_id` and
  `version`. The company comes from the authenticated actor. A submitted `company_id`
  is refused (`company_id_not_allowed`), and so is a submitted `version`
  (`version_not_allowed`).
- **Canonical UUID.** The trusted company id must be the canonical UUID that
  `CompanyOperatingModel` requires; otherwise publishing fails closed with
  `company_identity_invalid`. Reads still work for any company id.
- **Validation.** The payload is validated by the **existing** `CompanyOperatingModel`, so
  there is no second model.
- **Capabilities.** In v1, `capabilities.enabled_agents` may name only Agents installed in
  this build (today `operations`); anything else is refused with
  `capability_not_installed`. The `AgentCapability` enum is unchanged. Capabilities are
  operating **intent**: they never build, register, enable or disable an Agent. Agent
  Management (`AgentConfiguration`) stays authoritative.
- **Atomic version assignment.** The repository assigns the version atomically. In one
  transaction it upserts and increments the pointer row, which locks it. It then compares
  the new hash with the previous version and inserts the immutable version, with the model
  JSON carrying the trusted company and the assigned version. Concurrent publishers are
  serialized on the pointer row, so versions are never duplicated.
- **Duplicates.** A configuration identical to the current version is refused with
  `duplicate_content`, and nothing is written.
- **Size and hash.** The canonical configuration is limited to 64 KB. The hash is a
  SHA-256 of the canonical JSON with identity excluded; it is used for integrity and
  duplicate detection only.
- **Re-validation on read.** Every stored version is re-validated through
  `CompanyOperatingModel` on read, and the stored version, company and hash must match.
  Malformed data fails closed (HTTP 503).

The console only **displays** the model and its history. Publishing is API-first:
`POST /api/v1/knowledge/operating-model/publish` with `{"configuration": {...}}`. There
is no JSON editor in the UI, and the BFF does not proxy the publish route.

## Knowledge documents

| Table | Holds |
| --- | --- |
| `product.knowledge_documents` | Identity and lifecycle: `document_id`, `company_id`, `category`, `lifecycle` (`active`/`archived`), `current_version`, timestamps |
| `product.knowledge_document_versions` | Immutable snapshots: `title`, `content_type`, `body`, SHA-256 `content_hash`, `created_by_actor_id`, `created_at` (append-only trigger) |
| `product.knowledge_chunks` | Immutable chunks of every version, with offsets, a hash and a stored generated `tsvector` (GIN index; append-only trigger) |

Documents follow these rules:

- **Categories** are `sop`, `policy`, `pricing`, `returns`, `shipping`, `supplier` and
  `general`.
- **Content types** are only `text/plain` and `text/markdown`. Markdown is stored and
  returned as text; it is never parsed or rendered as HTML.
- **Not supported:** PDF, DOCX, OCR, HTML, URLs, connectors and file upload.
- **Limits** (`app/knowledge/limits.py`):
  - title: 200 characters;
  - body: 50,000 characters or 200,000 UTF-8 bytes;
  - at most 256 chunks of up to 1,200 characters each;
  - control characters, including NUL, are refused;
  - line endings are normalized.
- **Lifecycle:**
  - Create makes version 1.
  - Publish-version appends the next immutable version of an **active** document.
  - Archive excludes the document from retrieval and keeps its history.
  - There is **no delete**. An archived document accepts no new versions
    (`document_archived`).
- **Duplicate content (chosen behavior):**
  - A version identical to the **current** version is refused (`duplicate_content`).
  - Identical text in two different documents is allowed, because they are separate
    documents.
  - Reverting to an older version's text is allowed and becomes a new version.
- **Concurrency:** publishing locks the document row with
  `UPDATE … SET current_version = current_version + 1 … RETURNING`. Concurrent writers
  therefore get distinct consecutive versions; this is proven with six parallel writers on
  PostgreSQL.
- **Secrets:** never store secrets, credentials or API keys in Knowledge. The UI says so;
  nothing in Knowledge reads or stores secrets.

## Chunking and retrieval

**Chunking** (`app/knowledge/chunking.py`) is deterministic and uses no tokenizer and no
model:

1. Paragraphs split on blank lines, and Markdown heading lines form their own blocks.
2. Oversized blocks are cut at the last whitespace, or hard-cut when there is none.
3. Blocks are packed in order into chunks of at most 1,200 characters.
4. A heading starts a new chunk once the current one is more than half full.

The same body always yields the same chunks.

**Retrieval** v1 is PostgreSQL full-text search with the language-neutral **`simple`**
configuration, used for both the stored generated `tsvector` and the query `tsquery`.

- **No language-specific processing.** `simple` lowercases words and matches exact terms.
  It intentionally does no stemming, uses no stop-word list and detects no language, so
  Product Core never assumes English. Arabic, English, mixed Arabic/English and other
  languages work the same way.
- **What that means in practice:**
  - `refund` finds "refund policy", but `refunds` does not match `refund`.
  - Arabic prefixes are not stripped: `الشحن` does not match `والشحن`.
  - Common words such as `our` or `the` are ordinary terms.
  - Arabic diacritics are not normalized. Unvocalized text is the expected form.
- **Future retrieval.** Language-specific or semantic retrieval (stemming, embeddings) may
  be added later as an optional enhancement behind the same `KnowledgeContextReader`
  contract, without changing callers. This version adds no language detection,
  per-document language setting, tokenizer or NLP dependency.
- **Tests.** Real PostgreSQL tests prove English exact, Arabic and mixed-language retrieval
  (`test_language_neutral_retrieval_english_arabic_and_mixed`).

Query safety and scoping:

- The query is reduced to at most 16 distinct word terms. Terms contain only word
  characters, so no full-text operator, quote or SQL can survive, and the terms are OR-ed
  into **one bound parameter**.
- The SQL is scoped by the trusted company and covers only the **current version** of
  **active** documents. Archived documents, old versions and other companies never match.
- Results are ordered by `ts_rank_cd` descending, then `document_id`, then `chunk_index`,
  which is deterministic.
- Output is bounded: at most 10 results (default 5), each excerpt at most one chunk, and
  12,000 characters in total.
- There is no embedding provider, no vector-only design and no new dependency.

## Context contract

`app/knowledge/context.py` is runtime-, provider- and persistence-independent. It defines:

- `KnowledgeReference`: a document id and version, category, title, chunk index, a
  bounded excerpt, the relevance, and `trust = "untrusted_reference"`.
- `CompanyContextBundle`: the structured context (the current `OperatingModelVersion`, or
  none), the references, `precedence`, `references_trust` and `retrieved_at`.
- `KnowledgeContextReader.retrieve_context(company_id, query, limit)`: the stable reader
  that future trusted consumers depend on. It takes a **trusted** company id, imports no
  FastAPI or SQLAlchemy, and only returns context; it generates no answer.
  `RepositoryKnowledgeContextReader` is the Product implementation.

## Governance, audit and observability

- **Permissions:**
  - `knowledge.read` covers the operating model, documents and retrieval.
  - `knowledge.manage` covers publish, create, publish-version and archive.
  - Knowledge grants no permission.
- **Agents:** `KnowledgePermissionEvaluator` never grants `knowledge.manage` to a
  `system_agent`, even when its permissions name it. Reads are **not** globally denied to
  Agents; `knowledge.read` is evaluated normally.
- **Actions** (all COMPANY-scoped):
  - reads: `knowledge.operating_model.read`, `knowledge.documents.read` and
    `knowledge.query` (READ);
  - writes: `knowledge.operating_model.publish`, `knowledge.document.create`,
    `knowledge.document.publish_version` and `knowledge.document.archive`
    (LOW_RISK_WRITE).
- **Governed writes:** every write runs through `ExecutionCoordinator` (governance →
  validation → handler → persistence → verification → audit), and a denied write is also
  audited. Approval and HIGH_RISK are not used.
- **Audit:** the existing `audit_events` trail holds metadata only:
  - action, actor, company and status;
  - safe references (`operating-model:v3`, `<document_id>:v2`);
  - verification codes.

  It never holds a body, excerpt, title, query or operating-model JSON.
- **Observability:** this uses the ONE Product observability, chosen by the composition
  root and handed to the Knowledge composition. Operations are `knowledge.query` and
  `knowledge.mutation`. Labels are fixed enums only: the outcome, a mutation kind and a
  validation reason. They never contain a query, content, an id or a hash.

## API (Product authentication only; the AgentOS key is rejected)

| Method | Path | Permission |
| --- | --- | --- |
| GET | `/api/v1/knowledge/operating-model` | `knowledge.read` |
| GET | `/api/v1/knowledge/operating-model/versions` | `knowledge.read` |
| GET | `/api/v1/knowledge/operating-model/version?version=` | `knowledge.read` |
| POST | `/api/v1/knowledge/operating-model/publish` | `knowledge.manage` |
| GET | `/api/v1/knowledge/documents` | `knowledge.read` |
| GET | `/api/v1/knowledge/document?document_id=` | `knowledge.read` |
| GET | `/api/v1/knowledge/document/version?document_id=&version=` | `knowledge.read` |
| POST | `/api/v1/knowledge/document/create` | `knowledge.manage` |
| POST | `/api/v1/knowledge/document/version?document_id=` | `knowledge.manage` |
| POST | `/api/v1/knowledge/document/archive?document_id=` | `knowledge.manage` |
| POST | `/api/v1/knowledge/query` | `knowledge.read` |

The routes behave as follows:

- **Exact paths.** Paths are fixed, so the AgentOS exemption stays a list of exact
  paths, and AgentOS's own `/knowledge` routes stay AgentOS-key protected.
- **No other endpoints.** There is no delete, upload, URL-import or raw repository
  endpoint.
- **Status codes:**
  - 401 when unauthenticated, or when the AgentOS key is used;
  - 403 when a permission is missing;
  - 404 for a document of another company, which is indistinguishable from a missing one;
  - 409 when a governed write did not complete;
  - 422 with a stable `code` and no echoed input;
  - 503 when Knowledge is not configured or the stored data is unreadable or malformed.

The service is composed whenever `APP_DATABASE_URL` is set, independently of the business
backend. A company with zero Knowledge rows works: there is no model, there are no
documents, and retrieval returns no references.

## Console: Settings → Knowledge (`/settings/knowledge`)

The page offers:

- the current operating model (a summary plus the full model as read-only text) and its
  version history;
- the document list, document details and version history;
- create and publish-version forms (category, title, content type, text) and archive with
  confirmation;
- a retrieval preview labelled **Untrusted reference**.

Every title, body and excerpt is rendered inertly as plain text inside `<pre>`. There is no
`dangerouslySetInnerHTML`, no Markdown or HTML rendering and no file upload. The BFF gives
the two document-write routes a 256 KiB request cap; every other route keeps 16 KiB.

## Migration

`0006_create_knowledge_context.py` (down-revision `0005`) is the single head. The
downgrade removes only the five Knowledge tables and their trigger function. The seven
prior Product tables and their rows are preserved. Migrations `0001`–`0005` are
byte-for-byte unchanged.

## Not in this version

The following are not part of this version:

- Agent consumption of Knowledge, prompt injection of context, Knowledge tools and Agent
  memory;
- embeddings or vector search, and external ingestion (files, URLs, connectors);
- approval or high-risk handling of Knowledge changes;
- an operating-model editor in the UI.
