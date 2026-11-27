#!/usr/bin/env bash
set -euo pipefail
: "${BACKEND_DIGEST:?digest backend requis}"
: "${FRONTEND_DIGEST:?digest frontend requis}"
: "${GITHUB_SHA:?commit requis}"
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ ]] || exit 2
[[ "$BACKEND_DIGEST" =~ ^sha256:[a-f0-9]{64}$ && "$FRONTEND_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 2
namespace=conversations
expected=https://fce3ddcb-b9ff-49fb-8b7d-aca605272b0a.api.k8s.fr-par.scw.cloud:6443
server=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
[[ "$server" == "$expected" ]] || { echo 'Le kubeconfig ne cible pas le cluster staging attendu.' >&2; exit 2; }
kubectl -n "$namespace" get secret conversations-runtime conversations-identity scw-registry scaleway-database-ca -o name
kubectl -n "$namespace" get configmap conversations-llm-configuration -o name
helm upgrade --install conversations src/helm/conversations -n "$namespace"   -f deploy/kapsule/staging.yaml   --set-string "image.tag=sha-${GITHUB_SHA}@${BACKEND_DIGEST}"   --set-string "frontend.image.tag=sha-${GITHUB_SHA}@${FRONTEND_DIGEST}"   --atomic --wait --wait-for-jobs --timeout 10m --history-max 10
kubectl -n "$namespace" rollout status deployment/conversations-backend --timeout=120s
kubectl -n "$namespace" rollout status deployment/conversations-frontend --timeout=120s
kubectl -n "$namespace" rollout status deployment/conversations-backend-celery-default --timeout=120s
kubectl -n "$namespace" exec deployment/conversations-backend --   python -c 'import urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000/__heartbeat__", timeout=10); assert r.status==200'
echo "Conversations backend health: OK"

# Catch missing redirects before reporting the deployment as usable.
kubectl -n "$namespace" exec deployment/conversations-backend -- \
  python -c 'import sys,configurations; configurations.setup(); from django.conf import settings; assert settings.LOGIN_REDIRECT_URL == settings.LOGOUT_REDIRECT_URL == "/"; sys.stdout.write("Local login/logout redirects: OK\n")'
