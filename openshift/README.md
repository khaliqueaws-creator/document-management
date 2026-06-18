# OpenShift CRC Runbook

Deploy, restart, and troubleshoot the Django document manager on local OpenShift CRC.

This runbook reflects the current implementation:

- Django runs as `deployment/document-app` with Gunicorn.
- PostgreSQL runs as `deployment/postgresql` and is the active database backend.
- OpenSearch runs as `deployment/opensearch` and owns derived keyword/vector retrieval indexes.
- The app image referenced by the manifests is `docker.io/khalique/document-app:2.6-metadata-quality-batch`.
- Ask Documents retrieval uses the backend MCP boundary with `MCP_RETRIEVAL_ENABLED=True`.
- MCP fallback is disabled in the checked-in OpenShift ConfigMap so validation failures are visible.
- Ollama runs as `deployment/ollama` and is reached by Django at `http://ollama:11434`.
- Gemini can be used by setting `AI_METADATA_PROVIDER=gemini`.
- AWS Bedrock Nova Lite can be used by setting `AI_METADATA_PROVIDER=bedrock`.
- Gemini and Bedrock are external provider options; Ollama remains the local/private CRC-friendly option.
- The current CRC-friendly AI model is `qwen2.5:0.5b`.
- AI metadata suggestions are generated during upload when `AUTO_AI_METADATA_ON_UPLOAD` is enabled.
- The public demo URL is `https://docsdemo.khalique.net/` through Cloudflare Tunnel, while the OpenShift route host remains `document-app-document-app.apps-crc.testing`.

> [!IMPORTANT]
> Do not commit real production secret values to GitHub. If `openshift/docmanager-secret-template.yaml` contains live values, keep it local or replace them with placeholders before committing.

## Contents

