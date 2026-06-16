# MCP Document Indexing Contract

This document defines the planned MCP tool contract for document ingestion,
chunking, embedding, and OpenSearch indexing. It is the write-time companion to
the existing [MCP Document Retrieval Contract](mcp-retrieval-contract.md).

The current application already supports this direct pipeline:

```text
document upload/import
  -> text extraction/OCR
  -> paragraph-aware chunking
  -> AWS Bedrock Titan embeddings
  -> PostgreSQL DocumentChunk rows
  -> OpenSearch document and chunk indexes
```

The MCP indexing path should wrap that same behavior behind a stable backend
tool boundary without changing the first user-facing upload workflow.

## Design Goals

- Keep PostgreSQL `Document` as the canonical source of document metadata,
  lifecycle state, file references, extracted text, and audit history.
- Keep `DocumentChunk` rows as the durable rebuild/debug source until a later
  storage-ownership decision changes that explicitly.
- Keep OpenSearch as a derived search and vector retrieval index that can be
  rebuilt from PostgreSQL.
- Reuse the existing chunking, embedding, and indexing behavior first; do not
  redesign chunking in the same step.
- Make repeated indexing calls safe for the same document and content version.
- Return structured counts, warnings, and errors that operators can act on.
- Avoid exposing raw document text, secrets, credentials, or full chunks in logs.

## Tool Summary

| Field | Value |
| --- | --- |
| Tool name | `index_document` |
| Version | `1.0` |
| Purpose | Chunk, embed, and index one canonical document |
| Caller | Django backend service or future indexing worker |
| User-facing change | None for Phase 1 and first implementation |
| Canonical document source | PostgreSQL `Document` |
| Chunk storage | PostgreSQL `DocumentChunk` |
| Retrieval index | OpenSearch document and chunk indexes |
| Embedding provider | AWS Bedrock Titan Text Embeddings V2 |
| Generation provider | Out of scope |

## Request Contract

The backend calls `index_document` after a document has a canonical
`Document.id` and extracted text. The first implementation may hydrate the
document by `document_id` and ignore duplicated metadata fields, but the request
contract includes them so a future out-of-process MCP service has enough
context to validate intent and trace work.

```json
{
  "document": {
    "document_id": 42,
    "content_version": "sha256:optional-content-hash",
    "file_name": "documents/company_health_policy.pdf",
    "extracted_text": "Full extracted text or OCR output...",
    "metadata": {
      "document_type": "Policy",
      "document_subtype": "Benefits",
      "department": "HR",
      "author": "People Operations",
      "tags": ["benefits", "health"],
      "uploaded_at": "2026-06-01T14:30:00Z"
    }
  },
  "options": {
    "chunking_strategy": "paragraph",
    "chunking_version": "paragraph-v1",
    "max_chunk_chars": 2500,
    "chunk_overlap_chars": 0,
    "embedding_model": "amazon.titan-embed-text-v2:0",
    "embedding_dimensions": 1024,
    "index_document_metadata": true,
    "index_chunks": true,
    "replace_existing_chunks": true,
    "dry_run": false
  },
  "trace": {
    "request_id": "7f8e8a2c-0c30-4db5-b037-2b8f6b0e1a10",
    "source": "upload",
    "actor": "django-backend"
  }
}
```

### Request Fields

| Field | Required | Description |
| --- | --- | --- |
| `document.document_id` | Yes | Canonical PostgreSQL `Document.id`. |
| `document.content_version` | No | Stable content hash or version id used for idempotency and stale-work checks. |
| `document.file_name` | No | File name used for operator diagnostics. PostgreSQL remains authoritative. |
| `document.extracted_text` | No for in-process, yes for out-of-process | Extracted text to chunk. The first in-process implementation may hydrate this from PostgreSQL. |
| `document.metadata` | No | Metadata snapshot for indexing and trace validation. PostgreSQL remains authoritative. |
| `options` | No | Indexing controls. Defaults should match current Django settings and existing behavior. |
| `trace` | No | Non-sensitive request metadata for logs and rollout diagnostics. |

### Option Defaults

