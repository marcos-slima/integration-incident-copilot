#!/usr/bin/env bash
# =============================================================================
# start-docker.sh — Inicia o Integration Incident Copilot em modo CONTAINER
#
# O que roda em Docker:  api, Qdrant (compose deste repo); Neo4j se
#                        GRAPH_RAG_ENABLED=true (profile graphrag);
#                        PostgreSQL IIC, Grafana (profile observability)
#
# Validacao 2026-10-07 (M-18): o script dependia de $HOME/ai-stack (um stack
# pessoal que nao existe em outra maquina), rodava `uv run` dentro do
# container (a imagem nao tem uv) e o passo de ingestao tinha uma barra
# invertida seguida de comentario - o bash executava `docker compose run`
# sem comando e o resto como comandos no HOST.
# O que roda no HOST:    Ollama (acesso à GPU local para inferência rápida)
#
# Uso:
#   ./scripts/start-docker.sh                    # start completo (recomendado)
#   ./scripts/start-docker.sh --build            # rebuild da imagem antes
#   ./scripts/start-docker.sh --skip-ingest      # pula reindexação do RAG
#   ./scripts/start-docker.sh --no-observability # sem Grafana/PostgreSQL IIC
#   ./scripts/start-docker.sh --reset-rag        # A-06: recria collection RAG (destructivo — explicito)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKIP_INGEST=false
NO_OBS=false
BUILD=false
RESET_RAG=false  # A-06: reset da collection RAG (--reset-rag)

for arg in "$@"; do
  case $arg in
    --skip-ingest)        SKIP_INGEST=true ;;
    --no-observability)   NO_OBS=true ;;
    --build)              BUILD=true ;;
    --reset-rag)          RESET_RAG=true ;;  # A-06: reset explicito da collection RAG
  esac
done

log()  { echo -e "\033[1;34m[IIC]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[OK ]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WRN]\033[0m $*"; }
die()  { echo -e "\033[1;31m[ERR]\033[0m $*" >&2; exit 1; }

wait_http() {
  local url=$1 label=$2 retries=${3:-30}
  for i in $(seq 1 "$retries"); do
    if curl -sf "$url" >/dev/null 2>&1; then ok "$label disponível"; return 0; fi
    sleep 3
  done
  warn "$label não respondeu em $((retries * 3))s — verifique: docker compose logs"
}

# =============================================================================
log "Integration Incident Copilot — START (modo container)"
echo ""
echo "  HOST    → Ollama (inferência com GPU local)"
echo "  DOCKER  → API, Qdrant, (Neo4j), PostgreSQL IIC, Grafana"
echo ""

# --- 1. Pré-requisitos -------------------------------------------------------
log "1/6 Verificando pré-requisitos..."
command -v docker >/dev/null || die "docker não encontrado."
command -v ollama >/dev/null || warn "ollama não encontrado — inferência pode falhar se LLM_PROVIDER=ollama"
[[ -f "$PROJECT_DIR/.env" ]] || die ".env não encontrado. Execute: cp .env.example .env e preencha."
ok "Pré-requisitos OK"

# --- 2. Ollama no host (acesso à GPU) ----------------------------------------
log "2/6 Verificando Ollama no host..."
if command -v ollama >/dev/null 2>&1; then
  if ! pgrep -x ollama >/dev/null 2>&1; then
    log "Iniciando Ollama em background..."
    nohup ollama serve >/tmp/ollama.log 2>&1 &
    sleep 3
  fi
  wait_http "http://localhost:11434" "Ollama"

  for model in "qwen3-coder-next:latest" "nomic-embed-text"; do
    if ! ollama list 2>/dev/null | grep -q "${model%%:*}"; then
      warn "Modelo '$model' não encontrado — baixando..."
      ollama pull "$model"
    else
      ok "Modelo '$model' presente"
    fi
  done
else
  warn "Ollama não instalado — containers usarão LLM_PROVIDER do .env"
fi

