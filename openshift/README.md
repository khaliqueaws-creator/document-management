# OpenShift CRC Deployment Runbook

These manifests deploy the Django document manager to OpenShift CRC using:

- a Docker image built from the repository `Dockerfile`
- an OpenShift `ConfigMap` for non-sensitive settings
- an OpenShift `Secret` for Django, Okta, and database secrets
- a PVC mounted at `/app/media`
- a one-time migration `Job`
- a `Deployment` running Gunicorn
- a `Service` and `Route` to expose the app

## Files

- `docmanager-configmap.yaml`: non-sensitive environment variables
- `docmanager-secret-template.yaml`: sensitive values such as `DJANGO_SECRET_KEY`, Okta client secret, and DB password
- `docmanager-pvc.yaml`: persistent media storage mounted at `/app/media`
- `docmanager-deployment.yaml`: app deployment using Gunicorn, env refs, PVC, and health probes
- `docmanager-service.yaml`: service exposing the app inside the project on port 80
- `docmanager-route.yaml`: external OpenShift route for browser access
- `docmanager-migrate-job.yaml`: one-time job to run Django migrations

Do not commit real production secret values to GitHub. If `docmanager-secret-template.yaml` contains real values, keep the file local or replace them with placeholders before committing.

## Rebuild From Scratch

Use this section if CRC was deleted, the `docmanager` project was removed, or you want to recreate everything cleanly.

### 1. Create Or Select The Project

```powershell
oc new-project docmanager
```

If the project already exists:

```powershell
oc project docmanager
```

Confirm:

```powershell
oc project -q
```

### 2. Prepare Local Secret Values

Generate a Django secret key:

```powershell
$SecretKey = python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
$SecretKey
```

Generate database passwords:

```powershell
$DbPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$RootDbPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$DbPassword
$RootDbPassword
```

Update `openshift/docmanager-secret-template.yaml` before applying it:

```yaml
DJANGO_SECRET_KEY: "<generated-django-secret>"
OKTA_CLIENT_ID: "<okta-client-id>"
OKTA_CLIENT_SECRET: "<okta-client-secret>"
DB_NAME: "document_management"
DB_USER: "docuser"
DB_PASSWORD: "<db-password>"
DB_HOST: "mysql"
DB_PORT: "3306"
```

The `DB_PASSWORD` value must match the `MYSQL_PASSWORD` value used when creating MySQL.

### 3. Build The App Image

Create the binary Docker build if it does not exist:

```powershell
oc new-build --name=document-app --binary --strategy=docker
```

Build the image from the repository root:

```powershell
oc start-build document-app --from-dir=. --follow
```

Verify:

```powershell
oc get imagestream document-app
oc get istag document-app:latest
```

### 4. Create MySQL

Create the MySQL app and service:

```powershell
oc new-app mysql:8.0 `
  -e MYSQL_DATABASE=document_management `
  -e MYSQL_USER=docuser `
  -e MYSQL_PASSWORD="$DbPassword" `
  -e MYSQL_ROOT_PASSWORD="$RootDbPassword" `
  --name=mysql
```

Wait for MySQL:

```powershell
oc rollout status deployment/mysql
oc get svc mysql
oc get pods
```

### 5. Apply Django Resources

```powershell
oc apply -f openshift/docmanager-configmap.yaml
oc apply -f openshift/docmanager-secret-template.yaml
oc apply -f openshift/docmanager-pvc.yaml
oc apply -f openshift/docmanager-service.yaml
oc apply -f openshift/docmanager-route.yaml
oc apply -f openshift/docmanager-deployment.yaml
```

Wait for the app:

```powershell
oc rollout status deployment/document-app
oc get pods
oc get svc
```

### 6. Run Migrations

```powershell
oc apply -f openshift/docmanager-migrate-job.yaml
oc wait --for=condition=complete job/docmanager-migrate --timeout=180s
oc logs job/docmanager-migrate
```

If you need to rerun the migration job after changing the image or migration files:

```powershell
oc delete job docmanager-migrate
oc apply -f openshift/docmanager-migrate-job.yaml
oc wait --for=condition=complete job/docmanager-migrate --timeout=180s
oc logs job/docmanager-migrate
```

### 7. Restart And Verify

```powershell
oc rollout restart deployment/document-app
oc rollout status deployment/document-app
oc get pods
```

Test through local port-forward:

```powershell
oc port-forward svc/document-app 8080:80
```

In another PowerShell window:

```powershell
curl.exe -v http://localhost:8080/
curl.exe -v http://localhost:8080/login/
```

Expected results:

- `/` returns `302` to `/login/`
- `/login/` returns `302` to Okta

### 8. Cloudflare Tunnel Access

Keep the OpenShift port-forward running:

```powershell
oc port-forward svc/document-app 8080:80
```

In another PowerShell window, run the tunnel:

```powershell
cloudflared tunnel --config C:\Users\UFUserAdmin\.cloudflared\config.yml run docmanager
```

The local Cloudflare config should point to the local forwarded port:

```yaml
ingress:
  - hostname: docsdemo.khalique.net
    service: http://localhost:8080
  - service: http_status:404