| Option | Default source | Description |
| --- | --- | --- |
| `chunking_strategy` | `paragraph` | Existing paragraph-aware chunking behavior. |
| `chunking_version` | `paragraph-v1` | Version marker for future rebuild decisions. |
| `max_chunk_chars` | `AI_EMBEDDING_MAX_CHARS` | Maximum source characters per chunk. |
| `chunk_overlap_chars` | `0` | First implementation keeps existing no-overlap behavior. |
| `embedding_model` | `BEDROCK_EMBED_MODEL_ID` | Titan embedding model used for chunks. |
| `embedding_dimensions` | `AI_EMBEDDING_DIMENSIONS` | Expected vector length. |
| `index_document_metadata` | `true` | Whether to update the OpenSearch document index. |
| `index_chunks` | `true` | Whether to update the OpenSearch chunk vector index. |
| `replace_existing_chunks` | `true` | Whether to replace existing chunks for this document/version. |
| `dry_run` | `false` | Validate and report planned work without writing chunks or indexes. |

## Response Contract

Successful responses use `status: "ok"`. A successful response may include
warnings, especially for dry runs, empty extracted text, or skipped optional
indexing work.

```json
{
  "status": "ok",
  "document_id": 42,
  "content_version": "sha256:optional-content-hash",
  "summary": {
    "chunks_created": 8,
    "chunks_replaced": 8,
    "chunks_embedded": 8,
    "document_records_indexed": 1,
    "chunk_records_indexed": 8,
    "dry_run": false,
    "idempotent_replay": false
  },
  "indexing": {
    "document_index": "docmanager-documents",
    "chunk_index": "docmanager-document-chunks",
    "embedding_model": "amazon.titan-embed-text-v2:0",
    "embedding_dimensions": 1024,
    "chunking_strategy": "paragraph",
    "chunking_version": "paragraph-v1"
  },
  "warnings": [],
  "trace": {
    "request_id": "7f8e8a2c-0c30-4db5-b037-2b8f6b0e1a10"
  }
}
```

### Response Fields

| Field | Required | Description |
| --- | --- | --- |
| `status` | Yes | `ok` or `error`. |
| `document_id` | Yes | Canonical PostgreSQL document id. |
| `content_version` | No | Version/hash processed when available. |
| `summary.chunks_created` | Yes | Number of active chunks created or retained. |
| `summary.chunks_replaced` | Yes | Number of previous chunks replaced for this document. |
| `summary.chunks_embedded` | Yes | Number of chunks with valid embeddings. |
| `summary.document_records_indexed` | Yes | Number of document metadata records written to OpenSearch. |
| `summary.chunk_records_indexed` | Yes | Number of chunk records written to OpenSearch. |
| `summary.dry_run` | Yes | Whether no writes were performed. |
| `summary.idempotent_replay` | Yes | Whether the request matched already-indexed content and did no material work. |
| `indexing` | Yes | Operational metadata about indexes, model, dimensions, and chunking. |
| `warnings` | Yes | Non-fatal conditions safe to show to operators. |
| `trace` | Yes | Request trace data without secrets or document text. |

## Error Contract

Errors use `status: "error"` and must not include raw document text, chunk text,
AWS credentials, Okta tokens, file contents, or secrets.

```json
{
  "status": "error",
  "document_id": 42,
  "error": {
    "code": "embedding_provider_error",
    "message": "The embedding provider is temporarily unavailable.",
    "retryable": true
  },
  "summary": {
    "chunks_created": 0,
    "chunks_embedded": 0,
    "chunk_records_indexed": 0
  },
  "warnings": [],
  "trace": {
    "request_id": "7f8e8a2c-0c30-4db5-b037-2b8f6b0e1a10"
  }
}
```

