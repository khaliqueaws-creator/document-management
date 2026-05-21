# Deployment Guide

This document describes the deployment architecture and operational model for the Intelligent Document Management Platform.

The application is currently designed and tested primarily on OpenShift CRC running on a local workstation environment.

## Deployment Architecture

```mermaid
flowchart TB
    subgraph OpenShift_CRC[OpenShift CRC Project: docmanager]
        Config[docmanager-config ConfigMap]
        Secret[docmanager-secrets Secret]

        subgraph Django_Deployment[Deployment: document-app]
            Init[Init Container\nwait for MySQL + migrate]
            Web[document-app Container\nGunicorn on port 8000]
        end

        subgraph Database[Deployment: mysql]
            DB[MySQL 8.0 Container\nport 3306]
        end

        subgraph AI_Local[Deployment: ollama]
            Ollama[Ollama Container\nport 11434]
        end

        Media[(Media PVC)]
        DBPVC[(MySQL PVC)]
        ModelPVC[(Ollama Model PVC)]

        Config --> Init
        Secret --> Init
        Config --> Web
        Secret --> Web

        Init --> DB
        Web --> DB
        Web --> Media
        DB --> DBPVC
        Ollama --> ModelPVC
        Web --> Ollama
    end
```

## Deployment Components

| Component | Purpose |
| --- | --- |
| ConfigMap | Stores non-secret runtime configuration. |
| Secret | Stores database credentials, Okta secrets, Gemini API keys, and sensitive values. |
| Init Container | Waits for MySQL availability and runs Django migrations before startup. |
| document-app | Main Django application container running under Gunicorn. |
| mysql | Persistent relational database service. |
| ollama | Optional local AI inference service. |
| Media PVC | Persistent storage for uploaded files. |
| MySQL PVC | Persistent database storage. |
| Ollama Model PVC | Persistent AI model storage. |

## Deployment Flow

```mermaid
sequenceDiagram
    participant Admin
    participant OpenShift
    participant MySQL
    participant Django
    participant Ollama

    Admin->>OpenShift: Apply ConfigMap and Secrets
    Admin->>OpenShift: Start Django build
    OpenShift->>Django: Build application image

    Admin->>OpenShift: Apply mysql deployment
    OpenShift->>MySQL: Start MySQL pod

    Admin->>OpenShift: Apply document-app deployment
    OpenShift->>Django: Start init container
    Django->>MySQL: Wait for database
    Django->>MySQL: Run migrations
    OpenShift->>Django: Start Gunicorn container

    Admin->>OpenShift: Apply ollama deployment
    OpenShift->>Ollama: Start Ollama pod
```

## Persistent Storage Design

```mermaid
flowchart LR
    MySQL[(MySQL Pod)] --> DBPVC[(mysql-pvc)]
    Django[Django Pod] --> MediaPVC[(docmanager-media-pvc)]
    Ollama[Ollama Pod] --> ModelPVC[(ollama-models-pvc)]
```

## Operational Notes

- Do not delete PVCs unless intentionally resetting data.
- Restart the Django deployment after ConfigMap changes.
- Rebuild the image after Python or template updates.
- Recreate the Ollama model pull job after changing the configured model.
- Use OpenShift rollout status commands to validate deployments.
- Cloudflare Tunnel exposes the internal OpenShift route externally for demo access.

## Future Deployment Enhancements

Planned future improvements include:

- Production Kubernetes/OpenShift deployment.
- Horizontal scaling.
- Ingress controller with TLS termination.
- Asynchronous OCR and AI processing.
- External object storage.
- PostgreSQL with pgvector.
- CI/CD pipeline integration.
- Automated image builds.
- Centralized logging and monitoring.
