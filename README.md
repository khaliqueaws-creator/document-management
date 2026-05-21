# Intelligent Document Management Platform

A Django-based intelligent document management application deployed on OpenShift CRC with MySQL, persistent document storage, Okta authentication, OCR, audit logging, and AI metadata suggestions through a switchable Gemini or Ollama provider.

The public demo path used during development is:

```text
https://docsdemo.khalique.net/
```

OpenShift CRC still exposes the internal route host, while Cloudflare Tunnel provides the public demo hostname.

## Project Purpose

This project is a learning and architecture build for an enterprise-style document management and intelligent document processing platform. The goal is to grow a simple upload/search application into a practical ECM/IDP-style system using open-source components and production-like deployment patterns.

The platform currently supports document upload, metadata capture, OCR and text extraction, secure viewing, role-based access, audit history, OpenShift deployment, and AI-assisted metadata suggestions.

## Current Feature Set

- Upload PDFs, Word documents, text files, spreadsheets, and supported images.
- Validate uploads by extension, content type, and configured size limit.
- Store uploaded files on OpenShift persistent volume storage.
- Store document metadata and extracted text in MySQL.
- Extract text from PDF, DOCX, TXT, XLSX, and image files.
- Use OCR fallback for scanned PDFs and direct OCR for scanned images.
- Search by document type, subtype, department, author, tags, and extracted text.
- Paginate search results.
- Preview supported image documents inline.
- Open stored documents through authenticated secure views.
- Edit document metadata after upload.
- Delete documents through an admin-only confirmation flow.
- Record upload, metadata edit, and delete actions in an audit table.
- View audit events through an admin-only audit page.
- Authenticate users through Okta OIDC.
- Authorize access through Okta group-based application roles.
- Generate AI metadata suggestions through Ollama or Gemini.
- Auto-generate AI suggestions during upload when extracted text is available.
- Review, accept, reject, or regenerate AI suggestions from the edit metadata page.

## Technology Stack

| Layer | Technology |
| --- | --- |
| Frontend | Django templates, server-rendered HTML/CSS |
| Backend | Python, Django |
| App server | Gunicorn |
| Database | MySQL |
| File storage | OpenShift PersistentVolumeClaim |
| OCR | Tesseract, pdf2image, Pillow |
| Document parsing | pypdf, python-docx, openpyxl |
| Authentication | Okta OIDC through Authlib |
| Authorization | Okta groups stored in Django session |
| AI metadata | Switchable Ollama or Gemini provider |
| Container platform | OpenShift CRC |
| Container image | Docker build through OpenShift binary build |
| Public demo access | Cloudflare Tunnel |

## High-Level Architecture

```text
Browser
  -> Cloudflare Tunnel / OpenShift Route
  -> document-app Service
  -> Gunicorn + Django
  -> MySQL Service
  -> MySQL PVC

Django
  -> document media PVC
  -> Gemini API over HTTPS
  -> or Ollama Service and Ollama model PVC
```

The Django application and MySQL run as separate OpenShift deployments. Uploaded documents live on the media PVC. Gemini is the preferred external provider for higher-quality metadata suggestions. Ollama remains available as a local/private fallback and serves the local model over the internal OpenShift service name `http://ollama:11434`.

## Role Model

| Role | Okta Group | Capability |
| --- | --- | --- |
| Viewer | `DjangoViewer` | Search and view documents |
| Loader | `DjangoLoader` | Viewer permissions plus upload, scanned OCR, edit metadata, AI suggestion review |
| Admin | `DjangoAdmin` | Loader permissions plus delete access and audit log access |

Access is enforced with view decorators in `documents/permissions.py`. Group values are read from Okta claims during login and stored in the Django session.

## Development Phases

### Phase 1: Core Document Management

The first phase established the basic Django document management application.

Implemented:

- `Document` model for metadata and file storage.
- Upload form for supported document types.
- Search page with metadata filters.
- Secure document view endpoint.
- Edit metadata page.
- Basic templates and navigation.
- Local media storage that later mapped cleanly to OpenShift PVC storage.

Implementation method:

- Kept the application server-rendered with Django templates for simplicity.
- Used Django `ModelForm` classes for upload and edit flows.
- Stored uploaded files with Django `FileField`.
- Used metadata fields that are simple to search with database filters.

### Phase 2: OCR, Security, Audit, And OpenShift Hardening

The second phase made the app more enterprise-like and deployment-ready.

Implemented:

- Text extraction for PDF, DOCX, TXT, XLSX, and image uploads.
- OCR for scanned image upload and scanned PDF fallback.
- OCR language support for English, Hindi, and Urdu.
- Upload validation by extension, MIME/content type, and size.
- Okta OIDC login.
- Role-based access using `DjangoViewer`, `DjangoLoader`, and `DjangoAdmin`.
- Admin-only delete confirmation.
- Audit event model and audit page.
- MySQL deployment on OpenShift CRC.
- Media PVC for uploaded files.
- ConfigMap and Secret split for runtime configuration.
- Gunicorn-based container runtime.
- Migration init container in the Django deployment.
- Cloudflare Tunnel demo access through `https://docsdemo.khalique.net/`.

