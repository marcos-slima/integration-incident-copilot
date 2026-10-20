#!/usr/bin/env bash
# Cenario G (docs/DEPLOYMENT.md): valida o bundle deploy/kyma num Kubernetes
# real local (k3d), via o overlay deploy/local. Gera evidencia em
# reports/k3d-validation-<timestamp>.log.
#
# Uso:
#   scripts/k3d-validate.sh               # cria cluster, build, aplica, testa
#   scripts/k3d-validate.sh --skip-build  # reusa integration-incident-copilot:local
#   scripts/k3d-validate.sh --delete      # remove o cluster e sai
#
# Pre-requisitos: docker, k3d (>=5), kubectl, Ollama no host ouvindo em
# interface acessivel por containers (o mesmo requisito do docker compose,
# que usa host.docker.internal), com o modelo do configmap-local.yaml.
set -euo pipefail

CLUSTER="${K3D_CLUSTER:-iic}"
NS="integration-incident-copilot"
IMAGE="integration-incident-copilot:local"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$ROOT/reports/k3d-validation-$TS.log"
PF_PORT="${PF_PORT:-18000}"
SKIP_BUILD=0

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --delete) k3d cluster delete "$CLUSTER"; exit 0 ;;
    *) echo "argumento desconhecido: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$ROOT/reports"
exec > >(tee -a "$LOG") 2>&1
step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nFALHOU: %s\n' "$*"; exit 1; }

step "0. Pre-requisitos"
for bin in docker k3d kubectl curl; do
  command -v "$bin" >/dev/null || fail "'$bin' nao encontrado no PATH"
done
k3d version; kubectl version --client
curl -fsS --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null \
  || fail "Ollama nao responde em 127.0.0.1:11434"
if command -v ss >/dev/null && ss -ltn | grep -qE '127\.0\.0\.1:11434'; then
  echo "AVISO: Ollama ouve so em 127.0.0.1 - Pods nao alcancam. Use OLLAMA_HOST=0.0.0.0 no servico do Ollama."
fi

step "1. Cluster k3d '$CLUSTER'"
if ! k3d cluster list -o json | grep -q "\"name\":\"$CLUSTER\""; then
  k3d cluster create "$CLUSTER" --agents 0 --wait \
    --k3s-arg "--disable=traefik@server:0"
fi
kubectl config use-context "k3d-$CLUSTER"
kubectl get nodes -o wide

step "2. Imagem $IMAGE"
if [ "$SKIP_BUILD" -eq 0 ]; then
  docker build -t "$IMAGE" "$ROOT"
fi
k3d image import "$IMAGE" -c "$CLUSTER"

step "3. Namespace e Secret (valores aleatorios, nunca gravados em disco)"
kubectl apply -f "$ROOT/deploy/kyma/namespace.yaml"
# O Secret e criado UMA vez por cluster. Regerar a cada execucao trocava a
# REDIS_PASSWORD so para os Pods novos: o Redis em execucao seguia com a
# senha antiga, o /ready dos Pods novos dava 503 e o worker entrava em
# CrashLoopBackOff. Para trocar as chaves: --delete e recriar o cluster.
SECRET=integration-incident-copilot-secrets
if ! kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
  rnd() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }
  REDIS_PASSWORD="$(rnd)"
  kubectl -n "$NS" create secret generic "$SECRET" \
    --from-literal=API_KEY="$(rnd)" \
    --from-literal=A2A_API_KEY="$(rnd)" \
    --from-literal=EVENT_MESH_API_KEY="$(rnd)" \
    --from-literal=REDIS_PASSWORD="$REDIS_PASSWORD" \
    --from-literal=REDIS_URL="redis://:$REDIS_PASSWORD@redis:6379/0"
  echo "Secret criado"
else
  echo "Secret existente reutilizado"
fi
API_KEY_VALUE="$(kubectl -n "$NS" get secret "$SECRET" -o jsonpath='{.data.API_KEY}' | base64 -d)"

step "4. kubectl apply -k deploy/local (base = deploy/kyma)"
kubectl kustomize "$ROOT/deploy/local" > "$ROOT/reports/k3d-rendered-$TS.yaml"
grep -q "kind: APIRule" "$ROOT/reports/k3d-rendered-$TS.yaml" && fail "APIRule nao foi removido pelo overlay"
kubectl apply -k "$ROOT/deploy/local"

