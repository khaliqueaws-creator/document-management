# Architecture

This document describes the current high-level architecture of the Intelligent Document Management Platform.

The platform is a Django-based document management and intelligent document processing application deployed on OpenShift CRC. It uses PostgreSQL for canonical document metadata and audit state, OpenSearch for derived keyword/vector/RAG retrieval, persistent volume storage for uploaded files, Okta OIDC for authentication, Tesseract for OCR, and AWS Bedrock for AI metadata, embeddings, and answer generation. Ask Documents retrieval runs through the backend MCP retrieval boundary defined in [MCP Document Retrieval Contract](mcp-retrieval-contract.md). Upload, reprocess, and bulk import indexing can run through the MCP indexing boundary defined in [MCP Document Indexing Contract](mcp-indexing-contract.md).

## Current OpenShift CRC Architecture

```mermaid
flowchart TB
    User[User Browser] --> CF[Cloudflare Tunnel / Public URL]
    CF --> Route[OpenShift Route]
    Route --> SVC[document-app Service]
    SVC --> App[Gunicorn + Django Container]

    App --> Okta[Okta OIDC Login]
    App --> PostgreSQLSvc[PostgreSQL Service]
    PostgreSQLSvc --> PostgreSQL[(PostgreSQL Pod)]
    PostgreSQL --> PostgreSQLPVC[(postgresql-pvc)]

    App --> RetrievalMCP[MCP Retrieval Boundary]
    App --> IndexingMCP[MCP Indexing Boundary]
    RetrievalMCP --> OpenSearchSvc[OpenSearch Service]
    RetrievalMCP --> PostgreSQLSvc
    IndexingMCP --> OpenSearchSvc
    IndexingMCP --> PostgreSQLSvc
    OpenSearchSvc --> OpenSearch[(OpenSearch Pod)]
    OpenSearch --> OpenSearchPVC[(opensearch-pvc)]

    App --> MediaPVC[(docmanager-media-pvc)]
    App --> Tesseract[Tesseract OCR]

    App --> Bedrock[AWS Bedrock Nova Lite]
    App --> Titan[AWS Bedrock Titan Embeddings]
    Titan --> OpenSearchSvc
    App --> RAGAnswer[AWS Bedrock Nova Lite RAG Answers]
```

## Component Responsibilities

| Component | Responsibility |
| --- | --- |
| User Browser | Accesses the web application for upload, search, preview, edit, and review actions. |
| Cloudflare Tunnel | Provides public demo access to the OpenShift CRC route. |
| OpenShift Route | Routes external HTTP traffic to the document-app service. |
| document-app Service | Exposes the Django application pod inside OpenShift. |
| Django + Gunicorn | Hosts the application logic, templates, search, document Q&A, upload, OCR orchestration, AI metadata flow, and role-based access. |
| MCP Retrieval Boundary | Wraps Ask Documents retrieval, applies request validation, permission context handling, PostgreSQL hydration, and structured retrieval errors. |
| MCP Indexing Boundary | Write-time boundary for chunking, Bedrock Titan embedding, PostgreSQL `DocumentChunk` persistence, and OpenSearch indexing. |
| PostgreSQL Service / Pod | Stores canonical document metadata, extracted text, sessions, audit events, AI suggestion status, and chunk rebuild/debug data. |
| postgresql-pvc | Persists PostgreSQL database files. |
| OpenSearch Service / Pod | Stores derived document and chunk search records for keyword, vector, and RAG retrieval. |
| opensearch-pvc | Persists OpenSearch index data. |
| docmanager-media-pvc | Persists uploaded document files. |
| Tesseract OCR | Extracts text from image files and scanned documents. |
| AWS Bedrock Nova Lite | AI metadata provider and RAG answer generator accessed through boto3 and AWS credentials. |
| AWS Bedrock Titan Embeddings V2 | External embedding provider for document chunks and AI Search queries. |
| Okta OIDC | Handles authentication and provides group claims for application roles. |