Implementation method:

- Used native parsers before OCR where possible.
- Used Tesseract only when needed for image-like content.
- Kept secrets in `docmanager-secrets`.
- Kept non-secret runtime values in `docmanager-config`.
- Used OpenShift PVCs for database and uploaded media persistence.
- Used an init container to run `python manage.py migrate --noinput` before the web container starts.

### Phase 3: AI Metadata Suggestions With Gemini And Ollama

The third phase added AI-assisted metadata generation with a provider switch.

Implemented:

- Ollama deployment, service, model PVC, and model pull job.
- Gemini API support through a provider toggle.
- Django settings for provider, Ollama URL/model, Gemini URL/model/API key, timeout, context, and prompt text limit.
- AI metadata fields on `Document`:
  - `ai_document_type`
  - `ai_department`
  - `ai_tags`
  - `ai_summary`
  - `ai_suggestion_status`
  - `ai_suggested_at`
  - `ai_error`
- Metadata suggestion helper in `documents/ai_metadata.py` with `ollama` and `gemini` provider paths.
- Upload-time AI suggestion generation.
- Review panel on the edit metadata page.
- Accept, reject, and regenerate actions.
- Better Ollama error reporting for HTTP 500 responses.
- CRC-friendly Ollama model selection with `qwen2.5:0.5b`.
- Gemini provider validation with `gemini-2.5-flash`.

Implementation method:

- Started with `phi3`, then moved to `qwen2.5:0.5b` because CRC memory was too constrained for `phi3`.
- Limited prompt input with `AI_METADATA_MAX_CHARS=2500`.
- Limited model context with `OLLAMA_NUM_CTX=1024`.
- Kept Ollama internal only, accessed through `http://ollama:11434`.
- Added `AI_METADATA_PROVIDER` so deployments can switch between local Ollama and external Gemini without code changes.
- Stored `GEMINI_API_KEY` in the OpenShift Secret template.
- Use Gemini for better extraction quality when external API usage is acceptable.
- Use Ollama when local/private processing is preferred.
- Used a separate model pull job so model downloads are explicit and repeatable.
- Set Ollama deployment strategy to `Recreate` so CRC does not try to run old and new Ollama pods at the same time.
- Kept AI suggestions staged separately from official metadata until a loader accepts them.

## AI Metadata Workflow

Current behavior:

1. A loader uploads a document.
2. Django saves the file and extracts text.
3. If `AUTO_AI_METADATA_ON_UPLOAD=True`, Django sends extracted text to the configured provider.
4. The provider returns suggested JSON metadata.
5. Django stores the AI suggestions separately from the official metadata.
6. The user is redirected to the edit metadata page.
7. The user can accept, reject, or regenerate suggestions.

This design keeps a human review step. AI does not overwrite official metadata automatically.

Supported AI suggestion source types:

- PDF with extractable text.
- Scanned PDF when OCR fallback succeeds.
- DOCX.
- TXT.
- XLSX.
- Images supported by OCR.

The only requirement is non-empty extracted text.

## Important Runtime Configuration

Runtime values are configured in `openshift/docmanager-configmap.yaml`.

| Key | Purpose | Current Default |
| --- | --- | --- |
| `AI_METADATA_PROVIDER` | AI provider to use: `ollama` or `gemini` | `ollama` in repo, `gemini` in current demo deployment |
| `OLLAMA_BASE_URL` | Internal Ollama API endpoint | `http://ollama:11434` |
| `OLLAMA_MODEL` | Local model used for suggestions | `qwen2.5:0.5b` |
| `OLLAMA_TIMEOUT_SECONDS` | HTTP timeout for model generation | `90` |
| `OLLAMA_NUM_CTX` | Ollama context size | `1024` |
| `GEMINI_BASE_URL` | Gemini API endpoint | `https://generativelanguage.googleapis.com` |
| `GEMINI_MODEL` | Gemini model used for suggestions | `gemini-2.5-flash` |
| `GEMINI_API_KEY` | Gemini API key, stored in Secret | empty placeholder |
| `AI_METADATA_MAX_CHARS` | Max extracted text sent to AI | `2500` |
| `AUTO_AI_METADATA_ON_UPLOAD` | Generate suggestions during upload | `True` |
| `ALLOWED_HOSTS` | Django allowed hosts | includes `docsdemo.khalique.net` |
| `OKTA_CALLBACK_URL` | OIDC callback URL | `https://docsdemo.khalique.net/oidc/callback` |

Secrets live in `openshift/docmanager-secret-template.yaml`, but real secret values should not be committed.

To use Gemini, set `GEMINI_API_KEY` in the OpenShift Secret and change:

```yaml
AI_METADATA_PROVIDER: "gemini"
```

To use local Ollama, change it back to:

