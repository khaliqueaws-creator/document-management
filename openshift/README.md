# OpenShift CRC Runbook

Deploy, restart, and troubleshoot the Django document manager on local OpenShift CRC.

> [!IMPORTANT]
> Do not commit real production secret values to GitHub. If `openshift/docmanager-secret-template.yaml` contains live values, keep it local or replace them with placeholders before committing.

## Contents

| Section | Use When |
| --- | --- |
| [A. Rebuild From Scratch](#a-rebuild-from-scratch) | CRC was recreated, the project was removed, or you want a clean local deployment. |
| [B. Stop CRC And Start CRC Then Check App](#b-stop-crc-and-start-crc-then-check-app) | You are shutting down or restarting your existing local CRC environment. |
| [C. Some Troubleshooting Tips](#c-some-troubleshooting-tips) | Pods, routes, migrations, probes, or login checks are failing. |

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
$RootDbPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})

$SecretKey
$DbPassword
$RootDbPassword
```

Then update:

| File | What To Check |
| --- | --- |
| `openshift/docmanager-secret-template.yaml` | Django secret key, Okta client values, database name, user, passwords, host, and port. |
| `openshift/docmanager-configmap.yaml` | Route host, Okta issuer, callback URL, logout URL, and `ALLOWED_HOSTS`. |
| `openshift/docmanager-deployment.yaml` | Probe `Host` header. It must also appear in `ALLOWED_HOSTS`. |

### 3. Build The App Image

Create the binary Docker build if it does not exist:

```powershell
oc new-build --name=document-app --binary --strategy=docker
```

Build from the repository root:

```powershell
oc start-build document-app --from-dir=. --follow
```

Verify:

```powershell
oc get imagestream document-app
oc get istag document-app:latest
```

> [!NOTE]
> If the project name is not `docmanager`, update the image path in `openshift/docmanager-deployment.yaml` and `openshift/docmanager-migrate-job.yaml`.

### 4. Apply Resources

Apply MySQL resources first. MySQL reads database values from `docmanager-secrets`.

```powershell
oc apply -f openshift/docmanager-secret-template.yaml
oc apply -f openshift/mysql-pvc.yaml
oc apply -f openshift/mysql-deployment.yaml
oc rollout status deployment/mysql
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

### 5. Verify The App

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

## B. Stop CRC And Start CRC Then Check App

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

## C. Some Troubleshooting Tips

### Quick Checks

```powershell
oc get pods
oc get svc
oc get route
oc get events --sort-by=.lastTimestamp
```

### Common Issues

| Symptom | Check |
| --- | --- |
| Pod shows `InvalidImageName` | Look for an unreplaced project placeholder in the deployment or migration job image path. |
| MySQL is not reachable | Confirm `oc get svc mysql`, `oc get pods`, and `DB_HOST: "mysql"` in the secret. |
| Probe returns `HTTP 400` | Make sure the probe `Host` header is listed in `ALLOWED_HOSTS`. |
| Login works but `/` returns `500` | Check app logs and confirm migrations ran successfully. |
| Migration files changed | Rebuild the app image before restarting the deployment. |

### Logs And Details

```powershell
oc logs deployment/document-app --tail=200
oc describe pod -l app=document-app
oc get route document-app -o jsonpath="{.spec.host}"
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
| `mysql-pvc` | Stored local database data |
| `docmanager-media-pvc` | Uploaded media files |

