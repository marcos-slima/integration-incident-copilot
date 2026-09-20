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

O `Dockerfile` empacota os assets estáticos do frontend (React), mas não builda a partir do código-fonte — o build precisa existir antes de `docker compose build`:

```bash
cd frontend && npm install && npm run build && cd ..
mkdir -p static && cp -r frontend/dist static/dist
```

> Melhoria pendente: mover isso para um multi-stage build no `Dockerfile`, eliminando o passo manual.

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

## 4. Bug conhecido: `uv run` no `CMD` tenta ressincronizar no startup

O `CMD` do `Dockerfile` roda `uv run uvicorn ...`, e `uv run` por padrão tenta ressincronizar o ambiente a cada start — incluindo dependências de dev (`pre-commit` → `virtualenv` → `python-discovery`). Em um container sem saída de rede (ou rede restrita), isso derruba o container com erro de DNS/timeout.

**Fix**: sobrescrever o `command:` do serviço `api` no `docker-compose.yml` para chamar o `uvicorn` direto do venv já instalado no build, sem passar por `uv run`:
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

---

## Resolução de problemas

| Sintoma | Causa | Fix |
|---|---|---|
| `port is already allocated` (6333) | Outro Qdrant já usa a porta (ex: stack pessoal de dev) | Definir `QDRANT_HOST_PORT` no `.env` (ex: `QDRANT_HOST_PORT=6335`) — o `docker-compose.yml` já suporta via `${QDRANT_HOST_PORT:-6333}` |
| `address already in use` (11434) | Ollama nativo já rodando | Ver seção 3-B |
| `ConnectionError: Failed to connect to Ollama` dentro do container | `OLLAMA_HOST` errado, ou Ollama nativo só em `127.0.0.1` | Ver seção 3-B, passos 2 e 3 |
| Container `api` reinicia sozinho com erro de DNS/`python-discovery` | `uv run` tentando sync sem rede | Ver seção 4 |
| `401 Unauthorized` em `/diagnose` ou `/a2a` | Header `X-API-Key`/`X-A2A-Api-Key` ausente ou errado (DA-18: chave sempre exigida, gerada automaticamente se não configurada) | Ver a chave gerada no log de startup (`docker logs ... \| grep API_KEY`), ou configure `API_KEY`/`A2A_API_KEY` no `.env` |
| `RuntimeError: Directory 'static/dist/assets' does not exist` | Frontend não buildado / não copiado para a imagem | Ver seção 2 |
| `Collection 'sap_incident_docs' doesn't exist` | Qdrant do compose está vazio (esperado em ambiente novo) | Ver seção 6 |
| Variável de `.env` com `$` interpretada como interpolação do Compose (warning `variable is not set`) | `$` literal em segredo (ex: `CAP_CLIENT_SECRET`) | Escapar como `$$` no `.env` |

---

*Integration Incident Copilot · github.com/marcos-slima/sap-integration-copilot*
