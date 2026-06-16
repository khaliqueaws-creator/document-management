# MCP Document Retrieval Contract

This document defines the first MCP tool contract for document retrieval. The
current Django backend implements this boundary as an in-process
`search_documents` wrapper around the OpenSearch-backed RAG retrieval path
without changing the Django UI or the `/ask/` response shape.

## Design Goals

- Keep the existing Ask Documents and AI Search user experience stable.
- Reuse the current Bedrock Titan embedding and OpenSearch chunk retrieval path.
- Keep PostgreSQL `Document` records as the final authority for metadata,
  lifecycle state, file links, and user-visible citations.
- Treat OpenSearch as a derived index populated from PostgreSQL through the MCP
  indexing boundary when `MCP_INDEXING_ENABLED=True`.
- Return retrieved context only after permission trimming has been applied.
- Keep answer generation outside this MCP tool. The tool retrieves grounded
  context; the backend still builds prompts and calls the generation provider.

## Tool Summary

| Field | Value |
| --- | --- |
| Tool name | `search_documents` |
| Version | `1.0` |
| Purpose | Retrieve relevant document chunks for semantic search and RAG context |
| Caller | Django backend service |
| User-facing change | None |
| Generation provider | Out of scope |
| Canonical document source | PostgreSQL `Document` |
| Retrieval index | OpenSearch chunk index |

## Request Contract

The Django backend calls `search_documents` with the user's query, security
context, optional metadata filters, and retrieval options.

```json
{
  "query": "When does health coverage start?",
  "user_context": {
    "user_id": "okta-00u123",
    "email": "viewer@example.com",
    "roles": ["viewer"],
    "groups": ["DjangoViewer"],
    "session_id": "optional-session-id"
  },
  "filters": {
    "document_type": "Policy",
    "document_subtype": "",
    "department": "HR",
    "author": "",
    "tags": ["benefits", "health"],
    "uploaded_from": "2026-01-01",
    "uploaded_to": "2026-12-31"
  },
  "options": {
    "max_results": 5,
    "candidate_multiplier": 3,
    "min_score": 0,
    "max_chunk_chars": 1800,
    "include_snippets": true,
    "include_citations": true,
    "include_document_metadata": true
  },
  "trace": {
    "request_id": "7f8e8a2c-0c30-4db5-b037-2b8f6b0e1a10",
    "source": "ask_documents"
  }
}
```

### Request Fields

| Field | Required | Description |
| --- | --- | --- |
| `query` | Yes | Natural-language user query. Empty or whitespace-only queries return `invalid_request`. |
| `user_context` | Yes | Authenticated user/session data used by the backend to derive accessible documents. |
| `filters` | No | Optional metadata filters. Empty strings and empty arrays are ignored. |
| `options` | No | Retrieval tuning values. Server defaults match Django settings when omitted. |
| `trace` | No | Non-sensitive request metadata for logs and rollout diagnostics. |

### Supported Filters

The first contract supports filters already represented in OpenSearch indexing
payloads:

| Filter | Type | OpenSearch field |
| --- | --- | --- |
| `document_type` | string | `document_type` |
| `document_subtype` | string | `document_subtype` |
| `department` | string | `department` |
| `author` | string | `author.raw` |
| `tags` | string array | `tags_list` |
| `ai_document_type` | string | `ai_document_type` |
| `ai_department` | string | `ai_department` |
| `ai_tags` | string array | `ai_tags_list` |
| `ai_metadata_provider` | string | `ai_metadata_provider` |
| `ai_suggestion_status` | string | `ai_suggestion_status` |
| `uploaded_from` | ISO date string | `uploaded_at` range lower bound |
| `uploaded_to` | ISO date string | `uploaded_at` range upper bound |

The implementation may start with query-only vector retrieval and add filter
application incrementally, but it must preserve these field names so callers do
not need a contract change later.

### Option Defaults

