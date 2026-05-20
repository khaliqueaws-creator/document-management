# Intelligent Document Management Platform

A Django-based intelligent document management application deployed on OpenShift CRC using containerized services and persistent storage.

## Overview

This project was built as a learning and enterprise-style architecture platform to explore:

* Document management
* OCR and intelligent document processing
* OpenShift container deployment
* Authentication and authorization
* Metadata-driven search
* AI-powered document enhancements

The application allows users to upload documents, assign metadata, search content, preview supported files directly in the browser, and track basic audit history for important document actions.

---

## Features

* Document upload and persistent storage
* Metadata tagging, editing, and search
* OCR text extraction for supported document and image types
* Okta OIDC authentication
* Role-based access control
* File type validation for supported formats
* File size limits for uploads
* Paginated search results
* Admin-only delete confirmation flow
* Basic audit log for uploads, metadata edits, and deletes
* Persistent storage using OpenShift PVC
* Containerized deployment with Docker and Gunicorn
* OpenShift Secrets for database and application credentials
* OpenShift CRC deployment support
* Cloudflare Tunnel public demo support

---

## Technology Stack

| Layer              | Technology        |
| ------------------ | ----------------- |
| Frontend           | Django Templates  |
| Backend            | Python / Django   |
| App Server         | Gunicorn          |
| Database           | MySQL             |
| OCR                | Tesseract OCR     |
| Authentication     | Okta OIDC         |
| Container Platform | OpenShift CRC     |
| Containerization   | Docker            |
| Storage            | OpenShift PVC     |
| Cloud Exposure     | Cloudflare Tunnel |

---

## Architecture

Browser -> OpenShift Route -> Gunicorn/Django Application -> MySQL Database + Persistent File Storage

---

## Current Capabilities

* Upload PDFs, Office documents, text files, spreadsheets, and supported image formats
* Validate uploads by extension, content type, and configured size limit
* Store and update searchable document metadata
* Extract text from uploaded files using native parsers and OCR fallback where supported
* Search by metadata fields and extracted document text
* Browse search results with pagination
* Preview supported image files inline and open stored documents securely
* Edit document metadata through the application UI
* Delete documents through an admin-only confirmation page
* Record upload, edit, and delete activity in an audit table
* View audit events through an admin-only audit log page
* Secure login and role-based access using Okta groups
* Deploy on OpenShift CRC with MySQL, PVC-backed media storage, and OpenShift Secrets

---

## Roles

| Role         | Capability |
| ------------ | ---------- |
| DjangoViewer | Search and view documents |
| DjangoLoader | Upload documents, run scanned-image OCR, and edit metadata |
| DjangoAdmin  | Loader permissions plus delete access and audit log access |

---

## Production-Like Deployment Notes

The container image runs Django through Gunicorn rather than `runserver`.

Application and database credentials are supplied through the `docmanager-secrets` OpenShift Secret. Non-secret runtime configuration is supplied through the `docmanager-config` ConfigMap.

The OpenShift deployment runs database migrations in an init container before starting the Gunicorn application container.

After adding new migrations, rebuild the application image and redeploy:

```powershell
oc start-build document-app --from-dir=. --follow
oc rollout restart deployment/document-app
oc rollout status deployment/document-app
```

---

## Planned Enhancements

* AI-based document classification
* Automatic metadata extraction
* Semantic/vector search
* RAG-based document assistant
* Workflow and approval engine
* Document versioning
* Intelligent capture interface
* Async OCR processing using Celery
* PostgreSQL + pgvector integration

---

## Learning Goals

This project is intended to simulate enterprise content management and intelligent document processing systems similar to modern ECM/IDP platforms while using open-source technologies.

---

## Author

Khalique AWS
