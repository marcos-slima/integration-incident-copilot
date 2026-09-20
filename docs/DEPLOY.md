# Deploy em Outro Ambiente (Docker Compose) — Integration Incident Copilot

> Guia para levar o projeto, como está hoje, para um ambiente novo (cliente, servidor, outra máquina) via `docker compose`. Cobre os problemas reais encontrados ao validar esse fluxo do zero e como resolvê-los.

Para rodar localmente sem Docker (`uv run uvicorn app.main:app --reload`), veja [GETTING_STARTED.md](GETTING_STARTED.md).

---

## Pré-requisitos

| Requisito | Versão mínima | Verificar |
|---|---|---|
| Docker | 24+ | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| Node.js | 18+ | `node --version` (só para build do frontend) |
| Ollama (se local) | qualquer | `ollama --version` |

---

## 1. Clone e configure

```bash
git clone https://github.com/marcos-slima/sap-integration-copilot.git
cd sap-integration-copilot
cp .env.example .env
```

---

## 2. Build do frontend

O `Dockerfile` builda o frontend (React/Vite) a partir do código-fonte em um estágio próprio (`frontend-build`, base `node:22-slim`) e copia o resultado (`frontend/dist`) para `static/dist` no estágio final da imagem. Não há passo manual: `docker compose build` (ou `docker build .`) já builda o frontend sozinho.

Se quiser rodar o frontend fora do Docker (dev local com hot-reload), use `cd frontend && npm install && npm run dev`.

---

## 3. Decida a estratégia de LLM/Ollama

O `docker-compose.yml` sobe um serviço `ollama` próprio (containerizado). Duas situações:

### A) Ambiente novo, sem Ollama nativo instalado
Não precisa mudar nada — suba tudo junto:
```bash
docker compose up -d
```
O container `ollama` ocupa a porta 11434 e a API se conecta a ele internamente (`http://ollama:11434`, se configurado assim no compose).

### B) Já existe um Ollama nativo (systemd) rodando na máquina
Situação comum em máquinas de desenvolvimento (como a estação de estudo do autor). Sintoma: erro `failed to bind host port 0.0.0.0:11434/tcp: address already in use` ao subir o compose.

**Passo 1 — não subir o container `ollama`, usar só os outros serviços:**
```bash
docker compose up -d --no-deps qdrant api
```

**Passo 2 — garantir que o container `api` alcança o Ollama nativo do host.**
No `docker-compose.yml`, o serviço `api` precisa de:
```yaml
  api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      OLLAMA_HOST: http://host.docker.internal:11434
```

**Passo 3 — garantir que o Ollama nativo aceita conexões vindas do bridge do Docker.**
Por padrão, o `ollama.service` (systemd) escuta só em `127.0.0.1:11434` — recusa conexões vindas de containers (`172.17.0.x`). Verifique:
```bash
ss -tlnp | grep 11434
```
Se aparecer `127.0.0.1:11434` (e não `0.0.0.0` ou `*:11434`), configure:
```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
echo -e "[Service]\nEnvironment=\"OLLAMA_HOST=0.0.0.0:11434\"" | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

---

## 4. Bug conhecido (corrigido no Dockerfile desde DA-24): `uv run` no `CMD` tentava ressincronizar no startup

Historicamente o `CMD` do `Dockerfile` rodava `uv run uvicorn ...`, e `uv run` por padrão tenta ressincronizar o ambiente a cada start — incluindo dependências de dev (`pre-commit` → `virtualenv` → `python-discovery`). Em um container sem saída de rede (ou rede restrita, como um cluster Kyma com Network Policies), isso derrubava o container com erro de DNS/timeout.

**Corrigido na origem (DA-24)**: o `Dockerfile` agora chama `.venv/bin/uvicorn` diretamente no `CMD`, sem passar por `uv run` - `docker-compose.yml` e os manifests Kyma (`deploy/kyma/`) não precisam mais de nenhum override de `command:` para contornar isso. Se você estiver usando uma imagem construída antes desta fase, seu próprio `command:` override (o de baixo) continua funcionando como workaround:
```yaml
  api:
    command: [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. Suba e valide

```bash
docker compose up -d --no-deps --build qdrant api   # cenário B (Ollama nativo)
# ou
docker compose up -d --build                         # cenário A (Ollama containerizado)

curl http://127.0.0.1:8000/health
```

Esperado: `{"status":"ok"}`.

---

## 6. Indexe a base de conhecimento

O Qdrant deste compose é uma instância própria e vazia (design intencional — ver [ARCHITECTURE.md](ARCHITECTURE.md), Decisão de Arquitetura sobre portabilidade para demos de terceiros). Popule-a:

```bash
QDRANT_URL=http://127.0.0.1:${QDRANT_HOST_PORT:-6333} uv run python -m app.rag.ingest --target incidents
QDRANT_URL=http://127.0.0.1:${QDRANT_HOST_PORT:-6333} uv run python -m app.rag.ingest --target reference
```

Sem `--reset`, o ingest processa só documentos ainda não indexados (idempotente — seguro rodar de novo).

---

## 7. Teste end-to-end

Desde a DA-18, `/diagnose` sempre exige o header `X-API-Key`. Se você
não configurou `API_KEY` no `.env`, uma chave aleatória é gerada a
cada `docker compose up`/restart e avisada em nível `WARNING` no log
de startup:

```bash
docker logs integration-incident-copilot-api-1 2>&1 | grep "API_KEY"
```

Use essa chave (ou a que você configurou em `API_KEY=` no `.env`, se
preferir uma chave estável entre restarts):

```bash
curl -s -X POST http://127.0.0.1:8000/diagnose \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <chave-do-log-ou-do-.env>" \
  -d '{"description":"IDoc travado com status 51, erro de mapeamento de material"}'
```

Deve retornar um JSON com `probable_root_cause`, `confidence`, `next_steps` e `report_markdown`. Sem o header (ou com valor errado): `401 Unauthorized`.

O mesmo diagnóstico também está disponível via **MCP** (DA-19) em
`POST /mcp/` (com barra final - sem ela, `307 Temporary Redirect`),
reusando o mesmo `X-API-Key`:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-API-Key: <chave-do-log-ou-do-.env>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cliente-teste","version":"1.0"}}}'
```

Deve retornar um evento `message` com `serverInfo.name == "sap-integration-copilot"` e as ferramentas `diagnose_incident`/`list_connectors` disponíveis.

Desde a **DA-23**, também existe um caminho de **ingestão orientada a
evento**: `POST /events/incident` simula o que um assinante de webhook
do SAP Event Mesh (modo REST/Webhook push subscription) receberia -
dispara o mesmo diagnóstico automaticamente, sem chamada manual.
Requer o header dedicado `X-Event-Mesh-Api-Key` (chave separada de
`X-API-Key`/`X-A2A-Api-Key` - ver log de startup ou `EVENT_MESH_API_KEY`
no `.env`):

```bash
curl -s -X POST http://127.0.0.1:8000/events/incident \
  -H "Content-Type: application/json" \
  -H "X-Event-Mesh-Api-Key: <chave-do-log-ou-do-.env>" \
  -d '{
        "type": "com.sap.integration.incident.detected.v1",
        "source": "cpi-monitor",
        "data": {"description": "IDoc travado com status 51", "interface_type": "rfc"}
      }'