| Code | Retryable | Typical Cause |
| --- | --- | --- |
| `invalid_request` | No | Missing `document_id`, unsupported options, or invalid request shape. |
| `document_not_found` | No | PostgreSQL document no longer exists. |
| `no_extracted_text` | No | Document has no usable extracted text to chunk. |
| `chunking_error` | No | Text could not be chunked with the requested strategy. |
| `embedding_provider_error` | Yes | Titan embedding call failed, throttled, or returned invalid dimensions. |
| `indexing_unavailable` | Yes | OpenSearch is unreachable or rejected writes temporarily. |
| `indexing_timeout` | Yes | OpenSearch indexing exceeded timeout. |
| `stale_content_version` | No | Request version does not match current document content. |
| `permission_denied` | No | Caller is not allowed to index this document. |
| `internal_error` | Yes | Unexpected failure after sanitization. |

## Idempotency Rules

The first implementation should be safe to retry from upload, bulk import,
manual reprocessing, or a future background worker.

1. The preferred idempotency key is `(document_id, content_version,
   chunking_version, embedding_model)`.
2. If `content_version` is not available, the tool should behave like the
   existing rebuild path: delete/replace active chunks for the document and
   rewrite OpenSearch records.
3. Replaying the same idempotency key should either no-op with
   `idempotent_replay: true` or replace the same logical records without
   duplicates.
4. OpenSearch document ids for chunks should remain deterministic, using the
   existing pattern based on document id and chunk id.
5. If a newer content version exists, stale requests must fail with
   `stale_content_version` rather than overwriting current chunks.
6. Dry runs must not create, delete, or index records.

## Security And Permission Rules

The MCP indexing boundary must follow these rules:

1. PostgreSQL remains the authority for whether a document exists and may be
   indexed.
2. The first backend-only implementation may trust Django service calls. A
   future exposed MCP service must require explicit service authentication.
3. User-facing roles do not directly call this tool from the frontend.
4. Logs may include document id, counts, timings, error codes, indexes, model
   ids, content version hashes, and request ids.
5. Logs must not include full extracted text, chunk text, raw file contents,
   AWS secrets, Okta tokens, API keys, or session cookies.
6. The tool must not generate summaries, answers, or metadata suggestions.

## Backend Compatibility

The first implementation should wrap existing code rather than creating a new
indexing stack:

| Existing Behavior | MCP Indexing Responsibility |
| --- | --- |
| `documents.embeddings.chunk_text()` | Preserve paragraph-aware chunking semantics. |
| `documents.embeddings.rebuild_document_embeddings()` | Reuse or split into smaller functions for chunk creation and embedding. |
| `documents.opensearch_indexing.reindex_document()` | Reuse for document and chunk index writes. |
| `bulk_import_documents --rebuild-embeddings --reindex-opensearch` | May call the MCP wrapper after validation, but must keep current behavior until then. |
| Upload-time embedding/indexing hooks | May call the MCP wrapper after validation, but must not change the user upload flow first. |

## Rollout Plan

Phase 1 is this contract and documentation. No runtime behavior changes.

Phase 2 should add an in-process wrapper such as `documents.mcp_indexing` with
tests for successful indexing, empty extracted text, embedding failures,
OpenSearch failures, dry runs, and idempotent replay.

Phase 3 can route one operator-only management command through the wrapper, for
example a new `health_mcp_indexing` or a document-id-specific smoke command.

Phase 4 can optionally route upload/import/reprocessing paths through the MCP
wrapper behind a feature flag after parity is validated.

## Validation Plan For Phase 2

- Unit test request validation and error mapping.
- Unit test dry-run behavior with no database or OpenSearch writes.
- Unit test successful indexing counts and response shape.
- Unit test embedding provider errors and OpenSearch indexing errors.
- Compare direct `rebuild_embeddings` plus `reindex_opensearch` output with
  MCP indexing output for the same document.
- Validate from OpenShift with one known document id before using bulk imports.

## Open Questions

- Should `content_version` be added as a model field or computed on demand from
  extracted text and file metadata?
- Should chunking settings become explicit runtime settings, such as
  `AI_CHUNKING_VERSION` and `AI_CHUNK_OVERLAP_CHARS`?
- Should the first wrapper call existing rebuild functions directly, or should
  those functions be refactored into smaller chunk/create/embed/index steps?
- Should indexing remain synchronous for single documents while bulk indexing
  moves to a background worker from issue #9?
