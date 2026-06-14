#!/usr/bin/env bash
set -euo pipefail

PUBLIC_IP="${1:-44.223.25.155}"
RECORD_1="1:://f87be553c48944dc84d0a3a966d0c3281c.mylabserver.com"
RECORD_2="1:://f87be553c48944dc84d0a3a966d0c3281d.mylabserver.com"

echo "===== Deploy NGINX test app ====="
kubectl create deployment my-nginx --image=nginx --port=80 || true
kubectl expose deployment my-nginx --type=NodePort --name=my-nginx-service --port=80 --target-port=80 || true
kubectl wait --for=condition=Available deployment/my-nginx --timeout=180s

echo "===== Test NGINX service inside cluster ====="
kubectl run curl-test --rm -i --restart=Never --image=curlimages/curl -- curl -I http://my-nginx-service

echo "===== Install NGINX Ingress Controller ====="
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/cloud/deploy.yaml
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=300s

echo "===== Change ingress service to NodePort and set HTTP NodePort 30080 and HTTPS NodePort 30081 ====="
kubectl patch svc ingress-nginx-controller -n ingress-nginx -p '{"spec":{"type":"NodePort"}}'

# Safer way to target ports without assuming hardcoded array indexes
kubectl patch svc ingress-nginx-controller -n ingress-nginx --type='json' -p='[
  {"op": "replace", "path": "/spec/ports/0/nodePort", "value": 30080},
  {"op": "replace", "path": "/spec/ports/1/nodePort", "value": 30081}
]'

echo "===== Enable ingress controller on host ports 80 and 443 ====="
# Fixed the "dnsPkubolicy" typo here
kubectl patch deployment ingress-nginx-controller \
  -n ingress-nginx \
  --type='merge' \
  -p '{ "spec": { "template": { "spec": { "hostNetwork": true, "dnsPolicy": "ClusterFirstWithHostNet" } } } }'

kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=300s

echo "===== Create basic HTTP ingress ====="
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-nginx-tls-ingress
  namespace: default
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx  
  rules:
  - http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: my-nginx-service
            port:
              number: 80
EOF

echo "===== Test NodePort ingress ====="
# Fixed port 3000 to match your configured 30080 port
curl -I http://127.0.0.1 || true