## Role-Based Access Architecture

```mermaid
flowchart LR
    Okta[Okta OIDC] --> Claims[OIDC Token / Group Claims]
    Claims --> Session[Django Session]
    Session --> Permissions[documents/permissions.py]

    Permissions --> Viewer[Viewer: Search and View]
    Permissions --> Loader[Loader: Upload, OCR, Edit, AI Review]
    Permissions --> Admin[Admin: Delete and Audit Access]
```

## Data Storage Architecture

```mermaid
flowchart TB
    App[Django Application]

    App --> DB[(PostgreSQL)]
    DB --> Metadata[Document Metadata]
    DB --> ExtractedText[Extracted Text]
    DB --> AISuggestions[AI Metadata Suggestions]
    DB --> Chunks[DocumentChunk Text And Embeddings]
    DB --> Audit[Audit Events]
    DB --> SessionData[Session Data]

    App --> RetrievalMCP[MCP Retrieval Boundary]
    App --> IndexingMCP[MCP Indexing Boundary]
    RetrievalMCP --> Search[(OpenSearch)]
    RetrievalMCP --> DB
    IndexingMCP --> Search
    IndexingMCP --> DB
    Search --> SearchDocs[Derived Document Index]
    Search --> SearchChunks[Derived Chunk Vector Index]

    App --> MediaPVC[(Media PVC)]
    MediaPVC --> Files[Uploaded Documents]

    App --> Bedrock[AWS Bedrock Nova Lite]

    App --> Titan[AWS Bedrock Titan Embeddings V2]
    Titan --> Chunks
    Titan --> SearchChunks
    SearchChunks --> RetrievalMCP
    RetrievalMCP --> RAGAnswer[AWS Bedrock Nova Lite RAG Answer]
```

## Design Notes

- Uploaded files are kept separate from metadata.
- Metadata, extracted text, AI suggestion status, chunk rebuild/debug data, and audit history are stored in PostgreSQL.
- PostgreSQL `Document` records are the canonical source of truth for document metadata, lifecycle state, permissions, audit, and file locations.
- OpenSearch records are derived from PostgreSQL data and can be rebuilt with `python manage.py reindex_opensearch --create-indexes`.
- OpenSearch search hits are treated as candidate retrieval results only. Django hydrates final search results from PostgreSQL before rendering them to users.
- Ask Documents uses the MCP retrieval boundary for structured retrieval responses and error handling before prompt construction.
- Upload, scanned-document confirmation, metadata reindexing, and bulk import can use the MCP indexing boundary for write-time chunking, embedding, and OpenSearch indexing.
- Deleted or missing PostgreSQL documents are not shown even if stale OpenSearch records still exist.
- AI suggestions are staged separately from official metadata until accepted by a Loader or Admin user.
- AWS Bedrock Nova Lite generates AI metadata suggestions and grounded Ask Documents answers after MCP retrieval.
- AWS Bedrock Titan Text Embeddings V2 creates document and query vectors used by OpenSearch retrieval.
- The application is intentionally structured to support future enhancements such as hybrid retrieval, background jobs, document versioning, and workflow approvals.

## Source Of Truth Boundary

PostgreSQL owns all canonical document state. Upload, metadata edit, AI metadata accept/reject, delete, audit, and file access flows write or read PostgreSQL first. OpenSearch is updated afterward as a derived index. If OpenSearch is unavailable, the PostgreSQL document record remains valid and can be reindexed later.

The Django app is the only end-user UI. OpenSearch Dashboards is useful for operational inspection, but application users never act directly on OpenSearch records. Search and AI Search use OpenSearch for retrieval, then Django validates and hydrates results from PostgreSQL before displaying document metadata or file links. Ask Documents uses the MCP retrieval boundary over the same OpenSearch chunk index, hydrates citations from PostgreSQL, and only then builds the answer prompt.