| Section | Use When |
| --- | --- |
| [A. Rebuild From Scratch](#a-rebuild-from-scratch) | CRC was recreated, the project was removed, or you want a clean local deployment. |
| [B. Normal Code Or Config Redeploy](#b-normal-code-or-config-redeploy) | You changed Django code, templates, migrations, ConfigMap values, or Ollama model settings. |
| [C. Stop CRC And Start CRC Then Check App](#c-stop-crc-and-start-crc-then-check-app) | You are shutting down or restarting your existing local CRC environment. |
| [D. PostgreSQL And OpenSearch Checks](#d-postgresql-and-opensearch-checks) | You want to verify database and search dependencies. |
| [E. Some Troubleshooting Tips](#e-some-troubleshooting-tips) | Pods, routes, migrations, probes, Ollama, or login checks are failing. |
| [F. Issue Log](#f-issue-log) | Known CRC/OpenShift issues and the exact recovery steps used. |

## A. Rebuild From Scratch

Use this path when you need a clean deployment in the `docmanager` OpenShift project.

### 1. Start In The Project

Create the project:

```powershell
oc new-project docmanager
```

If it already exists, select it:

```powershell
oc project docmanager
oc project -q
```

### 2. Prepare Secrets And Config

Generate local secret values:

```powershell
$SecretKey = python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
$DbPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})

$SecretKey
$DbPassword
```

Then update:

| File | What To Check |
| --- | --- |
| `openshift/docmanager-secret-template.yaml` | Django secret key, Okta client values, database values, Gemini API key, and AWS credential placeholders. |
| `openshift/docmanager-configmap.yaml` | Route host, Okta issuer, callback URL, logout URL, `ALLOWED_HOSTS`, provider toggle, Ollama settings, Gemini settings, Bedrock settings, MCP retrieval flags, and AI upload behavior. |
| `openshift/docmanager-deployment.yaml` | Probe `Host` header. It must also appear in `ALLOWED_HOSTS`. |

Current public URL values:

| Config Key | Expected Value |
| --- | --- |
| `OKTA_CALLBACK_URL` | `https://docsdemo.khalique.net/oidc/callback` |
| `OKTA_LOGOUT_REDIRECT_URL` | `https://docsdemo.khalique.net/` |
| `ALLOWED_HOSTS` | Must include `docsdemo.khalique.net` and `document-app-document-app.apps-crc.testing` |

### 3. Build Or Pull The App Image

The checked-in manifests reference the Docker Hub image:

```text
docker.io/khalique/document-app:2.6-metadata-quality-batch
```

If you are iterating locally, build and push a new tag before applying the
deployment:

```powershell
docker build --no-cache --pull -t docker.io/khalique/document-app:<tag> .
docker push docker.io/khalique/document-app:<tag>
```

Then update both the init container and app container image references in
`openshift/docmanager-deployment.yaml`.

The current known-good image for the MCP-backed RAG document Q&A path is
`docker.io/khalique/document-app:2.6-metadata-quality-batch`.

### 4. Apply Resources

Apply PostgreSQL resources first. The PostgreSQL deployment uses the standard `postgres:16` image. PostgreSQL reads database values from `docmanager-secrets` and remains the metadata/system-of-record database; OpenSearch owns semantic and vector retrieval.

```powershell
oc apply -f openshift/docmanager-secret-template.yaml
oc apply -f openshift/postgresql-pvc.yaml
oc apply -f openshift/postgresql-deployment.yaml
oc rollout status deployment/postgresql
```

Apply the Django resources:

```powershell
oc apply -f openshift/docmanager-configmap.yaml
oc apply -f openshift/docmanager-pvc.yaml
oc apply -f openshift/docmanager-service.yaml
oc apply -f openshift/docmanager-route.yaml
oc apply -f openshift/docmanager-deployment.yaml
oc rollout status deployment/document-app
```

The `document-app` deployment runs migrations automatically in an init container before Gunicorn starts.

> [!NOTE]
> The Django image must exist in Docker Hub before `deployment/document-app` can start. If the deployment shows `ImagePullBackOff`, confirm the referenced tag was pushed and is pullable.

### 5. Apply Ollama Resources

Apply Ollama after the config map exists. The model pull job reads `OLLAMA_MODEL` from `openshift/docmanager-configmap.yaml`.

Current Ollama implementation choices:

| Item | Current Value | Reason |
| --- | --- | --- |
| Model | `qwen2.5:0.5b` | Fits local CRC memory better than `phi3`. |
| Deployment strategy | `Recreate` | Prevents CRC from trying to run old and new Ollama pods at the same time during rollout. |
| Memory request | `256Mi` | Keeps the pod schedulable on constrained CRC. |
| Memory limit | `2Gi` | Gives the small model enough runtime headroom. |
| Model storage | `ollama-models-pvc` | Keeps downloaded models across pod restarts. |

```powershell
oc apply -f openshift/ollama-pvc.yaml
oc apply -f openshift/ollama-deployment.yaml
oc rollout status deployment/ollama
oc delete job ollama-pull-model --ignore-not-found
oc apply -f openshift/ollama-model-pull-job.yaml
oc wait --for=condition=complete job/ollama-pull-model --timeout=600s
```

If you change `OLLAMA_MODEL`, delete and recreate the pull job:

```powershell
oc delete job ollama-pull-model
oc apply -f openshift/ollama-model-pull-job.yaml
```

Check the model list:

```powershell
oc exec deployment/ollama -- ollama list
oc exec deployment/ollama -- ollama run qwen2.5:0.5b "Say OK"
```

### 6. Apply OpenSearch And OpenSearch Dashboards

OpenSearch and OpenSearch Dashboards use upstream images that expect to run with UID/GID `1000`. On OpenShift CRC, grant `anyuid` to both service accounts before or immediately after applying the manifests. The grant requires a CRC admin login.

If `oc` is not on `PATH`, load CRC's bundled client in the current PowerShell session:

```powershell
& crc oc-env | Invoke-Expression
```

Log in as the local CRC admin. Get the current password from CRC rather than saving it in this file:

```powershell
crc console --credentials
oc login -u kubeadmin -p <password-from-crc> https://api.crc.testing:6443
oc project docmanager
```

Apply OpenSearch:

```powershell
oc apply -f openshift/opensearch-pvc.yaml
oc apply -f openshift/opensearch-deployment.yaml
oc adm policy add-scc-to-user anyuid -z opensearch -n docmanager
oc rollout restart deployment/opensearch
oc rollout status deployment/opensearch
```

Apply OpenSearch Dashboards:

```powershell
oc apply -f openshift/opensearch-dashboards-deployment.yaml
oc adm policy add-scc-to-user anyuid -z opensearch-dashboards -n docmanager
oc rollout restart deployment/opensearch-dashboards
oc rollout status deployment/opensearch-dashboards
```

Verify:

```powershell
oc get pods -l app=opensearch
oc get pods -l app=opensearch-dashboards
oc logs deployment/opensearch-dashboards --tail=100
```

Expected Dashboards status:

```text
opensearch-dashboards-...   1/1   Running   0
```

### 7. Verify The App

Check OpenShift objects:

```powershell
oc get pods
oc get svc
oc get route
```

Start a local port-forward:

```powershell
oc port-forward svc/document-app 8080:80
```

In another PowerShell window:

```powershell
curl.exe -v http://localhost:8080/
curl.exe -v http://localhost:8080/login/
```

Expected results:

| URL | Expected Result |
| --- | --- |
| `/` | `302` redirect to `/login/` |
| `/login/` | `302` redirect to Okta |

For Cloudflare tunnel access, keep the port-forward running and start the tunnel:

```powershell
cloudflared tunnel --config C:\Users\UFUserAdmin\.cloudflared\config.yml run docmanager
```

Test the public hostname:

```powershell
curl.exe -vk https://docsdemo.khalique.net/
curl.exe -vk https://docsdemo.khalique.net/login/
```

Browser URL:

```text
https://docsdemo.khalique.net/login/
```

### 7. Verify AI Metadata Suggestions

Confirm the Django pod reads the expected AI settings:

```powershell
oc exec deployment/document-app -- printenv OLLAMA_BASE_URL
oc exec deployment/document-app -- printenv OLLAMA_MODEL
oc exec deployment/document-app -- printenv AI_METADATA_PROVIDER
oc exec deployment/document-app -- printenv GEMINI_BASE_URL
oc exec deployment/document-app -- printenv GEMINI_MODEL
oc exec deployment/document-app -- printenv AI_METADATA_MAX_CHARS
oc exec deployment/document-app -- printenv AUTO_AI_METADATA_ON_UPLOAD
oc exec deployment/document-app -- printenv AI_RAG_TOP_K
oc exec deployment/document-app -- printenv AI_RAG_MAX_CONTEXT_CHARS
oc exec deployment/document-app -- printenv AI_RAG_MAX_ANSWER_TOKENS
oc exec deployment/document-app -- printenv AI_RAG_MIN_CONTEXT_CHARS
oc exec deployment/document-app -- printenv AI_RAG_MIN_RETRIEVAL_SCORE
oc exec deployment/document-app -- printenv MCP_RETRIEVAL_ENABLED
oc exec deployment/document-app -- printenv MCP_RETRIEVAL_FALLBACK_ENABLED
```

Expected important values:

```text
http://ollama:11434
qwen2.5:0.5b
bedrock
https://generativelanguage.googleapis.com
gemini-2.5-flash
2500
True
5
1800
700
80
0
True
False
```

Then upload a document through the app. The current workflow is:

1. Upload document.
2. Django extracts text.
3. Django calls the configured provider for suggested metadata.
4. The app redirects to the edit metadata page.
5. The loader reviews, accepts, rejects, or regenerates the AI suggestion.

Watch logs while testing:

```powershell
oc logs deployment/document-app -f
oc logs deployment/ollama -f
```

## B. Normal Code Or Config Redeploy

Use this section during normal development.

### Django Code, Template, Model, Or Migration Changes

Rebuild the application image first. OpenShift is running the last built image, not your working directory.

```powershell
oc start-build document-app --from-dir=. --follow
oc rollout restart deployment/document-app
oc rollout status deployment/document-app
```

If migrations changed, the `document-app` init container runs them automatically before Gunicorn starts.

### ConfigMap Changes

For changes to `openshift/docmanager-configmap.yaml`, apply the ConfigMap and restart Django:

```powershell
oc apply -f openshift/docmanager-configmap.yaml
oc rollout restart deployment/document-app
oc rollout status deployment/document-app
```

Examples that need this:

- Okta URLs.
- `ALLOWED_HOSTS`.
- `OLLAMA_BASE_URL`.
- `OLLAMA_MODEL`.
- `OLLAMA_TIMEOUT_SECONDS`.
- `OLLAMA_NUM_CTX`.
- `AI_METADATA_PROVIDER`.
- `GEMINI_BASE_URL`.
- `GEMINI_MODEL`.
- `AI_METADATA_MAX_CHARS`.
- `AUTO_AI_METADATA_ON_UPLOAD`.
- `AI_RAG_TOP_K`.
- `AI_RAG_MAX_CONTEXT_CHARS`.
- `AI_RAG_MAX_ANSWER_TOKENS`.
- `AI_RAG_MIN_CONTEXT_CHARS`.
- `AI_RAG_MIN_RETRIEVAL_SCORE`.
- `MCP_RETRIEVAL_ENABLED`.
- `MCP_RETRIEVAL_FALLBACK_ENABLED`.

### Gemini Provider Setup

Do not apply the whole secret template just to update Gemini. That can overwrite database credentials if the local file contains placeholders.

Set the Gemini API key directly on the existing Secret:

```powershell
oc set env secret/docmanager-secrets GEMINI_API_KEY="YOUR_REAL_GEMINI_API_KEY"
```

The template contains this placeholder for rebuild-from-scratch cases:

```yaml
GEMINI_API_KEY: "<your-real-gemini-api-key>"
```

Current Gemini ConfigMap values:

```yaml
AI_METADATA_PROVIDER: "gemini"
GEMINI_BASE_URL: "https://generativelanguage.googleapis.com"
GEMINI_MODEL: "gemini-2.5-flash"
```

If `AI_METADATA_PROVIDER` is not already `gemini`, switch the live deployment with `oc set env`:

```powershell
oc set env deployment/document-app AI_METADATA_PROVIDER=gemini
oc rollout status deployment/document-app
```

Verify:

```powershell
oc exec deployment/document-app -- printenv AI_METADATA_PROVIDER
oc exec deployment/document-app -- printenv GEMINI_MODEL
oc exec deployment/document-app -- printenv GEMINI_BASE_URL
```

### Switch Back To Ollama

Use this when you want local/private AI processing:

```powershell
oc set env deployment/document-app AI_METADATA_PROVIDER=ollama
oc rollout status deployment/document-app
```

Make sure Ollama is running and the model is available:

```powershell
oc rollout status deployment/ollama
oc exec deployment/ollama -- ollama list
oc exec deployment/ollama -- ollama run qwen2.5:0.5b "Say OK"
```

### Ollama Model Changes

If `OLLAMA_MODEL` changes, apply the ConfigMap, recreate the pull job, and restart Django:

```powershell
oc apply -f openshift/docmanager-configmap.yaml

oc delete job ollama-pull-model --ignore-not-found
oc apply -f openshift/ollama-model-pull-job.yaml
oc logs job/ollama-pull-model -f

oc rollout restart deployment/document-app
oc rollout status deployment/document-app
```

Verify:

```powershell
oc exec deployment/ollama -- ollama list
oc exec deployment/document-app -- printenv OLLAMA_MODEL
```

### Ollama Resource Changes

If `openshift/ollama-deployment.yaml` changes:

```powershell
oc apply -f openshift/ollama-deployment.yaml
oc rollout status deployment/ollama
```

If CRC gets stuck with an old running pod and a new pending pod, reset Ollama cleanly:

```powershell
oc scale deployment/ollama --replicas=0
oc wait --for=delete pod -l app=ollama --timeout=120s
oc scale deployment/ollama --replicas=1
oc rollout status deployment/ollama
```

## C. Stop CRC And Start CRC Then Check App

Use this path for normal local shutdown and startup.

### 1. Stop And Start CRC

```powershell
crc stop
crc start
```

> [!CAUTION]
> `crc stop` is safe for normal shutdown. `crc delete` destroys the local CRC VM and can remove local cluster data.

### 2. Select The Project

```powershell
oc project docmanager
oc project -q
```

### 3. Check The App

```powershell
oc get pods
oc get svc
oc get route
```

If the app needs a fresh pod after CRC starts:

```powershell
oc rollout restart deployment/document-app
oc rollout status deployment/document-app
```

Start port-forward:

```powershell
oc port-forward svc/document-app 8080:80
```

In another PowerShell window:

```powershell
curl.exe -v http://localhost:8080/
curl.exe -v http://localhost:8080/login/
```

Watch logs while testing:

```powershell
oc logs deployment/document-app -f
```

## D. PostgreSQL And OpenSearch Checks

PostgreSQL is the only supported application database backend. OpenSearch is
the derived retrieval tier for keyword and vector search.

Verify PostgreSQL:

```powershell
oc apply -f openshift/postgresql-pvc.yaml
oc apply -f openshift/postgresql-deployment.yaml
oc rollout status deployment/postgresql
oc get pods -l app=postgresql
oc get svc postgresql
oc exec deployment/postgresql -- psql -U docuser -d document_management -c "select version();"
oc exec deployment/document-app -- python -c "import socket; print(socket.gethostbyname('postgresql'))"
```

Verify Django is using PostgreSQL:

```powershell
oc exec deployment/document-app -- python manage.py shell -c "from django.conf import settings; print(settings.DATABASES['default']['ENGINE']); print(settings.DATABASES['default']['HOST']); print(settings.DATABASES['default']['PORT'])"
```

Expected:

```text
django.db.backends.postgresql
postgresql
5432
```

Verify OpenSearch:

```powershell
oc apply -f openshift/opensearch-pvc.yaml
oc apply -f openshift/opensearch-deployment.yaml
oc rollout status deployment/opensearch
oc get pods -l app=opensearch
oc get svc opensearch
oc exec deployment/document-app -- python -c "from documents.opensearch_indexing import get_opensearch_client; print(get_opensearch_client().info())"
```

Rebuild OpenSearch indexes from PostgreSQL:

```powershell
oc exec deployment/document-app -- python manage.py reindex_opensearch --create-indexes
```

Run the combined AI Search health report:

```powershell
oc exec deployment/document-app -- python manage.py health_ai_search
oc exec deployment/document-app -- python manage.py health_ai_search --skip-bedrock
```

Verify MCP retrieval from the deployed app:

```powershell
oc get deployment document-app -o jsonpath="{.spec.template.spec.containers[0].image}{'\n'}"
oc exec deployment/document-app -- python manage.py shell -c "from django.conf import settings; print(settings.MCP_RETRIEVAL_ENABLED, settings.MCP_RETRIEVAL_FALLBACK_ENABLED)"
oc exec deployment/document-app -- python manage.py health_mcp_retrieval
```

Expected image and flags:

```text
docker.io/khalique/document-app:2.6-metadata-quality-batch
True False
```

Use a settings-only MCP check when you do not want to call Bedrock or
OpenSearch:

```powershell
oc exec deployment/document-app -- python manage.py health_mcp_retrieval --skip-live
```

Print the raw MCP response for detailed troubleshooting:

```powershell
oc exec deployment/document-app -- python manage.py health_mcp_retrieval --json
```

## E. Some Troubleshooting Tips

### Quick Checks

```powershell
oc get pods
oc get svc
oc get route
oc get events --sort-by=.lastTimestamp
```

### Implementation Checks

```powershell
oc get deployment document-app -o jsonpath="{.spec.template.spec.containers[0].image}"
oc get deployment ollama -o jsonpath="{.spec.strategy.type}"
oc get configmap docmanager-config -o yaml
oc get secret docmanager-secrets
```

### Common Issues

| Symptom | Check |
| --- | --- |
| Pod shows `InvalidImageName` | Look for an unreplaced project placeholder in the deployment or migration job image path. |
| OpenSearch connection refused | Confirm `deployment/opensearch` is running, then run `reindex_opensearch --create-indexes`. |
| AI Search has no embeddings or unclear provider/index state | Run `python manage.py health_ai_search` from `deployment/document-app`. |
| Ask Documents works only when fallback is enabled | MCP retrieval is failing; set `MCP_RETRIEVAL_FALLBACK_ENABLED=False`, rerun `health_mcp_retrieval --json`, and inspect the returned error code. |
| MCP smoke test returns `permission_context_missing` | Include a role such as `viewer`, `loader`, or `admin`, or an allowed Okta group in `user_context`. |
| MCP smoke test returns `retrieval_unavailable` | Check `deployment/opensearch`, run `health_ai_search --skip-bedrock`, then rebuild indexes with `reindex_opensearch --create-indexes`. |
| MCP smoke test returns `embedding_provider_error` | Check AWS credentials, `AWS_REGION`, Bedrock model access, and `BEDROCK_EMBED_MODEL_ID`. |
| UI Ask Documents returns low-context answer | Import documents, rebuild embeddings, reindex OpenSearch, and ask a question covered by the uploaded content. |
| Probe returns `HTTP 400` | Make sure the probe `Host` header is listed in `ALLOWED_HOSTS`. |
| Login works but `/` returns `500` | Check app logs and confirm migrations ran successfully. |
| Migration files changed | Rebuild the app image before restarting the deployment. |
| Upload code changes do not appear in the browser | Rebuild with `oc start-build document-app --from-dir=. --follow`, then restart `deployment/document-app`. |
| AI suggestions are not generated during upload | Confirm `AUTO_AI_METADATA_ON_UPLOAD=True`, restart `document-app`, and check `deployment/document-app` logs. |
| Gemini says API key is not configured | Add `GEMINI_API_KEY` to `docmanager-secrets`, then restart `deployment/document-app`. |
| Gemini returns HTTP 403 or 400 | Confirm the API key is valid and `GEMINI_MODEL` is available in your Gemini models list. |
| PowerShell fails on `oc patch ... -p '{"data":...}'` | Use a temporary JSON patch file with `--patch-file`. |
| Applying the secret template changes provider credentials | Restore the affected secret values; prefer `oc set env secret/docmanager-secrets GEMINI_API_KEY=...` for Gemini updates. |
| Django still uses the old Ollama model | Restart `deployment/document-app` after applying the ConfigMap. |
| Ollama pull job says `couldn't find key OLLAMA_MODEL in ConfigMap` | Reapply `openshift/docmanager-configmap.yaml`, then delete and recreate `job/ollama-pull-model`. |
| Ollama pod says `Insufficient memory` or `model requires more system memory` | Use the smaller default `qwen2.5:0.5b`, reapply the config map and Ollama deployment, then recreate `job/ollama-pull-model`. |
| Ollama rollout has one running old pod and one pending new pod | Scale `deployment/ollama` to zero, wait for pods to delete, then scale back to one. |
| Ollama returns HTTP 500 to Django | Run `oc logs deployment/ollama --since=2m` and test direct generation with `oc exec deployment/ollama -- ollama run qwen2.5:0.5b "Say OK"`. |
| OpenSearch Dashboards is `CrashLoopBackOff` with `opensearch-dashboards-docker-entrypoint.sh: Permission denied` | Grant `anyuid` to `opensearch-dashboards`, restart the deployment, and confirm the new ReplicaSet reaches `1/1 Running`. |

### Logs And Details

```powershell
oc logs deployment/document-app --tail=200
oc logs deployment/ollama --tail=200
oc describe pod -l app=document-app
oc describe pod -l app=ollama
oc get route document-app -o jsonpath="{.spec.host}"
```

### Useful AI Debug Commands

```powershell
oc exec deployment/document-app -- printenv OLLAMA_BASE_URL
oc exec deployment/document-app -- printenv OLLAMA_MODEL
oc exec deployment/document-app -- printenv AI_METADATA_PROVIDER
oc exec deployment/document-app -- printenv MCP_RETRIEVAL_ENABLED
oc exec deployment/document-app -- printenv MCP_RETRIEVAL_FALLBACK_ENABLED
oc exec deployment/document-app -- printenv GEMINI_BASE_URL
oc exec deployment/document-app -- printenv GEMINI_MODEL
oc exec deployment/document-app -- printenv AUTO_AI_METADATA_ON_UPLOAD
oc exec deployment/document-app -- python manage.py health_mcp_retrieval --skip-live
oc exec deployment/document-app -- python manage.py health_mcp_retrieval --json
oc exec deployment/ollama -- ollama list
oc exec deployment/ollama -- ollama run qwen2.5:0.5b "Say OK"
```

### Fallback Migration Job

Run this only when the deployment init container did not complete migrations:

```powershell
oc delete job docmanager-migrate
oc apply -f openshift/docmanager-migrate-job.yaml
oc wait --for=condition=complete job/docmanager-migrate --timeout=180s
oc logs job/docmanager-migrate
```

### Data Safety

| Do Not Delete | Unless You Intend To Reset |
| --- | --- |
| `postgresql-pvc` | Canonical document database data |
| `docmanager-media-pvc` | Uploaded media files |
| `opensearch-pvc` | Derived search/vector indexes |
| `ollama-models-pvc` | Downloaded Ollama models |

## F. Issue Log

### OpenSearch Dashboards CrashLoopBackOff On CRC

Observed symptom:

```text
exec container process `/usr/share/opensearch-dashboards/./opensearch-dashboards-docker-entrypoint.sh`: Permission denied
```

Root cause:

OpenShift CRC admitted the pod under the restricted SCC with a namespace-assigned random UID. The upstream `opensearchproject/opensearch-dashboards:3.3.0` image could not execute or read its entrypoint under that UID. The fixed manifest runs the container as UID/GID `1000`, which requires the `anyuid` SCC.

Recovery process:

```powershell
& crc oc-env | Invoke-Expression
crc console --credentials
oc login -u kubeadmin -p <password-from-crc> https://api.crc.testing:6443
oc project docmanager

oc adm policy add-scc-to-user anyuid -z opensearch-dashboards -n docmanager
oc apply -f openshift/opensearch-dashboards-deployment.yaml
oc rollout restart deployment/opensearch-dashboards
oc rollout status deployment/opensearch-dashboards
```

Validation:

```powershell
oc get pods -l app=opensearch-dashboards -o wide
oc get deployment opensearch-dashboards
oc logs deployment/opensearch-dashboards --tail=100
```

Expected result:

```text
deployment "opensearch-dashboards" successfully rolled out
opensearch-dashboards   READY 1/1   AVAILABLE 1
```