| Option | Default source | Description |
| --- | --- | --- |
| `max_results` | `AI_RAG_TOP_K` | Number of accessible chunks returned to the backend. |
| `candidate_multiplier` | `3` | Number of OpenSearch candidates retrieved before PostgreSQL hydration and trimming. |
| `min_score` | `AI_RAG_MIN_RETRIEVAL_SCORE` | Optional score floor for usable chunks. |
| `max_chunk_chars` | `AI_RAG_MAX_CONTEXT_CHARS` | Maximum snippet/excerpt characters returned per chunk. |
| `include_snippets` | `true` | Whether to include chunk text excerpts. |
| `include_citations` | `true` | Whether to include citation-ready source fields. |
| `include_document_metadata` | `true` | Whether to include user-visible document metadata. |

## Response Contract

Successful responses use `status: "ok"` even when no accessible chunks are
found. Empty retrieval is not an exception.

```json
{
  "status": "ok",
  "query": "When does health coverage start?",
  "results": [
    {
      "citation_id": 1,
      "document_id": 42,
      "chunk_id": 318,
      "chunk_index": 0,
      "score": 0.91,
      "snippet": "Coverage starts on the first day of the month after eligibility...",
      "source": {
        "document_id": 42,
        "file_name": "documents/company_health_policy.docx",
        "document_type": "Policy",
        "document_subtype": "",
        "department": "HR",
        "author": "",
        "tags": ["benefits", "health"],
        "uploaded_at": "2026-06-01T14:30:00Z",
        "open_url": "/documents/42/open/"
      },
      "retrieval": {
        "index": "docmanager-chunks",
        "embedding_model": "amazon.titan-embed-text-v2:0",
        "retrieval_mode": "vector",
        "hydrated_from_postgres": true
      }
    }
  ],
  "summary": {
    "returned_count": 1,
    "candidate_count": 15,
    "trimmed_count": 2,
    "empty_reason": ""
  },
  "warnings": [],
  "trace": {
    "request_id": "7f8e8a2c-0c30-4db5-b037-2b8f6b0e1a10"
  }
}
```

### Result Fields

| Field | Required | Description |
| --- | --- | --- |
| `citation_id` | Yes | 1-based citation number scoped to this response. |
| `document_id` | Yes | Canonical PostgreSQL `Document.id`. |
| `chunk_id` | Yes | PostgreSQL `DocumentChunk.id` when available. |
| `chunk_index` | Yes | Chunk order inside the source document. |
| `score` | Yes | OpenSearch retrieval score as a number. |
| `snippet` | Yes | Trimmed chunk text used for answer context or search preview. |
| `source` | Yes | User-visible document metadata hydrated from PostgreSQL. |
| `retrieval` | Yes | Operational retrieval metadata without sensitive document text beyond the snippet. |

## Empty Retrieval

When retrieval succeeds but no accessible context is usable, return `ok` with an
empty `results` array and a populated `summary.empty_reason`.

```json
{
  "status": "ok",
  "query": "What is the moon made of?",
  "results": [],
  "summary": {
    "returned_count": 0,
    "candidate_count": 4,
    "trimmed_count": 4,
    "empty_reason": "no_accessible_context"
  },
  "warnings": []
}
```

Supported empty reasons:

| Reason | Meaning |
| --- | --- |
| `no_query` | Query was empty after trimming. |
| `no_candidates` | OpenSearch returned no matching chunks. |
| `no_accessible_context` | Candidates existed but PostgreSQL hydration or permission trimming removed them. |
| `below_min_score` | Candidates were below the configured score floor. |
| `below_min_context` | Retrieved snippets were too short for RAG generation. |

## Error Contract

Errors use `status: "error"` and must not include secrets, raw credentials, or
large document text.

```json
{
  "status": "error",
  "error": {
    "code": "retrieval_unavailable",
    "message": "Document retrieval is temporarily unavailable.",
    "retryable": true
  },
  "results": [],
  "warnings": [],
  "trace": {
    "request_id": "7f8e8a2c-0c30-4db5-b037-2b8f6b0e1a10"
  }
}
```