```

Deve retornar o mesmo formato de `DiagnosisResponse` de `/diagnose`. Um
`type` diferente de `com.sap.integration.incident.detected.v1` retorna
`422 Unprocessable Content` (formato de evento não reconhecido).

---

## Resolução de problemas

| Sintoma | Causa | Fix |
|---|---|---|
| `port is already allocated` (6333) | Outro Qdrant já usa a porta (ex: stack pessoal de dev) | Definir `QDRANT_HOST_PORT` no `.env` (ex: `QDRANT_HOST_PORT=6335`) — o `docker-compose.yml` já suporta via `${QDRANT_HOST_PORT:-6333}` |
| `address already in use` (11434) | Ollama nativo já rodando | Ver seção 3-B |
| `ConnectionError: Failed to connect to Ollama` dentro do container | `OLLAMA_HOST` errado, ou Ollama nativo só em `127.0.0.1` | Ver seção 3-B, passos 2 e 3 |
| Container `api` reinicia sozinho com erro de DNS/`python-discovery` | `uv run` tentando sync sem rede | Ver seção 4 |
| `401 Unauthorized` em `/diagnose` ou `/a2a` | Header `X-API-Key`/`X-A2A-Api-Key` ausente ou errado (DA-18: chave sempre exigida, gerada automaticamente se não configurada) | Ver a chave gerada no log de startup (`docker logs ... \| grep API_KEY`), ou configure `API_KEY`/`A2A_API_KEY` no `.env` |
| `401 Unauthorized` em `/events/incident` | Header `X-Event-Mesh-Api-Key` ausente/errado, ou reusando `X-API-Key` por engano (DA-23: chave dedicada, não compartilhada com `/diagnose`/`/a2a`) | Ver a chave gerada no log de startup, ou configure `EVENT_MESH_API_KEY` no `.env` |
| `422 Unprocessable Content` em `/events/incident` | Campo `type` do evento diferente de `com.sap.integration.incident.detected.v1` (DA-23: único tipo de evento reconhecido hoje) | Ajustar o `type` do payload, ou aguardar suporte a novos tipos de evento em fase futura |
| `307 Temporary Redirect` em `POST /mcp` | Faltou a barra final - `app.mount()` do Starlette redireciona `/mcp` → `/mcp/` antes de checar autenticação (comportamento padrão, não é bug do MCP) | Chame `/mcp/` (com barra final) diretamente, ou configure o cliente MCP para seguir redirects |
| `RuntimeError: Directory 'static/dist/assets' does not exist` | Frontend não buildado / não copiado para a imagem | Ver seção 2 |
| `Collection 'sap_incident_docs' doesn't exist` | Qdrant do compose está vazio (esperado em ambiente novo) | Ver seção 6 |
| Variável de `.env` com `$` interpretada como interpolação do Compose (warning `variable is not set`) | `$` literal em segredo (ex: `CAP_CLIENT_SECRET`) | Escapar como `$$` no `.env` |

---

*Integration Incident Copilot · github.com/marcos-slima/sap-integration-copilot*
