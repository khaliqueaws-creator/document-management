# Kubernetes Deployment Checklist

This directory contains Kubernetes manifests for the current PostgreSQL +
OpenSearch architecture. The manifests are intended to track the working
OpenShift deployment without OpenShift-only Route or SCC resources.

## Current Components

- `document-app`: Django + Gunicorn app using `docker.io/khalique/document-app:1.6-bulk`.
- `postgresql`: Standard `postgres:16` database for canonical document state.
- `opensearch`: Derived keyword/vector retrieval index.
- `opensearch-dashboards`: Optional operational UI for OpenSearch.
- `docmanager-media-pvc`: Uploaded document storage.
- `postgresql-pvc`: PostgreSQL data storage.
- `opensearch-pvc`: OpenSearch index storage.

MySQL and PostgreSQL pgvector are not part of the current Kubernetes runtime.
PostgreSQL stores canonical metadata, extracted text, chunk text, and JSON
embeddings for rebuild/debug support. OpenSearch owns semantic retrieval.

## Apply Order

From the repository root:

```powershell
kubectl apply -f k8s/yaml/docmanager-secret-template.yaml
kubectl apply -f k8s/yaml/docmanager-configmap.yaml
```

Edit secret values before applying them in a real environment. Do not commit
live secret files.

Avoid `kubectl apply -f k8s/yaml` if you keep ignored `.local` secret files in
that directory. Apply the listed files explicitly so local-only secrets are not
sent to the cluster by accident.

Apply persistent storage and data/search services:

```powershell
kubectl apply -f k8s/yaml/postgresql-pvc.yaml
kubectl apply -f k8s/yaml/opensearch-pvc.yaml
kubectl apply -f k8s/yaml/docmanager-pvc.yaml
kubectl apply -f k8s/yaml/postgresql-deployment.yaml
kubectl apply -f k8s/yaml/opensearch-deployment.yaml
kubectl rollout status deployment/postgresql
kubectl rollout status deployment/opensearch
```

Apply the app and ingress:

```powershell
kubectl apply -f k8s/yaml/docmanager-deployment.yaml
kubectl apply -f k8s/yaml/aws-khalique-us-ingress.yaml
kubectl rollout status deployment/document-app
```

OpenSearch Dashboards is optional:

```powershell
kubectl apply -f k8s/yaml/opensearch-dashboards-deployment.yaml
kubectl rollout status deployment/opensearch-dashboards
```

## Validation

Check pods and services:

```powershell
kubectl get pods
kubectl get svc
kubectl get ingress
```

Validate Django, PostgreSQL, and OpenSearch:

```powershell
kubectl exec deployment/document-app -- python manage.py check
kubectl exec deployment/document-app -- python -c "from django.conf import settings; print(settings.DATABASES['default']['ENGINE']); print(settings.DATABASES['default']['HOST'])"
kubectl exec deployment/document-app -- python -c "from documents.opensearch_indexing import get_opensearch_client; print(get_opensearch_client().info())"
```

Build embeddings and indexes for AI Search:

```powershell
kubectl exec deployment/document-app -- python manage.py rebuild_embeddings --limit 5
kubectl exec deployment/document-app -- python manage.py reindex_opensearch --create-indexes
kubectl exec deployment/document-app -- python manage.py health_ai_search
kubectl exec deployment/document-app -- python manage.py validate_bedrock_opensearch
```

Use `health_ai_search --skip-bedrock` for PostgreSQL and OpenSearch checks
without making a live AWS Bedrock embedding call.

## Bulk Test Import

Copy local test files into the app pod:

```powershell
$pod = kubectl get pod -l app=document-app -o jsonpath="{.items[0].metadata.name}"
kubectl cp .\test_documents\pdf\ "${pod}:/tmp/bulk-docs"
```

Import and prepare AI Search:

```powershell
kubectl exec deployment/document-app -- python manage.py bulk_import_documents /tmp/bulk-docs --limit 20 --rebuild-embeddings --reindex-opensearch --create-indexes
```

## Notes

- `AI_EMBEDDING_DIMENSIONS=1024` describes the expected Bedrock Titan embedding
  vector length and OpenSearch mapping dimension. It is not a PostgreSQL vector
  setting.
- If the app reports OpenSearch connection refused, confirm
  `deployment/opensearch` is running before reindexing.
- If the app image tag changes, update both the init container and app
  container in `k8s/yaml/docmanager-deployment.yaml`.