```yaml
AI_METADATA_PROVIDER: "ollama"
```

Gemini sends extracted document text to Google. Ollama keeps the text inside the local environment.

## Deployment Summary

Use `openshift/README.md` as the operational runbook. The short version is below.

Create or select the project:

```powershell
oc project docmanager
```

Apply config and secrets:

```powershell
oc apply -f openshift/docmanager-secret-template.yaml
oc apply -f openshift/docmanager-configmap.yaml
```

Build the Django image after code changes:

```powershell
oc start-build document-app --from-dir=. --follow
```

Apply or restart Django:

```powershell
oc apply -f openshift/docmanager-deployment.yaml
oc rollout restart deployment/document-app
oc rollout status deployment/document-app
```

Apply Ollama resources:

```powershell
oc apply -f openshift/ollama-pvc.yaml
oc apply -f openshift/ollama-deployment.yaml
oc rollout status deployment/ollama
```

Pull or refresh the configured model:

```powershell
oc delete job ollama-pull-model --ignore-not-found
oc apply -f openshift/ollama-model-pull-job.yaml
oc logs job/ollama-pull-model -f
```

Verify Ollama:

```powershell
oc exec deployment/ollama -- ollama list
oc exec deployment/ollama -- ollama run qwen2.5:0.5b "Say OK"
```

## Current Operational Notes

- Rebuild the Django image after Python, template, migration, or settings changes.
- Reapply the ConfigMap after changing runtime values.
- Restart `document-app` after ConfigMap changes so the pod reads new environment variables.
- Recreate `ollama-pull-model` after changing `OLLAMA_MODEL`.
- Use `oc set env secret/docmanager-secrets GEMINI_API_KEY="..."` to update only the Gemini key without overwriting database secrets.
- Avoid applying the full secret template unless it contains real local values for every key.
- Do not delete `mysql-pvc` unless intentionally resetting database data.
- Do not delete `docmanager-media-pvc` unless intentionally deleting uploaded files.
- `phi3` was tested but needed more memory than available in CRC; `qwen2.5:0.5b` is the current CRC-friendly model.

## Testing And Validation

Useful local checks:

```powershell
$env:DJANGO_SECRET_KEY='test-secret'
.\venv\Scripts\python.exe manage.py check
```

Focused AI helper tests:

```powershell
$env:DJANGO_SETTINGS_MODULE='docmanager.settings'
$env:DJANGO_SECRET_KEY='test-secret'
.\venv\Scripts\python.exe -m unittest documents.tests.MetadataSuggestionTests
```

Note: the local virtual environment must have all requirements installed. During development, full Django checks were blocked locally when Pillow was missing from the venv, even though `pillow` is listed in `requirements.txt`.

Cluster validation:

```powershell
oc get pods
oc logs deployment/document-app --tail=100
oc logs deployment/ollama --tail=100
oc exec deployment/document-app -- printenv OLLAMA_MODEL
oc exec deployment/document-app -- printenv AI_METADATA_PROVIDER
oc exec deployment/document-app -- printenv GEMINI_MODEL
oc exec deployment/ollama -- ollama list
```

## Future Improvements

Near-term:

- Add a visible AI status column or badge on the search results page.
- Add a "needs review" filter for documents with AI suggestions.
- Add audit events specifically for AI generation, acceptance, and rejection.
- Add retry behavior or background queue for upload-time AI failures.
- Improve prompt design with controlled document type and department choices.
- Add tests for upload-time AI suggestion generation.
- Add a management command to backfill AI suggestions for existing documents.

Medium-term:

- Move OCR and AI work to asynchronous background jobs with Celery or Django-Q.
- Add progress/status tracking for OCR and AI processing.
- Add document versioning.
- Add richer preview support for PDFs and office documents.
- Add full-text indexes for extracted text search.
- Add duplicate detection based on file hash and extracted text.
- Add export/reporting for audit events and metadata.

Long-term:

- Add semantic search with embeddings and vector storage.
- Add RAG-based document question answering.
- Add workflow approvals for metadata changes and document publication.
- Add retention policies and legal hold concepts.
- Add multi-tenant separation by department or organization.
- Move from CRC-only deployment toward a production OpenShift or Kubernetes environment.
- Consider PostgreSQL with pgvector for combined relational and semantic search.

## Repository Map

| Path | Purpose |
| --- | --- |
| `docmanager/` | Django project settings and root URL configuration |
| `documents/` | Main app models, forms, views, templates, AI metadata helper, migrations |
| `openshift/` | OpenShift manifests and CRC runbook |
| `Dockerfile` | Container image build definition |
| `requirements.txt` | Python dependency list |
| `manage.py` | Django management entry point |

## Learning Goals

This project intentionally combines application code, security, infrastructure, persistence, OCR, and AI integration. It is meant to model the kinds of tradeoffs found in enterprise content management and intelligent document processing systems while staying small enough to run locally on OpenShift CRC.

## Author

Khalique AWS
