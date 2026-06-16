#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
YAML_DIR="${BASE_DIR}/yaml"

echo "===== Checking yaml directory ====="
echo "SCRIPT_DIR=${SCRIPT_DIR}"
echo "BASE_DIR=${BASE_DIR}"
echo "YAML_DIR=${YAML_DIR}"

if [ ! -d "$YAML_DIR" ]; then
  echo "ERROR: $YAML_DIR directory not found"
  exit 1
fi

apply_file() {
  local file="$1"
  local label="$2"

  echo
  echo "===== ${label} ====="

  if [ ! -f "$file" ]; then
    echo "ERROR: Missing file: $file"
    exit 1
  fi

  kubectl apply -f "$file"
}

apply_file "$YAML_DIR/docmanager-configmap.yaml" "running application config map"
apply_file "$YAML_DIR/docmanager-secret.local.yaml" "running application secret"
apply_file "$YAML_DIR/postgresql-pvc.yaml" "running application postgres PVC"
apply_file "$YAML_DIR/postgresql-deployment.yaml" "running application postgres deployment"
apply_file "$YAML_DIR/docmanager-pvc.yaml" "running application PVC"
apply_file "$YAML_DIR/docmanager-deployment.yaml" "running application deployment"
apply_file "$YAML_DIR/cloudflare-job-update.yaml" "running cloudflare update"
apply_file "$YAML_DIR/aws-khalique-us-ingress.yaml" "running aws-khalique-us ingress"

echo
echo "===== Waiting for deployments ====="

kubectl rollout status deployment/postgresql --timeout=180s || true
kubectl rollout status deployment/document-app --timeout=300s || true

echo
echo "===== Current status ====="

kubectl get pods -o wide
kubectl get svc
kubectl get pvc
kubectl get ingress

echo
echo "===== DONE ====="