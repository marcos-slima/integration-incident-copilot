# Troubleshooting

Guia de diagnóstico para problemas comuns ao rodar o Integration Incident Copilot.
Para instalação e configuração inicial veja [GETTING_STARTED.md](GETTING_STARTED.md).

---

## Conteúdo

1. [Startup falha — KeyError / AttributeError em settings](#1-startup-falha--keyerror--attributeerror-em-settings)
2. [AMQP consumer não inicia (AMQP_ENABLED=true)](#2-amqp-consumer-nao-inicia-amqp_enabledtrue)
3. [Kyma: pods em CrashLoopBackOff / Readiness probe failing](#3-kyma-pods-em-crashloopbackoff--readiness-probe-failing)
4. [Redis não conecta / circuit breaker sempre aberto](#4-redis-nao-conecta--circuit-breaker-sempre-aberto)
5. [RAG retorna contexto vazio ou irrelevante](#5-rag-retorna-contexto-vazio-ou-irrelevante)
6. [LLM retorna erro 401/403 (autenticação)](#6-llm-retorna-erro-401403-autenticacao)
7. [Busca web: resultados com dados sensíveis ou erros 429](#7-busca-web-resultados-com-dados-sensiveis-ou-erros-429)
8. [Frontend: tela em branco ou "Unauthorized"](#8-frontend-tela-em-branco-ou-unauthorized)
9. [CI falha em pytest-cov / bandit / pip-audit](#9-ci-falha-em-pytest-cov--bandit--pip-audit)
10. [Docker build falha — python-qpid-proton no Linux](#10-docker-build-falha--python-qpid-proton-no-linux)
11. [GraphRAG: Neo4j inacessível](#11-graphrag-neo4j-inacessivel)

---

## 1. Startup falha — KeyError / AttributeError em settings

**Sintoma:** `app.config.Settings` levanta erro ao carregar; FastAPI não sobe.

**Causas e correções:**

| Causa | Correção |
|---|---|
| `.env` ausente ou incompleto | Copie `.env.example` para `.env` e preencha as variáveis obrigatórias (`OPENAI_API_KEY` ou equivalente, `QDRANT_URL`). |
| Variável tipada incorretamente | `GRAPH_RAG_ENABLED` e `WEB_SEARCH_ENABLED` esperam `true`/`false` (sem aspas). Inteiros devem ser inteiros (`QDRANT_PORT=6333`, sem aspas). |
| `.env` com BOM ou CRLF | Converta para UTF-8 sem BOM e quebra de linha LF: `sed -i 's/\r//' .env`. |

```bash
# Verificação rápida das configurações carregadas
uv run python -c "from app.config import settings; print(settings.model_dump())"
```

---

## 2. AMQP consumer não inicia (AMQP_ENABLED=true)

**Sintoma:** FastAPI levanta `TypeError: A coroutine was expected` durante o lifespan.

**Causa:** O consumer AMQP usa `asyncio.create_task()` — que espera uma *coroutine* —
com um `Future` retornado por `run_in_executor()`. Certifique-se de que a versão no git
inclui o fix B2 (commit `7166f55` ou posterior).

**Verificação:**
```bash
git log --oneline | head -5
# Deve conter: "fix(amqp): wrap run_in_executor Future in coroutine..."
```

**Outras causas:**

| Causa | Correção |
|---|---|
| `SOLACE_HOST` / `SOLACE_PORT` incorretos | Verifique credenciais no `.env`; nunca comite o `.env`. |
| `python-qpid-proton` não instalado | Ver item [10](#10-docker-build-falha--python-qpid-proton-no-linux). |
| Firewall bloqueando porta AMQP (5671/5672) | Libere a porta ou use o modo mock (`AMQP_ENABLED=false`). |

---

## 3. Kyma: pods em CrashLoopBackOff / Readiness probe failing

**Sintoma:** `kubectl get pods` mostra `CrashLoopBackOff` ou `0/1 Running` com
readiness failing.

**Diagnóstico:**
```bash
kubectl logs -l app=integration-incident-copilot --previous
kubectl describe pod <pod-name>
```

**Causas comuns:**

| Causa | Correção |
|---|---|
| Readiness probe aponta para `/ready` (rota inexistente) | Aponte a probe para `/health` no `deployment.yaml`. |
| `REDIS_URL` ausente no ConfigMap | Adicione `REDIS_URL: "redis://redis:6379/0"` ao `configmap.yaml`; sem Redis, o task-store é por-processo e a idempotência distribuída não opera em múltiplas réplicas. |
| Secret com API keys não montado | Crie o Secret referenciado em `secret.example.yaml` e aplique antes do Deployment. |
| Imagem não encontrada (ImagePullBackOff) | Verifique se o registry e a tag no `deployment.yaml` batem com o que foi publicado pelo CI. |

**Verificar health check manualmente:**
```bash
kubectl port-forward svc/integration-incident-copilot 8080:80
curl http://localhost:8080/health
```

---

## 4. Redis não conecta / circuit breaker sempre aberto

**Sintoma:** Logs com `ConnectionError: Redis ...`; ou circuit breaker abre após
a primeira falha e nunca fecha.

**Causas:**

| Causa | Correção |
|---|---|
| `REDIS_URL` não configurado | Adicione ao `.env` ou ao ConfigMap Kyma. Sem Redis, o circuit breaker usa estado em memória (por-processo) — não compartilhado entre réplicas. |
| Redis não iniciado | `docker compose up -d redis` (para desenvolvimento local). |
| Senha incorreta | Se `REDIS_PASSWORD` estiver definido, certifique-se de que bate com o que o Redis espera. |
| TLS/mTLS em Redis externo | Configure `REDIS_URL` com esquema `rediss://` e forneça os certificados. |

**Verificação rápida:**
```bash
redis-cli -u "$REDIS_URL" ping
# Esperado: PONG
```

---

## 5. RAG retorna contexto vazio ou irrelevante

**Sintoma:** Diagnóstico genérico sem referência a documentos; `matched_document: null`.

**Diagnóstico:**
```bash
# Verificar se Qdrant está acessível e com dados
uv run python -c "
from qdrant_client import QdrantClient
from app.config import settings
c = QdrantClient(url=settings.qdrant_url)
info = c.get_collection(settings.qdrant_collection_name)
print('vectors:', info.vectors_count)
"
```

**Causas:**

| Causa | Correção |
|---|---|
| Coleção vazia (nenhum documento ingerido) | Execute `uv run python -m app.rag.ingest` ou siga [INGEST_REFERENCE.md](INGEST_REFERENCE.md). |
| `QDRANT_URL` incorreto | Verifique `.env`; padrão local: `http://localhost:6333`. |
| Modelo de embedding diferente do usado na ingestão | O modelo é fixado na coleção; reingerir com o modelo correto ou recriar a coleção. |
| `QDRANT_COLLECTION_NAME` diverge entre ingestão e runtime | Certifique-se de que a variável é a mesma nos dois contextos. |

---

## 6. LLM retorna erro 401/403 (autenticação)

**Sintoma:** Diagnóstico falha com `AuthenticationError` ou `403 Forbidden`.

**Causas:**

| Causa | Correção |
|---|---|
| Chave de API ausente ou expirada | Atualize `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `AZURE_OPENAI_API_KEY` no `.env`. |
| Endpoint Azure incorreto | `AZURE_OPENAI_ENDPOINT` deve terminar sem barra: `https://<resource>.openai.azure.com`. |
| `LLM_PROVIDER` não configurado | Valores válidos: `openai`, `azure`, `anthropic`, `mock`. |
| Modo `DATA_SOVEREIGNTY_MODE=local_only` com chave cloud | Neste modo apenas modelos locais (Ollama) são usados; configure `OLLAMA_BASE_URL`. |

---

## 7. Busca web: resultados com dados sensíveis ou erros 429

**Sintoma:** A consulta enviada ao DuckDuckGo contém IDs de documentos SAP, GUIDs ou
tokens; ou a busca retorna `RateLimitError`.

**Sanitização (fix A3):** A partir do commit que inclui A3, as consultas passam por
`_sanitize_web_search_query()` que redige URLs, números IDoc SAP (18 dígitos), GUIDs,
tokens Bearer/Basic e trunca a 200 caracteres. Confirme que o commit está presente:
```bash
git log --oneline | grep -i "web.search\|egress\|A3"
```

**Limitar/desabilitar busca web:**
```bash
# No .env
WEB_SEARCH_ENABLED=false
```

**Rate limit 429:** DuckDuckGo não exige chave de API, mas limita por IP. Em produção,
considere um proxy rotativo ou cache de resultados.

---

## 8. Frontend: tela em branco ou "Unauthorized"

**Sintoma:** A UI carrega mas retorna `401 Unauthorized` ao chamar `/diagnose`.

**Causa:** A partir do fix A4, a chave de API **não** é mais embutida em variáveis de
ambiente Vite. Ela deve ser fornecida em runtime via `sessionStorage`.

**Solução:** Na interface, preencha o campo **API Key** (canto superior direito) com a
chave configurada em `API_KEY` no `.env` do backend. A chave é armazenada apenas na
sessão do navegador e nunca enviada ao servidor em parâmetros de URL.

**Se `REQUIRE_AUTH=false`:** O backend aceita requisições sem chave; o campo de API Key
no frontend pode ser deixado em branco.

---

## 9. CI falha em pytest-cov / bandit / pip-audit

**Sintoma:** Jobs de CI falham com `No module named pytest_cov` ou `command not found: bandit`.

**Causa:** Essas ferramentas precisam estar no grupo `dev` do `pyproject.toml` e no
`uv.lock`. A partir do fix B4 elas estão declaradas. Certifique-se de que o `uv.lock`
está atualizado:

```bash
uv lock
git add uv.lock pyproject.toml
git commit -m "chore: update lockfile"
```

**O CI usa `uv run` para isolar o ambiente:**
```bash
uv run pytest --cov=app tests/
uv run bandit -r app/ -c pyproject.toml
uv run pip-audit
```

---

## 10. Docker build falha — python-qpid-proton no Linux

**Sintoma:** `docker build` falha com `gcc: command not found` ou erro de compilação
do `python-qpid-proton`.

**Causa:** O pacote `python-qpid-proton==0.40.0` não possui wheel para Linux/amd64 no
PyPI; o build a partir do código-fonte exige toolchain de compilação (gcc, cmake,
bibliotecas de desenvolvimento do Proton).

**Solução — adicionar build deps ao Dockerfile:**
```dockerfile
# Adicionar antes do `uv sync` na stage de build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ cmake make \
    libqpid-proton-cpp12-dev \
    && rm -rf /var/lib/apt/lists/*
```

**Alternativa:** Se AMQP não for necessário, mantenha `AMQP_ENABLED=false` e remova
`python-qpid-proton` das dependências opcionais.

---

## 11. GraphRAG: Neo4j inacessível

**Sintoma:** Logs com `ServiceUnavailable: Failed to establish connection to Neo4j`;
ou diagnóstico sem contexto de histórico relacional.

**GraphRAG é opcional** e desligado por padrão (`GRAPH_RAG_ENABLED=false`). O sistema
funciona normalmente sem ele — o RAG vetorial (Qdrant) continua operando.

**Para ativar:**
```bash
# 1. Subir Neo4j via compose
docker compose --profile graphrag up -d neo4j

# 2. Configurar no .env
GRAPH_RAG_ENABLED=true
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=<sua-senha>

# 3. Criar constraints/índices (uma vez)
uv run python -m app.rag.graph_store --init
```

**Verificar conectividade:**
```bash
uv run python -c "
from app.rag.graph_store import _get_driver
_get_driver().verify_connectivity()
print('Neo4j OK')
"
```

---

## Não resolveu?

1. Verifique os logs com `docker compose logs -f app` (local) ou `kubectl logs -f <pod>` (Kyma).
2. Abra uma issue em [github.com/marcos-lima/integration-incident-copilot](https://github.com/marcos-lima/integration-incident-copilot/issues) com o stack trace completo e a saída de `GET /health`.