```

Test:

```powershell
curl.exe -vk https://docsdemo.khalique.net/
curl.exe -vk https://docsdemo.khalique.net/login/
```

Expected results:

- `/` returns `302` to `/login/`
- `/login/` returns `302` to Okta

Then complete login in the browser:

```text
https://docsdemo.khalique.net/login/
```

Watch logs while testing:

```powershell
oc logs deployment/document-app -f
```

## 1. Select The Project

```powershell
oc project docmanager
```

Confirm:

```powershell
oc project -q
```

## 2. Build The App Image

The deployment expects this image:

```text
image-registry.openshift-image-registry.svc:5000/docmanager/document-app:latest
```

If the image stream/build does not exist yet, create it from the local `Dockerfile`:

```powershell
oc new-build --name=document-app --binary --strategy=docker
oc start-build document-app --from-dir=. --follow
```

Check the image:

```powershell
oc get imagestream document-app
oc get istag document-app:latest
```

If the project name is not `docmanager`, update the image path in both:

- `openshift/docmanager-deployment.yaml`
- `openshift/docmanager-migrate-job.yaml`

Use:

```text
image-registry.openshift-image-registry.svc:5000/<project-name>/document-app:latest
```

## 3. Prepare Secrets

Generate a Django secret key from PowerShell:

```powershell
$SecretKey = python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
$SecretKey
```

Generate a database password if needed:

```powershell
$DbPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$DbPassword
```

Update `openshift/docmanager-secret-template.yaml` with real values:

```yaml
DJANGO_SECRET_KEY: "<generated-django-secret>"
OKTA_CLIENT_ID: "<okta-client-id>"
OKTA_CLIENT_SECRET: "<okta-client-secret>"
DB_NAME: "document_management"
DB_USER: "docuser"
DB_PASSWORD: "<db-password>"
DB_HOST: "mysql"
DB_PORT: "3306"
```

## 4. Prepare Config

Review `openshift/docmanager-configmap.yaml`.

Important values:

```yaml
ALLOWED_HOSTS: "document-app-document-app.apps-crc.testing,localhost,127.0.0.1,docsdemo.khalique.net"
OKTA_DOMAIN: "https://your-okta-domain.okta.com"
OKTA_ISSUER: "https://your-okta-domain.okta.com/oauth2/default"
OKTA_CALLBACK_URL: "https://your-app-route/oidc/callback"
OKTA_LOGOUT_REDIRECT_URL: "https://your-app-route/"
```

The health probes in `docmanager-deployment.yaml` send a `Host` header that must also be listed in `ALLOWED_HOSTS`.

## 5. Create MySQL

The Django app expects a database host named `mysql`.

Check whether it already exists:

```powershell
oc get svc
oc get pods
```

If there is no `mysql` service, create one:

```powershell
oc new-app mysql:8.0 `
  -e MYSQL_DATABASE=document_management `
  -e MYSQL_USER=docuser `
  -e MYSQL_PASSWORD="$DbPassword" `
  -e MYSQL_ROOT_PASSWORD="<root-db-password>" `
  --name=mysql
```

Wait for MySQL:

```powershell
oc rollout status deployment/mysql
oc get pods
```

The value used for `MYSQL_PASSWORD` must match `DB_PASSWORD` in `docmanager-secret-template.yaml`.

## 6. Apply App Resources

```powershell
oc apply -f openshift/docmanager-configmap.yaml
oc apply -f openshift/docmanager-secret-template.yaml
oc apply -f openshift/docmanager-pvc.yaml
oc apply -f openshift/docmanager-deployment.yaml
oc apply -f openshift/docmanager-service.yaml
oc apply -f openshift/docmanager-route.yaml
```

Check the app pod, service, and route:

```powershell
oc get pods
oc get svc document-app
oc get route document-app
oc describe pod -l app=document-app
```

If a pod shows `InvalidImageName`, check for an unreplaced `<your-project>` placeholder in the image path.

Note: a `Deployment` only creates pods. Browser/network access requires the separate `Service` and `Route` manifests.

## 7. Run Migrations

Jobs are immutable in Kubernetes/OpenShift. If the migration job already exists and you changed its image or environment, delete and recreate it:

```powershell
oc delete job docmanager-migrate
oc apply -f openshift/docmanager-migrate-job.yaml
```

If the job does not exist yet, `oc delete` may print a not found error. That is okay; then apply the job.

Wait and check logs:

```powershell
oc wait --for=condition=complete job/docmanager-migrate --timeout=120s
oc logs job/docmanager-migrate
```

If migration logs show `Unknown server host 'mysql'`, the MySQL service does not exist or `DB_HOST` is wrong.

If login succeeds but `/` returns `500`, check whether the database schema matches the Django model:

```powershell
oc logs deployment/document-app --tail=200
oc delete job docmanager-migrate
oc apply -f openshift/docmanager-migrate-job.yaml
oc logs job/docmanager-migrate
```

After adding or changing migration files, rebuild the image before recreating the migration job:

```powershell
oc start-build document-app --from-dir=. --follow
```

## 8. Restart And Verify The App

```powershell
oc rollout restart deployment/document-app
oc rollout status deployment/document-app
oc get pods
oc get svc
oc get route
```

Check logs:

```powershell
oc logs deployment/document-app
```

Common probe issue:

- `HTTP probe failed with statuscode: 400` usually means Django rejected the probe host.
- Make sure the probe `Host` header in `docmanager-deployment.yaml` is listed in `ALLOWED_HOSTS`.

## Useful Commands

Decode a value from the OpenShift secret:

```powershell
$Encoded = oc get secret docmanager-secrets -o jsonpath="{.data.DB_PASSWORD}"
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Encoded))
```

Update the deployment image directly:

```powershell
oc set image deployment/document-app document-app=image-registry.openshift-image-registry.svc:5000/docmanager/document-app:latest
```

View services:

```powershell
oc get svc
```

Show the application URL:

```powershell
oc get route document-app -o jsonpath="{.spec.host}"
```

View recent events:

```powershell
oc get events --sort-by=.lastTimestamp
```
