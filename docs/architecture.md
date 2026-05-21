# Architecture

This document describes the current high-level architecture of the Intelligent Document Management Platform.

The platform is a Django-based document management and intelligent document processing application deployed on OpenShift CRC. It uses MySQL for metadata, persistent volume storage for uploaded files, Okta OIDC for authentication, Tesseract for OCR, and a switchable AI metadata provider using either Gemini or Ollama.

## Current OpenShift CRC Architecture

```mermaid
flowchart TB
    User[User Browser] --> CF[Cloudflare Tunnel / Public URL]
    CF --> Route[OpenShift Route]
    Route --> SVC[document-app Service]
    SVC --> App[Gunicorn + Django Container]

    App --> Okta[Okta OIDC Login]
    App --> MySQLSvc[MySQL Service]
    MySQLSvc --> MySQL[(MySQL Pod)]
    MySQL --> MySQLPVC[(mysql-pvc)]

    App --> MediaPVC[(docmanager-media-pvc)]
    App --> Tesseract[Tesseract OCR]

    App --> AIChoice{AI Metadata Provider}
    AIChoice --> Gemini[Gemini API]
    AIChoice --> OllamaSvc[Ollama Service]
    OllamaSvc --> Ollama[Ollama Pod]
    Ollama --> OllamaPVC[(ollama-models-pvc)]
```

## Component Responsibilities

| Component | Responsibility |
| --- | --- |
| User Browser | Accesses the web application for upload, search, preview, edit, and review actions. |
| Cloudflare Tunnel | Provides public demo access to the OpenShift CRC route. |
| OpenShift Route | Routes external HTTP traffic to the document-app service. |
| document-app Service | Exposes the Django application pod inside OpenShift. |
| Django + Gunicorn | Hosts the application logic, templates, search, upload, OCR orchestration, AI metadata flow, and role-based access. |
| MySQL Service / Pod | Stores document metadata, extracted text, sessions, audit events, and AI suggestion status. |
| mysql-pvc | Persists MySQL database files. |
| docmanager-media-pvc | Persists uploaded document files. |
| Tesseract OCR | Extracts text from image files and scanned documents. |
| Gemini API | External AI metadata provider for higher-quality suggestions. |
| Ollama Service / Pod | Local AI metadata provider for private/offline model execution. |
| ollama-models-pvc | Persists downloaded Ollama models. |
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

    App --> DB[(MySQL)]
    DB --> Metadata[Document Metadata]
    DB --> ExtractedText[Extracted Text]
    DB --> AISuggestions[AI Metadata Suggestions]
    DB --> Audit[Audit Events]
    DB --> SessionData[Session Data]

    App --> MediaPVC[(Media PVC)]
    MediaPVC --> Files[Uploaded Documents]

    App --> ModelPVC[(Ollama Model PVC)]
    ModelPVC --> Models[Local AI Models]
```

## Design Notes

- Uploaded files are kept separate from metadata.
- Metadata, extracted text, AI suggestion status, and audit history are stored in MySQL.
- AI suggestions are staged separately from official metadata until accepted by a Loader or Admin user.
- Gemini is useful when external API processing is acceptable.
- Ollama is useful when local/private processing is preferred.
- The application is intentionally structured to support future enhancements such as semantic search, RAG, background jobs, document versioning, and workflow approvals.
