# Troubleshooting

Guia de diagnóstico dos problemas mais comuns ao rodar o Integration
Incident Copilot. Instalação e configuração inicial estão em
[GETTING_STARTED.md](GETTING_STARTED.md).

> **Revisado na validação de 2026-10-07 (Bloco 5).** A versão anterior
> mandava trocar a readiness `/ready` por `/health`, o que desliga a
> readiness real. Também citava variáveis e atributos que não existem. Toda
> variável citada aqui é conferida pelo gate `docs_env_vars`.

## Conteúdo

1. [A API não sobe](#1-a-api-nao-sobe)
2. [Consumidor AMQP não conecta](#2-consumidor-amqp-nao-conecta)
3. [Kyma: CrashLoopBackOff ou readiness falhando](#3-kyma-crashloopbackoff-ou-readiness-falhando)
4. [Redis e circuit breaker](#4-redis-e-circuit-breaker)
5. [RAG sem contexto ou `/diagnose` com 500 por Qdrant](#5-rag-sem-contexto-ou-diagnose-com-500-por-qdrant)
6. [LLM: autenticação ou `PolicyViolationError`](#6-llm-autenticacao-ou-policyviolationerror)
7. [Busca web não acontece](#7-busca-web-nao-acontece)
8. [Frontend: 401 ou tela de login em loop](#8-frontend-401-ou-tela-de-login-em-loop)
9. [Build da imagem e `python-qpid-proton`](#9-build-da-imagem-e-python-qpid-proton)
10. [GraphRAG: Neo4j inacessível](#10-graphrag-neo4j-inacessivel)
11. [Ambiente em containers (`scripts/start-docker.sh`)](#11-ambiente-em-containers-scriptsstart-dockersh)

---

## 1. A API não sobe

```bash
# Configuração efetiva (sem segredos: confira só os nomes e os modos)
uv run python -c "from app.config import settings; print(settings.llm_provider, settings.qdrant_url, settings.require_auth)"
```

| Mensagem no boot | Causa e correção |
|---|---|
| `ValidationError` do pydantic | Valor fora do domínio. Exemplos: `LLM_PROVIDER` aceita `ollama`, `openai` e `azure_openai`; `DATA_SOVEREIGNTY_MODE` aceita `strict` e `cloud_with_dlp`; `EMBEDDING_BACKEND` aceita `ollama` e `fastembed`. Booleanos são `true`/`false`. |
| Recusa subir por chave ausente | Com `REQUIRE_AUTH=true`, `API_KEY`, `A2A_API_KEY` e `EVENT_MESH_API_KEY` precisam estar definidas. Sem `REQUIRE_AUTH`, a chave vazia é gerada no boot e só aparece no log. |
| `ConfigurationError` sobre a chave de evidência | Com `DATABASE_URL` preenchida, `LLM_CREDENTIALS_MASTER_KEY` é obrigatória (DA-60). |
| Variável "ignorada" | O pydantic **ignora** nomes que não são campos do `Settings`. Confira o nome exato em `app/config.py` ou no `.env.example`. A conexão com o Qdrant é por `QDRANT_URL` (URL completa), e não existe variável de porta separada. |
| `.env` com BOM ou CRLF | `sed -i 's/\r//' .env` |

## 2. Consumidor AMQP não conecta

O consumidor (DA-32/40) só sobe com `AMQP_ENABLED=true`. As variáveis usam o
prefixo `AMQP_`: `AMQP_HOST`, `AMQP_PORT` (default 5671, TLS),
`AMQP_USERNAME`, `AMQP_PASSWORD` e `AMQP_QUEUE`.

| Sintoma | Causa e correção |
|---|---|
| Nenhuma mensagem consumida | Confira `AMQP_QUEUE` e as permissões do usuário no broker (Solace/Event Mesh). |
| Mensagem vai para a DMQ | Depois de `AMQP_MAX_REDELIVERIES` entregas com falha, o consumidor rejeita a mensagem. Envelope inválido (sem `specversion`, `id` ou `source`, CloudEvents 1.0) é rejeitado na hora. |
| Diagnósticos lentos travam o consumo | Com `REDIS_URL`, o consumidor só enfileira no RQ e o worker diagnostica. Sem Redis, usa um pool de threads no processo da API. |
| Erro de SSL | O `python-qpid-proton` precisa ter sido compilado com SSL. O build da imagem verifica isso (seção 9). |

Não verificado neste projeto contra um broker real; os testes simulam o
reactor do Proton.

## 3. Kyma: CrashLoopBackOff ou readiness falhando

```bash
kubectl logs -l app=integration-incident-copilot --previous
kubectl describe pod <pod>
```

**As duas probes têm papéis diferentes. Não as troque.**

| Probe | Caminho | O que responde |
|---|---|---|
| `livenessProbe` | `/health` | o processo está vivo |
| `readinessProbe` | `/ready` | as dependências respondem; devolve 503 quando degradado, e o pod sai do balanceamento |

| Causa | Correção |
|---|---|
| `/ready` em 503 | Sem credencial, a resposta é só o status. Com `X-API-Key`, o detalhe mostra qual serviço está `degraded` (Qdrant, Ollama, Redis, Neo4j). |
| Secret não aplicado | Crie o Secret a partir de `deploy/kyma/secret.example.yaml` antes do Deployment. |
| `ImagePullBackOff` | O registry e a tag em `deploy/kyma/deployment.yaml` precisam bater com a imagem publicada. |

## 4. Redis e circuit breaker

Sem `REDIS_URL`:

- o circuit breaker, a idempotência de eventos e o task store A2A ficam **em
  memória, por processo**;
- com mais de uma réplica, cada pod tem o próprio estado.

O boot avisa isso no log.

| Sintoma | Causa e correção |
|---|---|
| Circuito de um conector abre e não fecha | Ele abre só por indisponibilidade (5xx, 429, rede) e fecha depois do cooldown e de um sucesso. 401/403 **não** abrem o circuito: credencial errada aparece como erro do conector, não como circuito aberto. |
| `ConnectionError` do Redis | Confira com `redis-cli -u "$REDIS_URL" ping`. A aplicação tenta reconectar a cada 30 s e, enquanto isso, usa o estado em memória. |

## 5. RAG sem contexto ou `/diagnose` com 500 por Qdrant

```bash
uv run python -c "
from qdrant_client import QdrantClient
from app.config import settings
from app.rag.retriever import COLLECTIONS
c = QdrantClient(url=settings.qdrant_url)
for nome in COLLECTIONS.values():
    try: print(nome, c.get_collection(nome).points_count)
    except Exception as e: print(nome, 'ausente:', type(e).__name__)
"
```

| Causa | Correção |
|---|---|
| Collection vazia | `uv run python -m app.rag.ingest --target incidents`. Use `--target reference` para o acervo de referência e `--target all` para os dois. |
| **Qdrant fora do ar** | Hoje o `/diagnose` responde **500**, inclusive quando o rule engine resolveria sem RAG (achado da validação de 2026-10-07, ainda aberto). Suba o Qdrant antes da API; o `/ready` acusa `qdrant: degraded`. |
| Embedding diferente do usado na ingestão | O retriever confere a identidade do embedding (DA-45). Reingira com o mesmo `EMBEDDING_BACKEND`/`EMBEDDING_MODEL`. Desde a validação de 2026-10-07, `EMBEDDING_BACKEND` no `.env` passa a valer: antes só valia como variável exportada no shell. |
| Fallback da referência não acontece | Confira `REFERENCE_LIBRARY_FALLBACK_ENABLED`. O deploy Kyma vem com `false` (M-24). |

## 6. LLM: autenticação ou `PolicyViolationError`

| Sintoma | Causa e correção |
|---|---|
| 401/403 do provider | Atualize `OPENAI_API_KEY` ou `AZURE_OPENAI_API_KEY`. O endpoint Azure (`AZURE_OPENAI_ENDPOINT`) vai sem barra no fim. |
| `PolicyViolationError: nenhum provider permitido` (DA-43) | Dado `confidential` (o default sem conector real) só vai para origem local ou para uma liberada. Liberar cloud exige **as duas coisas**: `DATA_SOVEREIGNTY_MODE=cloud_with_dlp` e a origem em `CONFIDENTIAL_ALLOWED_ORIGINS`. A política efetiva aparece em `GET /llm/policy`. |
| Ollama não responde | O host vem de `OLLAMA_HOST` (default `http://127.0.0.1:11434`). Dentro do container, use o `host.docker.internal` que o compose injeta. |
| Timeout | `DIAGNOSIS_TIMEOUT_SECONDS` (default 180) limita o diagnóstico inteiro; modelos grandes no Ollama podem precisar de mais. |

## 7. Busca web não acontece

A busca web é *fail-closed* (DA-57). Ela só acontece quando **todas** estas
condições valem:

1. `WEB_SEARCH_ENABLED=true`;
2. `WEB_SEARCH_POLICY` diferente de `disabled`;
3. com `public_only`, o incidente é classificado como `public`;
4. existe uma linha **habilitada** em `web_search_sources` para o
   `interface_type` (tela `/admin/web-search`). Sem `DATABASE_URL`, não há
   linha e, portanto, não há busca.

A consulta passa por `app/agent/nodes.py::_sanitize_web_search_query`, que
redige URLs, números de IDoc, GUIDs e tokens e trunca o texto. O DuckDuckGo
limita por IP; falha da busca é logada e vira ausência de resultado, não erro do diagnóstico.

## 8. Frontend: 401 ou tela de login em loop

A UI usa **login de sessão** (DA-54): `POST /auth/login` emite um cookie
HttpOnly, que o navegador reenvia sozinho. A UI não usa `X-API-Key`; a chave
é para integrações de máquina.

| Causa | Correção |
|---|---|
| Login sempre 401 | Os usuários vêm de `WEB_UI_USERS` no `.env` (bootstrap, formato `usuario:pbkdf2_sha256$…`) ou da tabela `web_users` com ativação concluída (DA-55). Sem nenhuma das duas fontes, o login fica **fechado** (fail-closed). |
| Cookie não volta | No dev, use o proxy do Vite (`npm run dev`), para manter a mesma origem. |
| 429 no login | `/auth/login` e as rotas de ativação aceitam 5 tentativas por minuto por cliente. |

## 9. Build da imagem e `python-qpid-proton`

O `Dockerfile` compila o `python-qpid-proton` no estágio de build (gcc,
cmake, `libssl-dev`, `libsasl2-dev`) e **reprova o build** se o módulo sair
sem SSL. O estágio final leva só as bibliotecas de runtime.

| Sintoma | Causa e correção |
|---|---|
| `proton com SSL` não aparece / assert falha | Falta `pkg-config` ou `libssl-dev` no builder. Não remova esses pacotes. |
| Build lento | O torch é a variante CPU (índice do PyTorch fixado no `pyproject.toml`). Use cache do BuildKit. |

## 10. GraphRAG: Neo4j inacessível

O GraphRAG é opcional e vem desligado (`GRAPH_RAG_ENABLED=false`). Sem ele,
o grafo é o linear e o RAG vetorial continua funcionando.

```bash
docker compose --profile graphrag up -d neo4j
# .env: GRAPH_RAG_ENABLED=true, NEO4J_URI=bolt://127.0.0.1:7687, NEO4J_PASSWORD=<senha>
uv run python -m app.rag.graph_store --init     # constraints, uma vez
```

O `NEO4J_AUTH` do container só vale na **primeira** inicialização do volume.
Trocar a senha no `.env` depois exige recriar o volume.

## 11. Ambiente em containers (`scripts/start-docker.sh`)

Validado de ponta a ponta em 2026-10-07: imagem construída, com Qdrant,
Postgres, Grafana e API.

| Sintoma | Causa e correção |
|---|---|
| Para no passo 1 pedindo senhas | O compose exige `POSTGRES_PASSWORD`, `GRAFANA_PASSWORD` e `NEO4J_PASSWORD` mesmo sem o perfil correspondente. |
| "Migrações falharam" | O script para de propósito: subir a API com o schema velho seria pior. Leia o erro acima da mensagem. |
| `PermissionError: '.env'` no container | O container lê o `.env` como uid 1000. Com `chmod 600` e outro dono, ele não consegue ler; use `chmod 644` ou ajuste o dono. |
| Ingestão tenta o Ollama sem ele instalado | Defina `EMBEDDING_BACKEND=fastembed` no `.env`. |
| Container tenta `127.0.0.1` para o banco | No modo container, o compose injeta `postgres:5432`. Para um banco externo, use `CONTAINER_DATABASE_URL`, não `DATABASE_URL`. |

## Não resolveu?

Colete os logs (`docker compose logs api` ou `kubectl logs <pod>`) e a saída
de `GET /ready` com `X-API-Key`. Num erro 500, procure no log o `error_id`
que veio na resposta: ele marca a linha com o stack trace.
