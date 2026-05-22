# AI Metadata Workflow

This document describes the OCR and AI-assisted metadata workflow used by the Intelligent Document Management Platform.

The platform combines OCR, document parsing, and AI-assisted metadata generation to help users organize and classify uploaded documents.

## AI Metadata Processing Flow

```mermaid
sequenceDiagram
    actor Loader
    participant Django
    participant Media as Media PVC
    participant OCR as Text Extraction / OCR
    participant AI as AI Provider
    participant DB as MySQL

    Loader->>Django: Upload document + metadata
    Django->>Media: Save uploaded file
    Django->>OCR: Extract text / OCR if needed
    OCR-->>Django: Extracted text
    Django->>AI: Send text for metadata suggestion
    AI-->>Django: Suggested type, department, tags, summary
    Django->>DB: Save metadata, extracted text, AI suggestion, audit event
    Django-->>Loader: Show edit/review page
    Loader->>Django: Accept / reject / regenerate AI suggestion
    Django->>DB: Update final metadata and status
```

## OCR Processing Architecture

```mermaid
flowchart TB
    Upload[Uploaded Document]

    Upload --> FileType{File Type}

    FileType --> PDF[PDF]
    FileType --> DOCX[DOCX]
    FileType --> TXT[TXT]
    FileType --> XLSX[XLSX]
    FileType --> IMG[Image]

    PDF --> ExtractPDF[Native PDF Text Extraction]
    ExtractPDF --> OCRCheck{Text Found?}

    OCRCheck -->|Yes| Extracted[Extracted Text]
    OCRCheck -->|No| OCRPDF[OCR Scanned PDF]

    DOCX --> ExtractDOCX[python-docx Extraction]
    TXT --> ExtractTXT[Text Reader]
    XLSX --> ExtractXLSX[openpyxl Extraction]
    IMG --> OCRIMG[Tesseract OCR]

    OCRPDF --> Extracted
    ExtractDOCX --> Extracted
    ExtractTXT --> Extracted
    ExtractXLSX --> Extracted
    OCRIMG --> Extracted
```

## AI Provider Selection

```mermaid
flowchart LR
    ExtractedText[Extracted Text] --> Provider{AI_METADATA_PROVIDER}

    Provider -->|gemini| Gemini[Google Gemini API]
    Provider -->|ollama| Ollama[Local Ollama Service]
    Provider -->|bedrock| Bedrock[AWS Bedrock Nova Lite]

    Gemini --> Suggestions[AI Suggestions]
    Ollama --> Suggestions
    Bedrock --> Suggestions

    Suggestions --> Review[Human Review Workflow]
```

## AI Suggestion Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Uploaded
    Uploaded --> OCRProcessed
    OCRProcessed --> AISuggested
    AISuggested --> Accepted
    AISuggested --> Rejected
    AISuggested --> Regenerated
    Regenerated --> AISuggested
    Accepted --> Finalized
    Rejected --> ManualEdit
    ManualEdit --> Finalized
```

## AI Metadata Fields

The application stores AI-generated metadata separately from official metadata.

| Field | Purpose |
| --- | --- |
| ai_document_type | Suggested document classification |
| ai_department | Suggested business department |
| ai_tags | Suggested tags |
| ai_summary | AI-generated document summary |
| ai_metadata_provider | Provider that generated the suggestion |
| ai_suggestion_status | Tracks review state |
| ai_suggested_at | Timestamp of generation |
| ai_error | Error information if generation fails |

## Human Review Design

The platform intentionally requires human review before AI metadata becomes official metadata.

Benefits:

- Prevents incorrect classification.
- Keeps users in control.
- Improves metadata quality.
- Supports auditability.
- Allows safe experimentation with local and external AI providers.

## AI Provider Options

| Provider | Advantage | Tradeoff |
| --- | --- | --- |
| Gemini | Better metadata quality and reasoning | Sends data externally |
| AWS Bedrock Nova Lite | AWS-managed model access through boto3 | Requires AWS credentials, permissions, and model access |
| Ollama | Local/private inference | Limited by local CPU and memory |

## Current AI Design Decisions

- Gemini is preferred when external API usage is acceptable.
- AWS Bedrock Nova Lite is available when AWS-managed inference is preferred.
- Ollama provides a local/private fallback.
- qwen2.5:0.5b is currently used because it fits within CRC resource constraints.
- AI suggestions are generated during upload when extracted text is available.
- AI suggestions remain separate from official metadata until accepted.

## Future AI Enhancements

Planned future enhancements include:

- Background AI processing queues.
- Semantic search.
- Vector embeddings.
- RAG document question answering.
- Metadata confidence scoring.
- Duplicate document detection.
- AI-assisted workflow approvals.
- Document summarization.
- Entity extraction.
- Multilingual OCR enhancement.
