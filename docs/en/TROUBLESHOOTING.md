# Troubleshooting

> English translation of [`docs/TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) · source commit `0b03c7b` · [Português](../TROUBLESHOOTING.md)

Guide to diagnose the most common problems when running the Integration
Incident Copilot. Installation and initial configuration are in
[GETTING_STARTED.md](GETTING_STARTED.md).

> **Reviewed in the 2026-10-07 validation (Block 5).** The previous
> version instructed to replace the readiness `/ready` by `/health`, which turns off the
> real readiness. It also cited variables and attributes that do not exist. Every
> variable cited here is checked by the `docs_env_vars` gate.

## Content

1. [The API does not start](#1-the-api-does-not-start)
2. [AMQP consumer does not connect](#2-amqp-consumer-does-not-connect)
3. [Kyma: CrashLoopBackOff or readiness failing](#3-kyma-crashloopbackoff-or-readiness-failing)
4. [Redis and circuit breaker](#4-redis-and-circuit-breaker)
5. [RAG without context or `/diagnose` with 500 by Qdrant](#5-rag-without-context-or-diagnose-with-500-by-qdrant)
6. [LLM: authentication or `PolicyViolationError`](#6-llm-authentication-or-policyviolationerror)
7. [Web search does not happen](#7-web-search-does-not-happen)
8. [Frontend: 401 or login loop](#8-frontend-401-or-login-loop)
9. [Image build and `python-qpid-proton`](#9-image-build-and-python-qpid-proton)
10. [GraphRAG: Neo4j inaccessible](#10-graphrag-neo4j-inaccessible)
11. [Environment in containers (`scripts/start-docker.sh`)](#11-environment-in-containers-scriptsstart-dockersh)
12. [Language: screen or diagnosis in the wrong language](#12-language-screen-or-diagnosis-in-the-wrong-language)

---

## 1. The API does not start

```bash
# Effective configuration (without secrets: check only names and modes)
uv run python -c "from app.config import settings; print(settings.llm_provider, settings.qdrant_url, settings.require_auth)"
```

| Boot message | Cause and fix |
|---|---|
| `ValidationError` from pydantic | Value out of domain. Examples: `LLM_PROVIDER` accepts `ollama`, `openai` and `azure_openai`; `DATA_SOVEREIGNTY_MODE` accepts `strict` and `cloud_with_dlp`; `EMBEDDING_BACKEND` accepts `ollama` and `fastembed`. Booleans are `true`/`false`. |
| Refuses to start due to missing key | With `REQUIRE_AUTH=true`, `API_KEY`, `A2A_API_KEY` and `EVENT_MESH_API_KEY` must be defined. Without `REQUIRE_AUTH`, the empty key is generated at boot and only appears in the log. |
| `ConfigurationError` about the evidence key | With `DATABASE_URL` filled, `LLM_CREDENTIALS_MASTER_KEY` is mandatory (DA-60). |
| Variable "ignored" | Pydantic **ignores** names that are not fields of `Settings`. Check the exact name in `app/config.py` or in the `.env.example`. The connection to Qdrant is by `QDRANT_URL` (full URL), and there is no separate port variable. |
| `.env` with BOM or CRLF | `sed -i 's/\r//' .env` |

## 2. AMQP consumer does not connect

The consumer (DA-32/40) only starts with `AMQP_ENABLED=true`. The variables use the
prefix `AMQP_`: `AMQP_HOST`, `AMQP_PORT` (default 5671, TLS),
`AMQP_USERNAME`, `AMQP_PASSWORD` and `AMQP_QUEUE`.

| Symptom | Cause and fix |
|---|---|
| No messages consumed | Check `AMQP_QUEUE` and the user permissions on the broker (Solace/Event Mesh). |
| Message goes to DMQ | After `AMQP_MAX_REDELIVERIES` failed deliveries, the consumer rejects the message. Invalid envelope (without `specversion`, `id` or `source`, CloudEvents 1.0) is rejected immediately. |
| Slow diagnoses block consumption | With `REDIS_URL`, the consumer only queues in RQ and the worker diagnoses. Without Redis, uses a thread pool in the API process. |
| SSL error | The `python-qpid-proton` needs to have been compiled with SSL. The image build checks this (section 9). |

Not verified in this project against a real broker; the tests simulate the
Proton reactor.

## 3. Kyma: CrashLoopBackOff or readiness failing

```bash
kubectl logs -l app=integration-incident-copilot --previous
kubectl describe pod <pod>
```

**The two probes have different roles. Do not swap them.**

| Probe | Path | What it responds |
|---|---|---|
| `livenessProbe` | `/health` | the process is alive |
| `readinessProbe` | `/ready` | dependencies respond; returns 503 when degraded, and the pod is removed from load balancing |

| Cause | Fix |
|---|---|
| `/ready` at 503 | Without credential, the response is only the status. With `X-API-Key`, the detail shows which service is `degraded` (Qdrant, Ollama, Redis, Neo4j). |
| Secret not applied | Create the Secret from `deploy/kyma/secret.example.yaml` before the Deployment. |
| `ImagePullBackOff` | The registry and tag in `deploy/kyma/deployment.yaml` must match the published image. |

## 4. Redis and circuit breaker

Without `REDIS_URL`:

- the circuit breaker, the idempotency of events and the A2A task store are **in
  memory, per process**;
- with more than one replica, each pod has its own state.

The boot warns about this in the log.

| Symptom | Cause and fix |
|---|---|
| Circuit of a connector opens and does not close | It opens only for unavailability (5xx, 429, network) and closes after the cooldown and a success. 401/403 **do not** open the circuit: wrong credential appears as connector error, not as circuit open. |
| `ConnectionError` from Redis | Check with `redis-cli -u "$REDIS_URL" ping`. The application tries to reconnect every 30 s and, while that, uses the in-memory state. |

## 5. RAG without context or `/diagnose` with 500 by Qdrant

```bash
uv run python -c "
from qdrant_client import QdrantClient
from app.config import settings
from app.rag.retriever import COLLECTIONS
c = QdrantClient(url=settings.qdrant_url)
for nome in COLLECTIONS.values():
    try: print(nome, c.get_collection(nome).points_count)
    except Exception as e: print(nome, 'missing:', type(e).__name__)
"
```

| Cause | Fix |
|---|---|
| Empty collection | `uv run python -m app.rag.ingest --target incidents`. Use `--target reference` for the reference library and `--target all` for both. |
| **Qdrant down** | Today the `/diagnose` responds **500**, even when the rule engine would resolve without RAG (finding of the 2026-10-07 validation, still open). Bring up the Qdrant before the API; the `/ready` reports `qdrant: degraded`. |
| Embedding different from the one used in ingestion | The retriever checks the identity of the embedding (DA-45). Re-ingest with the same `EMBEDDING_BACKEND`/`EMBEDDING_MODEL`. Since the 2026-10-07 validation, `EMBEDDING_BACKEND` in the `.env` now works: before it only worked as exported variable in the shell. |
| Reference fallback does not happen | Check `REFERENCE_LIBRARY_FALLBACK_ENABLED`. The Kyma deployment comes with `false` (M-24). |

## 6. LLM: authentication or `PolicyViolationError`

| Symptom | Cause and fix |
|---|---|
| 401/403 from provider | Update `OPENAI_API_KEY` or `AZURE_OPENAI_API_KEY`. The Azure endpoint (`AZURE_OPENAI_ENDPOINT`) goes without trailing slash. |
| `PolicyViolationError: no provider allowed` (DA-43) | Data `confidential` (the default without real connector) only goes to local or to an allowed one. Allowing cloud requires **both**: `DATA_SOVEREIGNTY_MODE=cloud_with_dlp` and the origin in `CONFIDENTIAL_ALLOWED_ORIGINS`. The effective policy appears in `GET /llm/policy`. |
| Ollama does not respond | The host comes from `OLLAMA_HOST` (default `http://127.0.0.1:11434`). Inside the container, use the `host.docker.internal` that the compose injects. |
| Timeout | `DIAGNOSIS_TIMEOUT_SECONDS` (default 180) limits the entire diagnosis; large models in Ollama may need more. |

## 7. Web search does not happen

The web search is *fail-closed* (DA-57). It only happens when **all** these
conditions hold:

1. `WEB_SEARCH_ENABLED=true`;
2. `WEB_SEARCH_POLICY` different from `disabled`;
3. with `public_only`, the incident is classified as `public`;
4. there is an **enabled** line in `web_search_sources` for the
   `interface_type` (`/admin/web-search` screen). Without `DATABASE_URL`, there is
   no line and, therefore, no search.

The query passes through `app/agent/nodes.py::_sanitize_web_search_query`, which
redacts URLs, IDoc numbers, GUIDs and tokens and truncates the text. DuckDuckGo
limits by IP; search failure is logged and becomes absence of result, not diagnosis error.

## 8. Frontend: 401 or login loop

The UI uses **session login** (DA-54): `POST /auth/login` issues an HttpOnly
cookie, which the browser sends back by itself. The UI does not use `X-API-Key`; the key
is for machine integrations.

| Cause | Fix |
|---|---|
| Login always 401 | Users come from `WEB_UI_USERS` in the `.env` (bootstrap, format `user:pbkdf2_sha256$…`) or from the `web_users` table with completion of activation (DA-55). Without either source, the login is **closed** (fail-closed). |
| Cookie does not return | In dev, use the Vite proxy (`npm run dev`), to keep the same origin. |
| 429 on login | `/auth/login` and the activation routes accept 5 attempts per minute per client. |

## 9. Image build and `python-qpid-proton`

The `Dockerfile` compiles the `python-qpid-proton` in the build stage (gcc,
cmake, `libssl-dev`, `libsasl2-dev`) and **fails the build** if the module comes out
without SSL. The final stage only takes the runtime libraries.

| Symptom | Cause and fix |
|---|---|
| `proton with SSL` does not appear / assert fails | Missing `pkg-config` or `libssl-dev` in the builder. Do not remove these packages. |
| Slow build | The torch is the CPU variant (PyTorch index fixed in `pyproject.toml`). Use BuildKit cache. |

## 10. GraphRAG: Neo4j inaccessible

The GraphRAG is optional and comes disabled (`GRAPH_RAG_ENABLED=false`). Without it,
the graph is linear and the vector RAG continues to work.

```bash
docker compose --profile graphrag up -d neo4j
# .env: GRAPH_RAG_ENABLED=true, NEO4J_URI=bolt://127.0.0.1:7687, NEO4J_PASSWORD=<password>
uv run python -m app.rag.graph_store --init     # constraints, once
```

The `NEO4J_AUTH` of the container only works on the **first** initialization of the volume.
Changing the password in the `.env` afterwards requires recreating the volume.

## 11. Environment in containers (`scripts/start-docker.sh`)

Validated end-to-end on 2026-10-07: image built, with Qdrant,
Postgres, Grafana and API.

| Symptom | Cause and fix |
|---|---|
| Stops at step 1 asking for passwords | The compose requires `POSTGRES_PASSWORD`, `GRAFANA_PASSWORD` and `NEO4J_PASSWORD` even without the corresponding profile. |
| "Migrations failed" | The script stops on purpose: bringing up the API with the old schema would be worse. Read the error above the message. |
| `PermissionError: '.env'` in the container | The container reads the `.env` as uid 1000. With `chmod 600` and another owner, it cannot read; use `chmod 644` or adjust the owner. |
| Ingestion tries the Ollama without it installed | Set `EMBEDDING_BACKEND=fastembed` in the `.env`. |
| Container tries `127.0.0.1` for the database | In container mode, the compose injects `postgres:5432`. For an external database, use `CONTAINER_DATABASE_URL`, not `DATABASE_URL`. |

## 12. Language: screen or diagnosis in the wrong language

DA-73 (screens) and DA-74 (content).

| Symptom | Cause and fix |
|---|---|
| The screen opens in English, but the browser is in Portuguese | The `iic_locale` cookie wins over the browser. Switch in the selector or delete the cookie. `?lng=pt-BR` forces the language in the URL. |
| English screen, Portuguese diagnosis | Diagnosis run before switching languages (the one on screen does not change; run it again), or an API client without the `locale` field (without it, content comes in `pt-BR`). |
| `/diagnose` returns 422 with `locale nao suportado` | Content exists in `pt-BR` and `en`. Browser tags such as `en-US` and `pt-PT` are accepted; other languages are not. |
| Text between `⟦ ⟧` on screen | Review pseudo-locale (`?lng=xx`). Switch the language in the selector. |
| `prompt_digest_measured` gate warns about the `en` prompt | The `en` digest is not in `locales.en` (or changed after the measurement). Measure and re-record it as described in [`CUSTOMIZACAO.md`](../CUSTOMIZACAO.md) ("Idioma das interfaces"). |
| `content_i18n` gate fails after a new rule | The new category needs `rules.<category>` (cause and steps) in `app/i18n/locales/content.en.json`. |
| Activation e-mail in the wrong language | It goes out in the Admin UI language of whoever created the user (or the `locale` sent on creation). Users created before migration 021 get pt-BR. |

## Did not solve?

Collect the logs (`docker compose logs api` or `kubectl logs <pod>`) and the output
of `GET /ready` with `X-API-Key`. In a 500 error, look for the `error_id`
that came in the response: it marks the line with the stack trace.