step "5. host.docker.internal -> IP do host (hostAliases)"
HOST_IP="$(kubectl -n kube-system get configmap coredns -o jsonpath='{.data.NodeHosts}' | awk '/host.k3d.internal/{print $1; exit}')"
[ -n "$HOST_IP" ] || fail "nao achei host.k3d.internal no CoreDNS do k3d"
echo "host.docker.internal = $HOST_IP"
for d in integration-incident-copilot integration-incident-copilot-worker; do
  kubectl -n "$NS" patch deployment "$d" --type=merge -p \
    "{\"spec\":{\"template\":{\"spec\":{\"hostAliases\":[{\"ip\":\"$HOST_IP\",\"hostnames\":[\"host.docker.internal\"]}]}}}}"
done

# A tag e sempre :local - um `apply` sem mudanca de spec nao recria o Pod, e
# o Pod antigo seguiria com a imagem anterior. Restart forca o Pod novo a
# usar a imagem recem-importada.
kubectl -n "$NS" rollout restart deployment/integration-incident-copilot deployment/integration-incident-copilot-worker

diagnose() {
  # Pods que NAO estao prontos: e neles que esta a causa (o `logs deployment/x`
  # escolhe um Pod qualquer, em geral o antigo e saudavel).
  kubectl -n "$NS" get pods -o wide
  kubectl -n "$NS" get hpa || true
  for p in $(kubectl -n "$NS" get pods -l "app.kubernetes.io/name=$1" \
      -o jsonpath='{range .items[?(@.status.containerStatuses[0].ready==false)]}{.metadata.name}{" "}{end}'); do
    echo "--- pod nao pronto: $p"
    kubectl -n "$NS" describe pod "$p" | sed -n '/^Containers:/,/^Volumes:/p;/^Events:/,$p'
    kubectl -n "$NS" logs "$p" --tail=120 || true
    kubectl -n "$NS" logs "$p" --previous --tail=60 2>/dev/null || true
  done
}

step "6. Rollout"
for d in qdrant redis integration-incident-copilot integration-incident-copilot-worker; do
  kubectl -n "$NS" rollout status "deployment/$d" --timeout=600s \
    || { diagnose "$d"; fail "rollout de $d"; }
done
kubectl -n "$NS" get pods,svc,hpa -o wide
echo "imagem em execucao:"
kubectl -n "$NS" get pods -l app.kubernetes.io/name=integration-incident-copilot \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}'
docker image inspect "$IMAGE" --format 'imagem local: {{.Id}}'
kubectl -n "$NS" exec deployment/integration-incident-copilot -- sh -c 'id; ls -ld /app/data'


step "7. Indexacao da base de incidentes (sample_docs da imagem)"
kubectl -n "$NS" exec deployment/integration-incident-copilot -- \
  python -m app.rag.ingest --target incidents --reset-state

step "8. Smoke test via port-forward :$PF_PORT"
kubectl -n "$NS" port-forward svc/integration-incident-copilot "$PF_PORT:80" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$PF_PORT/health" >/dev/null 2>&1 && break; sleep 1; done
URL="http://127.0.0.1:$PF_PORT"

echo "--- GET /health";  curl -fsS "$URL/health"; echo
echo "--- GET /ready";   curl -sS -w '\nHTTP %{http_code}\n' "$URL/ready"
echo "--- POST /diagnose sem chave (esperado 401)"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/diagnose" -H 'Content-Type: application/json' -d '{"description":"x"}')"
echo "HTTP $code"; [ "$code" = "401" ] || fail "/diagnose sem chave retornou $code"

echo "--- POST /diagnose IDoc 51 (rule engine) e Salesforce sem erro visivel (LLM local; nenhuma regra casa)"
for body in \
  '{"description":"IDoc ORDERS05 parado com status 51 no S/4HANA","interface_type":"odata"}' \
  '{"description":"Pedido criado no Salesforce nao aparece no SAP SD e nenhum erro foi exibido ao usuario","interface_type":"salesforce"}'; do
  curl -sS --max-time 240 -w '\nHTTP %{http_code}\n' -X POST "$URL/diagnose" \
    -H "X-API-Key: $API_KEY_VALUE" -H 'Content-Type: application/json' -d "$body" \
    | python3 -c 'import json,sys; raw=sys.stdin.read(); body,_,code=raw.rpartition("\nHTTP "); d=json.loads(body); print(json.dumps({k:d.get(k) for k in ("probable_root_cause","diagnosis_confidence","llm_provider_used","agent_domain","matched_source")}, ensure_ascii=False)); print("HTTP", code.strip())'
done

step "9. HPA e eventos"
kubectl -n "$NS" get hpa
kubectl -n "$NS" get events --sort-by=.lastTimestamp | tail -20

printf '\nOK: cenario G validado. Evidencia: %s\n' "$LOG"
echo "Cluster mantido. Para remover: scripts/k3d-validate.sh --delete"