# --- 3. Infraestrutura Docker (compose deste repo) --------------------------
log "3/6 Subindo infraestrutura — Qdrant (e Neo4j se GRAPH_RAG_ENABLED=true)..."
set -a; source "$PROJECT_DIR/.env"; set +a
(cd "$PROJECT_DIR" && docker compose up -d qdrant)
wait_http "http://localhost:${QDRANT_HOST_PORT:-6333}/healthz" "Qdrant"
if [[ "${GRAPH_RAG_ENABLED:-false}" == "true" ]]; then
  (cd "$PROJECT_DIR" && docker compose --profile graphrag up -d neo4j)
  wait_http "http://localhost:7474" "Neo4j"
fi

# --- 4. Observabilidade: PostgreSQL IIC + Grafana ----------------------------
if [[ "$NO_OBS" == "true" ]]; then
  log "4/6 Observabilidade desativada (--no-observability)"
else
  log "4/6 Subindo observabilidade — PostgreSQL IIC + Grafana (Docker)..."
  (cd "$PROJECT_DIR" && docker compose --profile observability up -d postgres grafana)
  wait_http "http://localhost:3001" "Grafana"

  # Migrações via container (DATABASE_URL aponta para host; substituímos por postgres)
  log "Verificando migrações Alembic..."
  if [[ -n "${DATABASE_URL:-}" ]]; then
    CONTAINER_URL=$(echo "$DATABASE_URL" | sed 's|@localhost|@postgres|g;s|@127\.0\.0\.1|@postgres|g')
    (cd "$PROJECT_DIR" && \
      docker compose --profile observability run --rm \
        -e DATABASE_URL="$CONTAINER_URL" \
        --entrypoint /app/.venv/bin/alembic api upgrade head) && ok "Migrações aplicadas"
  else
    warn "DATABASE_URL não definida no .env — migrações puladas"
  fi
fi

# --- 5. Copilot API (container) ----------------------------------------------
log "5/6 Subindo Copilot API (container)..."
cd "$PROJECT_DIR"

if [[ "$BUILD" == "true" ]]; then
  log "Build da imagem Docker (--build)..."
  docker compose build api
fi

docker compose up -d api
wait_http "http://localhost:8000/health" "Copilot API" 40

# --- 6. Base de conhecimento (RAG) -------------------------------------------
if [[ "$SKIP_INGEST" == "true" ]]; then
  log "6/6 Ingest pulado (--skip-ingest)"
else
  log "6/6 Indexando base de conhecimento (RAG) no container..."
  # A-06: --reset só com a flag explícita --reset-rag.
  INGEST_FLAGS=(--target incidents)
  if [[ "$RESET_RAG" == "true" ]]; then
    INGEST_FLAGS+=(--reset)
    warn "RAG reset solicitado — collection sera recriada"
  fi
  docker compose run --rm --entrypoint /app/.venv/bin/python api -m app.rag.ingest "${INGEST_FLAGS[@]}"
  ok "Ingest concluído"
fi

# --- Status final ------------------------------------------------------------
echo ""
echo "  ┌─────────────────────────────────────────────────────────────────┐"
echo "  │  DOCKER (Copilot)                                               │"
echo "  │    Copilot UI / API  → http://localhost:8000                   │"
echo "  │    API docs (Swagger)→ http://localhost:8000/docs              │"
echo "  │  DOCKER (Infra)                                                 │"
if [[ "$NO_OBS" == "false" ]]; then
echo "  │    Grafana           → http://localhost:3001  admin/<GRAFANA_PW>│"
echo "  │    PostgreSQL IIC    → localhost:5432                          │"
fi
echo "  │    Qdrant dashboard  → http://localhost:6333/dashboard         │"
if [[ "${GRAPH_RAG_ENABLED:-false}" == "true" ]]; then
echo "  │    Neo4j browser     → http://localhost:7474  neo4j/<NEO4J_PW> │"
fi
echo "  │  HOST                                                           │"
echo "  │    Ollama            → http://localhost:11434                  │"
echo "  └─────────────────────────────────────────────────────────────────┘"
echo ""
log "Logs: docker compose logs -f api"
log "Stop: docker compose --profile observability --profile graphrag down"
