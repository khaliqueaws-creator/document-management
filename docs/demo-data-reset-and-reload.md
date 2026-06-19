# OpenShift Demo Data Reset and Reload

This runbook resets the document-management demo data without deleting the
OpenShift deployments, PostgreSQL schema, application configuration, secrets,
or persistent volume claims. It then reloads local documents, regenerates
embeddings, and synchronizes OpenSearch.

The commands use PowerShell and assume the project is deployed in the
`docmanager` namespace.

> This process permanently deletes the current documents, uploaded files,
> chunks, metadata-quality reviews, audit events, and derived OpenSearch
> records. Do not delete the PostgreSQL, media, or OpenSearch PVCs.

## 1. Set the Namespace

```powershell
$ns = "docmanager"
```

Confirm that the required deployments are available:

```powershell
oc get deployments -n $ns document-app postgresql opensearch
```

## 2. Clear Existing Application Data

Delete uploaded files from the media PVC and remove document-related records
from PostgreSQL:

```powershell
oc exec -n $ns deployment/document-app -c document-app -- python manage.py shell -c "from documents.models import Document,AuditEvent; docs=list(Document.objects.all()); [d.file.delete(save=False) for d in docs if d.file]; Document.objects.all().delete(); AuditEvent.objects.all().delete(); print('Application data cleared')"
```

Deleting `Document` records also deletes their `DocumentChunk` and
`MetadataQualityReview` records through Django's configured relationships.

Clear the derived OpenSearch document and chunk records while preserving the
indexes and mappings:

```powershell
oc exec -n $ns deployment/document-app -c document-app -- python manage.py shell -c "from documents.opensearch_indexing import get_opensearch_client,get_document_index_name,get_chunk_index_name; c=get_opensearch_client(); [c.delete_by_query(index=i,body={'query':{'match_all':{}}},conflicts='proceed',refresh=True,ignore=[404]) for i in (get_document_index_name(),get_chunk_index_name())]; print('OpenSearch cleared')"
```

Confirm that PostgreSQL is empty:

```powershell
oc exec -n $ns deployment/document-app -c document-app -- python manage.py shell -c "from documents.models import Document,DocumentChunk,AuditEvent; print('documents=',Document.objects.count(),'chunks=',DocumentChunk.objects.count(),'audit=',AuditEvent.objects.count())"
```

## 3. Redeploy the Application

Before redeploying, confirm that
`openshift/docmanager-deployment.yaml` references the tested application image.
The image must include a working `bulk_import_documents` command.

Apply the current configuration and deployment:

```powershell
oc apply -n $ns -f openshift/docmanager-configmap.yaml
oc apply -n $ns -f openshift/docmanager-deployment.yaml
oc rollout status -n $ns deployment/document-app
```

Capture the running application pod:

```powershell
$pod = oc get pod -n $ns -l app=document-app -o jsonpath="{.items[0].metadata.name}"
$pod
```

Create a temporary import directory:

```powershell
oc exec -n $ns pod/$pod -c document-app -- mkdir -p /tmp/bulk-docs
```

## 4. Copy Local Documents to the Pod

Change into the local directory containing the files. Using a relative source
path avoids the Windows drive-letter colon being interpreted as a remote
destination by `oc cp`.

```powershell
Push-Location "$env:USERPROFILE\Downloads\ecm_demo_1000_pdf_corpus\pdfs"
oc cp . "$ns/${pod}:/tmp/bulk-docs" -c document-app
Pop-Location
```

Confirm how many files were copied:

```powershell
oc exec -n $ns pod/$pod -c document-app -- sh -c "find /tmp/bulk-docs -type f | wc -l"
```

Files under `/tmp` are removed when the pod restarts. Complete the import before
restarting or redeploying the pod.

## 5. Import, Embed, and Index the Documents

Run the import against the exact pod containing `/tmp/bulk-docs`:

```powershell
oc exec -n $ns pod/$pod -c document-app -- python manage.py bulk_import_documents /tmp/bulk-docs --rebuild-embeddings --reindex-opensearch --create-indexes
```

The command:

- copies documents to the media PVC;
- creates canonical `Document` records in PostgreSQL;
- extracts document text;
- creates chunks and Bedrock Titan embeddings;
- stores chunks and embeddings in PostgreSQL; and
- indexes document metadata and chunk vectors in OpenSearch through the MCP
  indexing path when enabled.

The final output should report `failed=0`.

## 6. Synchronize OpenSearch

Run a complete synchronization from PostgreSQL after the import:

```powershell
oc exec -n $ns deployment/document-app -c document-app -- python manage.py reindex_opensearch --create-indexes
```

OpenSearch is a derived index. PostgreSQL remains the source of truth, so this
command can safely rebuild missing or stale search records.

## 7. Validate the Reload

Check PostgreSQL document, chunk, and embedding counts:

```powershell
oc exec -n $ns deployment/document-app -c document-app -- python manage.py shell -c "from documents.models import Document,DocumentChunk; print('documents=',Document.objects.count()); print('chunks=',DocumentChunk.objects.count()); print('embedded=',DocumentChunk.objects.exclude(embedding=[]).count())"
```

Run the complete document-intelligence health check:

```powershell
oc exec -n $ns deployment/document-app -c document-app -- python manage.py health_document_intelligence
```

The expected final line is:

```text
summary=done errors=0 warnings=0
```

The PostgreSQL document count can be higher than the chunk count when some
documents contain no extractable text. The PostgreSQL embedded-chunk count and
OpenSearch chunk count should match.

## 8. Browser Validation

Complete these checks before a demonstration:

1. Search finds a known filename or metadata value.
2. AI Search finds relevant documents using conceptual wording.
3. Ask Documents returns a grounded answer with citations.
4. A follow-up question uses the previous conversational context.
5. Citation links open the correct source documents.

Confirm MCP indexing and retrieval from the application logs:

```powershell
oc logs -n $ns deployment/document-app -c document-app --tail=1000 |
  Select-String "mcp_indexing|mcp_retrieval|rag_mcp"
```