| Code | Retryable | Typical cause |
| --- | --- | --- |
| `invalid_request` | No | Missing query or unsupported request shape. |
| `embedding_provider_error` | Yes | Titan embedding call failed or returned invalid dimensions. |
| `retrieval_timeout` | Yes | OpenSearch or MCP call exceeded timeout. |
| `retrieval_unavailable` | Yes | OpenSearch or MCP service is unreachable. |
| `permission_context_missing` | No | Backend did not provide enough user context for trimming. |
| `permission_denied` | No | User is authenticated but has no retrieval permission. |
| `internal_error` | Yes | Unexpected retrieval failure after sanitization. |

## Security And Permission Rules

The MCP retrieval boundary must follow these rules:

1. OpenSearch may produce candidate chunks, but candidates are not returned until
   they hydrate through PostgreSQL `Document` records.
2. The backend remains responsible for deriving the accessible document queryset
   from Okta roles/groups and any future row-level ACLs.
3. Returned `source` metadata must come from PostgreSQL or match a PostgreSQL
   hydrated document record.
4. Deleted, missing, or inaccessible documents must be silently trimmed from
   results.
5. Logs may include counts, timings, error codes, and request ids. Logs must not
   include full snippets, raw document text, AWS secrets, Okta tokens, or API
   keys.
6. The tool must not generate final answers. That prevents untrimmed or
   non-canonical context from reaching a model through an alternate path.

## Backend Compatibility

The current Django backend can adapt this response into the existing RAG shape:

```python
{
    "answer": answer,
    "citations": contexts,
    "empty": False,
}
```

Mapping rules:

| MCP field | Existing RAG context field |
| --- | --- |
| `citation_id` | `citation_id` |
| `document_id` | `document_id` |
| `chunk_id` | `chunk_id` |
| `chunk_index` | `chunk_index` |
| `snippet` | `chunk_text` |
| `score` | `score` |
| `source.file_name` and `source.open_url` | Rendered citation link/document display |

When MCP is disabled or unavailable and fallback is configured, the backend
should keep using the current direct retrieval path in `documents/rag.py`. The
UI should not know which retrieval path was used.

The validated OpenShift configuration enables MCP retrieval and disables
fallback during testing:

```yaml
MCP_RETRIEVAL_ENABLED: "True"
MCP_RETRIEVAL_FALLBACK_ENABLED: "False"
```

## Candidate Future Tools

These tools are intentionally out of scope for the first contract, but the names
are reserved as likely follow-ups:

| Tool | Purpose |
| --- | --- |
| `get_document_metadata` | Return canonical PostgreSQL metadata for one accessible document. |
| `summarize_document` | Produce or retrieve a summary for one accessible document. |
| `compare_documents` | Retrieve comparison context across two or more accessible documents. |
| `list_document_facets` | Return available metadata facets for search filters. |

Write-time indexing tools are tracked separately in
[MCP Document Indexing Contract](mcp-indexing-contract.md). Retrieval should
remain read-only; chunking, embedding, and OpenSearch writes belong to the
indexing contract.

## Implementation Notes

- The current implementation wraps the existing `retrieve_question_context()`
  behavior.
- Keep the implementation backend-only; do not call MCP directly from the
  frontend.
- Use `MCP_RETRIEVAL_FALLBACK_ENABLED=false` during validation so retrieval
  issues are visible. Use fallback only when operational continuity is more
  important than surfacing MCP-specific errors.
- Use existing settings where possible: `AI_RAG_TOP_K`,
  `AI_RAG_MAX_CONTEXT_CHARS`, `AI_RAG_MIN_RETRIEVAL_SCORE`,
  `BEDROCK_EMBED_MODEL_ID`, `OPENSEARCH_CHUNK_INDEX`, and
  `OPENSEARCH_TIMEOUT_SECONDS`.
- Add validation for successful retrieval, empty results, permission trimming,
  and OpenSearch/embedding failures.
