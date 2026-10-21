# Integration Incident Copilot — User Guide

> English translation of [`docs/USER_GUIDE.md`](../USER_GUIDE.md) · source commit `d67fea8` · [Português](../USER_GUIDE.md)
> If this page and the Portuguese source differ, the code and the Portuguese source prevail.

> Operational guide · For integration architects, support analysts and SAP sustainment teams

---

## What it is

The **Integration Incident Copilot** is an incident diagnosis agent for
integration. Given an incident — free text description and, optionally,
structured data from an SAP or multi-vendor system — it retrieves the most
relevant troubleshooting case from a knowledge base, analyzes the context
via LLM and returns a probable root cause with concrete next steps.

It is not an automatic correction tool. It is a diagnosis accelerator —
reduces the time spent in initial triage of integration incidents, which in
complex SAP landscapes can involve multiple systems, distributed logs
and protocol-specific knowledge (IDoc, RFC, OData, OAuth2).

---

## For whom

- **SAP integration architects** who need a quick entry point for
  CPI/Integration Suite, iFlow, RFC and IDoc incidents
- **N2/N3 support analysts** who receive integration tickets without
  sufficient context
- **SAP sustainment teams** that maintain heterogeneous landscapes (ECC,
  S/4HANA, SuccessFactors, non-SAP systems)
- **SAP BTP/CAP developers** who integrate services via OData v4,
  Event Mesh and XSUAA
- **Other AI agents** — via MCP and A2A, the copilot is a tool that
  a larger agent can call (see "Operation surfaces")

---

## How it works

The real pipeline has more guardrails than a simple RAG, and each is
visible in the response:

**1. Supervisor (deterministic, no LLM)** — classifies the incident into
domain (`sap`, `saas` or `generic`) from `interface_type` and
keywords. It is code, not model: the routing is cheap, explainable and
testable.

**2. Connector** — if a source system and identifier are provided,
the agent fetches structured incident data from that system (error code,
status, message). Without credential configured, the connector operates in
demo/mock mode and the evidence is marked `simulated`. There are 10 connectors:
OData, RFC, ServiceNow, Salesforce, Workday, Ariba, SuccessFactors, CAP,
API Management and SAP PO/PI (variables in `docs/CONNECTORS.md`).

**3. Rule engine (22 rules, no LLM)** — before calling the model, a
deterministic catalog of known SAP/integration errors is consulted.
If the incident matches a rule (ex: IDoc status 51, `RFC_COMM_FAILURE`,
expired OAuth2 token), the response returns **without consuming a single token** — fast
and with `prompt_digest`/`prompt_version` as `null` in the response, because
there was no prompt at all (DA-53).

**4. Retrieval (hybrid RAG + reranker)** — the text is searched by
dense similarity AND exact terms (BM25), fused via RRF and
reordered by a cross-encoder. Exact technical terms
(`RFC_COMM_FAILURE`, `status 51`) weigh as much as semantics.

**5. Diagnosis (LLM with code guardrails)** — the retrieved document
grounds the model (`qwen3.6:35b-a3b`). Confidence is
adjusted deterministically in code, never in the prompt: the ceiling is always
`evidence_strength + 0.25`; identifier not recognized by the connector
(fallback) limits to 0.4; without document, connector or rule, to 0.3; and a
`matched_source` cited that was not retrieved is nullified and limits to 0.3.

**6. Evidence layer** — each assertion in the response carries provenance:
real connector (`system_observed`), mock (`simulated`), RAG document
(`retrieved_document`), web search (`web_untrusted`) or just the user text
(`user_reported`). The `is_grounded` field is never LLM self-assessment.

---

## Operating

### Start the server

```bash
uv run uvicorn app.main:app --reload   # development
```

- Web UI: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Liveness/readiness: `GET /health` and `GET /ready`

### Authentication (read before the first curl)

Each credential in its layer (DA-18 + DA-54):

- **Web UI user** — does not type any key: the UI opens in a
  **standalone login screen** (DA-54) — the entire app (form,
  history, stack) only renders **after** the session exists. The login
  (`user + password`) returns an **HttpOnly** session cookie, signed
  (HMAC-SHA256) and with expiration (8h default); the browser sends it alone,
  `Logout` deletes it, and an expired session in use returns to the login
  screen with warning.
- **Machine/integration** — header `X-API-Key` in `/diagnose`,
  `/llm/policy` and `/incidents/{id}/verify` (DA-18). If `API_KEY` is
  empty in `.env`, a random key is generated at startup and the WARNING logs it
  **masked** (`****`) — the endpoint never stays open, but the key
  is not recoverable: fix `API_KEY` in `.env`. With `REQUIRE_AUTH=true`, the
  process **refuses to start** without `API_KEY`, `A2A_API_KEY` and
  `EVENT_MESH_API_KEY` configured.

Keys by surface (machine credentials — the web UI **does not** use
any of them):

| Surface | Header | Setting |
|---|---|---|
| `/diagnose`, `/llm/policy`, `/incidents/{id}/verify`, `/mcp` | `X-API-Key` | `API_KEY` |
| `/a2a` | `X-A2A-Api-Key` | `A2A_API_KEY` |
| `POST /events/incident` | `X-Event-Mesh-Api-Key` | `EVENT_MESH_API_KEY` |
| `/admin/api/*` | `X-API-Admin-Key` | `ADMIN_API_KEY` |

The `/admin/*` pages are data-free screens; all data comes from `/admin/api/*`.
The admin key typed in the UI stays in the browser `sessionStorage` and is
sent in each call.

User login (session cookie): `POST /auth/login` (user+password →
cookie), `POST /auth/logout`, `GET /auth/session` (who is logged in). The
login is **fail-closed**: without any user source, 401 always.

**Where users come from (DA-55):** the admin maintains the `web_users` table
(screen `/admin/users`, admin key), and the first entry of each requires
**two-step activation**: token sent to email (24h) and then 6-digit code to
phone (10 min) — both confirmed on the login screen ("Activate account"). The email token goes through `EMAIL_PROVIDER`
(`mailpit`/`resend`); only without provider or with send failure it appears to
the admin on the `/admin/users` screen. The phone code has no SMS provider and
**always** goes **out-of-band**: appears to the admin, who delivers it to
the user by the channel they control.
The `.env` (`WEB_UI_USERS`, DA-54) continues to work as bootstrap of
the operator — the operator never gets locked out.

The session cookie **does not** work in MCP, A2A, Event Mesh or admin — those
surfaces continue to require their dedicated keys. The user admin
(`/admin/users`) uses the `X-API-Admin-Key` (DA-46), not the session.

### Web interface

The UI at `http://localhost:8000` has a login screen and three work screens:

**Login screen** (before any other, DA-54) — user + password; without
session, nothing of the app renders. After it:

**Diagnosis** — clean form (only the incident: description, source system
with the 10 connectors, optional identifier), with file attachment
(`.txt`/`.log`/`.xml`/`.json`/`.csv`, up to 50 KB, sent as `logs` or
`payload`) and advanced fields `logs`/`payload` — no credential in the user's
hand; the HttpOnly session cookie flows alone.

Besides the root cause and next steps, the result shows:

- **Escalation signal** (DA-44): "Sufficient evidence" or "Human review
  recommended", with the reason. Informative: no paid model is called because
  of it.
- **Evidence**, each item with its `trust_level` (table in "Evidence and trust
  levels"). `simulated` data (demo connector) and `web_untrusted` (web search,
  DA-71) get a warning.
- **Provenance** (DA-53): "Deterministic rule · no LLM · zero cost" or
  provider, model, prompt version and digest.

AI Gateway refusals show their cause, not "internal error": diagnosis budget
exhausted (`budget_exhausted`, DA-67) or no provider allowed by the
sovereignty policy (`policy_violation`). Unexpected errors show the reference
(`error_id`) that locates the server log.

**Language** — the interface opens in the browser language (Brazilian
Portuguese or English; other languages open in English). The selector in the
sidebar and on the sign-in screen switches the language, and the choice also
applies to the Admin UI (`iic_locale` cookie). Dates, numbers and USD values
follow the chosen language. So does the diagnosis: the UI asks for the content
(root cause, next steps, report) in the screen language. A diagnosis already on
screen does not change when you switch languages — run it again. In English,
the LLM answer uses a prompt not yet measured by promptfoo (the quality numbers
apply to Portuguese).

**History** — diagnoses of the current session, with confidence, root cause
and a "human review" mark when the signal asked for it.

**Stack status** — state of connectors (real vs. demo/mock), infrastructure
(LLM provider, GraphRAG, Langfuse and RQ queue) and **data sovereignty**
(`GET /llm/policy`): mode, each provider's origin and whether it may receive
public or confidential data.

### REST API

```bash
curl -X POST http://localhost:8000/diagnose \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "description": "iFlow calling OData API_SALES_ORDER_SRV without $filter times out the 30 s limit when fetching ~45,000 records",
    "interface_type": "odata"
  }'
```

Without identifier and without matching rule, the case goes to the LLM. Response (main fields,
illustrative values):

```json
{
  "probable_root_cause": "OData query without $filter/$top brings excessive volume...",
  "model_confidence": 0.95,
  "diagnosis_confidence": 0.79,
  "next_steps": ["Add $filter and pagination ($top/$skip) to the query", "..."],
  "matched_source": "odata_timeout_cpi.md",
  "evidence_strength": 0.83,
  "llm_model": "qwen3.6:35b-a3b",
  "prompt_version": "1.0.0",
  "prompt_digest": "sha256:...",
  "incident_id": "8f14e45f-ea...",
  "trace_id": "...",
  "report_markdown": "..."
}
```

Fields that deserve attention:

- **`diagnosis_confidence` vs `model_confidence`** — `model_confidence` is the
  LLM self-assessment already limited by code guardrails;
  `diagnosis_confidence = evidence_strength × model_confidence` (above,
  0.83 × 0.95 ≈ 0.79) and is the recommended value for automation. Diverging is
  normal.
- **`prompt_digest`/`prompt_version` as `null`** — the rule engine responded
  without LLM (`matched_source` `rule_engine:<category>`, `llm_provider_used`
  `rule_engine`; ex.: "IDoc stuck with status 51" matches the rule
  `sap_idoc_status_51`). It is not an error: it is the fast and deterministic path.
- **`incident_id`** — keep it for the verification loop (below).
- Request also accepts: `logs`, `payload`, `sensitivity_level`
  (`public`/`internal`/`confidential`/`secret`). It **only elevates** the
  classification done by the gateway, never downgrades: `public` coming from the client
  does not release cloud provider for data that the gateway treats as
  `confidential` (GOV-01, DA-43),
  `connector_source_system` (`system_key` from admin catalog, improves
  incident correlation, DA-50), `locale` (`pt-BR` or `en`, accepts a browser
  tag such as `en-US`; language of the diagnosis content, default `pt-BR`; any
  other language returns 422 — DA-74). The response carries the `locale` the
  content was produced in.

**Asynchronous diagnosis** — same body, immediate response with `job_id`.
Requires `REDIS_URL` and an RQ worker (profile `async`); without it, 503:

```bash
curl -X POST http://localhost:8000/diagnose/async -H "X-API-Key: $API_KEY" ...
curl http://localhost:8000/diagnose/async/{job_id} -H "X-API-Key: $API_KEY"
```

### Command line

```bash
uv run python -m app.agent.graph \
  --interface rfc \
  --id RFC-IDOC-51-DEMO \
  --debug \
  "IDoc stuck with status 51"
```

`--interface` accepts the same 10 connectors as the API; `--model` overrides the
LLM; `--debug` prints the prompt sent. Does not require API key (calls the graph
directly).

### MCP — for AI agents to use the copilot

The MCP server is at `http://localhost:8000/mcp` (Streamable HTTP) and uses the
**same `X-API-Key`** as `/diagnose`. Tools are denied by default —
the capability catalog is fail-closed (DA-27). Client configuration
(ex.: `.mcp.json`) is in `CLAUDE.md` (DA-19): **fix `API_KEY` in `.env`**
or the client does not authenticate.

### A2A — agent talking to agent

`POST /a2a` implements JSON-RPC 2.0 (Agent2Agent), authenticated by
`X-A2A-Api-Key`. The public agent card — what another agent reads to
discover how to call this — is at
`GET /.well-known/agent-card.json` (with absolute URL when
`A2A_BASE_URL` is configured, requirement of A2A 0.3 spec).

### Events — event-driven diagnosis

- **Webhook:** `POST /events/incident` receives CloudEvents 1.0
  (required: `specversion=1.0`, `id`, `source`,
  `type=com.sap.integration.incident.detected.v1` and `data.description`),
  authenticated by `X-Event-Mesh-Api-Key` (`EVENT_MESH_API_KEY`), and triggers
  `run_diagnosis()` without anyone calling curl (DA-23). With `REDIS_URL`, the
  event is queued and the 202 returns `job_id`.
- **AMQP 1.0:** asynchronous consumer via Solace Cloud (`app/events/
  amqp_consumer.py`), for those who already have corporate event mesh.

### Closing the loop: verification

After resolving the incident, **register the outcome** — this is what separates
an assistant from a learning system:

```bash
curl -X POST http://localhost:8000/incidents/{incident_id}/verify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "root_cause": "OData query without $filter/pagination",
    "verified_by": "human",
    "correct": true
  }'
```

- `verified_at` is **always** recorded; `correct` is optional — leave `null`
  when only the cause was confirmed, but the outcome is not yet (forcing `true`
  inflates dashboard accuracy, DA-50).
- `verified_by`: `human` or `system`.
- Optionally `trace_id` to annotate the trace in Langfuse (requires also
  `correct` not null).

The verified history appears at `http://localhost:8000/admin/incidents`,
correlated to the integrated system when `connector_source_system` was
provided.

### Administration

The admin UI (`/admin`; data via `/admin/api/*` with `X-API-Admin-Key`) exposes:

- **Models** — registry of models/credentials by ORIGIN, with status. With
  `LLM_REGISTRY_DB=true`, every enabled model is a candidate for the agent
  (DA-72) and the screen sets the parameters the decision uses: transport,
  context window, capabilities (they only restrict the origin), served domains,
  tier, priority and monthly limit (blocks at 100%). It also holds the
  per-diagnosis budget, the per-call cap, the cost × latency weights and the
  **routing simulator**, which shows each model's rank or rejection reason
  without calling the LLM
- **Usage** — metering of **real** tokens (usage_metadata), not estimate
- **Systems** — catalog of integrated systems and their correlation with
  incidents (DA-49/50)
- **Incidents** — persisted history of diagnoses with verification
- **Users** (`/admin/users`) — web UI users and activation (DA-55)
- **Web search** (`/admin/web-search`) — web search sources by
  `interface_type`, fail-closed (DA-57)

Auxiliary operations by script:

- **SAP contract drift** (DA-52): `uv run python
  scripts/check_contract_drift.py` — compares live `$metadata` with the
  baseline; only **breaking** change opens incident.
- **Reports**: `uv run python scripts/generate_reports.py`

### Teaching the copilot: new knowledge base

The copilot only knows what is indexed. New resolved case → document in
`data/sample_docs/` → reindex:

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

---

## How to build a good prompt

The quality of the diagnosis depends directly on the quality of the
incident description. The agent does vector search — the more specific technical
context you provide, the more precise the retrieval of the relevant case.

### General principle

> Describe the incident as you would describe it to a SAP integration specialist colleague who does not have access to the system at the moment — with the exact error,
> the involved system, and what you have already tried.

### What to include

**Error code or status** — it is the strongest signal for retrieval. If
you have a code, put it in the description, even if it is already in
the identifier.

Bad:
> "iFlow not working"

Good:
> "iFlow returning HTTP 401 when trying to authenticate via OAuth2 in SAP Integration Suite"

---

**Protocol or adapter name** — RFC, IDoc, OData, SOAP, REST, AS2. The
agent has documents specific to each protocol.

Bad:
> "Integration error between ECC and S/4HANA"

Good:
> "RFC communication failure between ECC 6.0 and S/4HANA — destination SM59 returning connection refused on port 3300"

---

**Specific system status or code** — for IDocs, the numeric status is
critical (51, 02, 53). For OData, the HTTP status code. For RFC, the exception
code (RFC_COMM_FAILURE, SYSTEM_FAILURE).

Bad:
> "IDoc with problem"

Good:
> "ORDERS05 IDoc stuck with status 51 — SAPSLL: material not found in distribution center 1000"

---

**What has already been tried** — helps the agent calibrate the next steps and
avoid suggesting what you have already done.

Bad:
> "RFC not connecting"

Good:
> "RFC connection refused on destination SM59. We have already verified that the SAP dispatcher is active via SM50. Suspected firewall block on port 3300."

---

**Destination system** — especially in multi-system landscapes,
identifying the target system helps.

Bad:
> "timeout calling OData service"

Good:
> "timeout calling OData service /sap/opu/odata/sap/API_SALES_ORDER_SRV on the S/4HANA SAP Gateway — query without filter returning more than 50k records"

---

### Complete examples

**Example 1 — OData authentication incident**

```
Description: iFlow in SAP Integration Suite returning HTTP 401 when trying to
consume OData service from S/4HANA. The adapter is configured with
OAuth2 Client Credentials. The call worked yesterday. Suspected
expired token or invalid credential in Security Material.

System: OData
Identifier: CPI-401-DEMO
```

Why it is good: specifies the protocol (OAuth2 CC), the system (Integration
Suite → S/4HANA), the error code (401), and a hypothesis.

---

**Example 2 — Stuck IDoc**

```
Description: ORDERS05 IDoc with status 51 in the receiving system S/4HANA.
Application error: "Material 4711 not found in center 1000".
IDoc sent via partner port to destination RECEPCAO_01. Attempt
of reprocessing via BD87 failing with the same error.

System: RFC
Identifier: RFC-IDOC-51-DEMO
```

Why it is good: IDoc type, exact status, error message literal,
receiving system, action already attempted.

---

**Example 3 — RFC connection refused**

```
Description: SM59 cannot establish RFC connection to the
target system PRD. Connection test returns connection refused on
port 3300. The PRD system is active (verified via SSHADMIN).
Suspected firewall rule changed today during maintenance window.

System: RFC
Identifier: RFC-CONN-REFUSED-DEMO
```

Why it is good: SAP diagnostic code (SM59), specific port (3300),
verification actions already performed, temporal context (maintenance window).

---

**Example 4 — Incident without identifier**

```
Description: Integration iFlow between SuccessFactors and SAP HCM stuck
with timeout after 30 seconds. The iFlow calls an OData service from SAP
HCM via Cloud Connector. No filters in the query — it brings all
active employees (approximately 45,000 records). The problem
started after the employee base grew above 40,000.

System: No connector
```

Why it is good: even without identifier, the description contains enough
for the RAG to retrieve the OData timeout document (data volume,
absence of filter, expected behavior).

---

### What to avoid

**Ambiguous language without technical reference**

> "the system is slow" — which system? which operation? which protocol?

**Only the error code without context**

> "error 500" — 500 of which system? OData, CPI, custom app?

**Description at business level without technical detail**

> "purchase order integration not working" — without protocol,
> system, error code or specific behavior

---

## Typical use cases

### Stuck IDoc (status 51)

Occurs when the IDoc reaches the receiving system but fails in the application —
usually missing master data (material, supplier, accounting account) or
business validation.

What to inform: IDoc type (ORDERS05, MATMAS, DEBMAS), numeric status
(51), error message from WE05 transaction, receiving system.

Demo identifiers: `RFC-IDOC-51-DEMO`

### OData timeout

Occurs when an unfiltered OData query brings excessive data volume, or
when the backend system is overloaded.

What to inform: OData service URL, configured timeout time,
presence or absence of `$filter` and `$top` filters, estimated number of
records.

Demo identifiers: no specific identifier (use textual description)

### CPI authentication failure (HTTP 401)

Occurs when the OAuth2 token expires and the adapter does not automatically renew,
or when the Security Material credentials are incorrect/expired.

What to inform: authentication type (OAuth2 CC, Basic Auth, Certificate),
destination system, if there was recent credential rotation.

Demo identifiers: `CPI-401-DEMO`

### RFC connection refused

Occurs when the target SAP system is inaccessible on port 3300 —
instance stopped, firewall, network change.

What to inform: host and port of the target system, SM59 test result,
if the system is active (SM50/SM51), recent infrastructure changes.

Demo identifiers: `RFC-CONN-REFUSED-DEMO`

### RFC gateway pool timeout

Occurs when all connections in the SAP Gateway worker pool are
occupied — usually under high load or hanging RFC processes.

What to inform: exact gateway error message, incident time
(peak usage?), SM66 result (active processes).

Demo identifiers: no specific identifier (use textual description)

---

## Interpreting the result

### Confidence badge

The agent returns `diagnosis_confidence` between 0 and 1, displayed as a
colored badge in the UI:

- **High (green, ≥ 70%)** — document retrieved with high similarity and case
  well covered by the base. Follow the next steps with confidence.
- **Medium (yellow, 40–69%)** — the document is relevant but the match is not
  perfect. Use the next steps as a starting point.
- **Low (red, < 40%)** — the incident does not match well any documented case,
  or the identifier was not recognized. Consider adding more context to the description.

### Artificially limited confidence

Situations where confidence is automatically reduced, regardless of the
LLM analysis:

- Always → ceiling of **`evidence_strength` + 0.25**
- Identifier not recognized by the connector (fallback) → maximum **0.4**
- Without retrieved document, without connector and without rule → maximum **0.3**
- `matched_source` cited that was not retrieved → nullified, maximum **0.3**
- Connector in demo/mock mode → does not limit confidence, but the evidence comes
  marked `simulated`

### Evidence and confidence levels

The response brings `evidence`: the list of what grounds the diagnosis, each
item with a `trust_level`:

| trust_level | Source |
|---|---|
| `system_observed` | Real connector (not mock) OR rule engine — the strongest |
| `simulated` | Connector mock or fallback — example data, not real |
| `retrieved_document` | RAG chunk (Qdrant) or GraphRAG history |
| `web_untrusted` | Web search — treat as a hint, not a fact |
| `user_reported` | The incident text description — the weakest |

A diagnosis with only `user_reported` evidence is saying "I am
working with what you told me, nothing else".

### API errors: `code` and `error_id`

`/diagnose` errors carry `detail` and a stable `code`:

| HTTP | `code` | Meaning | Retry? |
|---|---|---|---|
| 503 | `budget_exhausted` | The diagnosis budget (`DIAGNOSIS_BUDGET_USD`, DA-67) does not cover any eligible provider | Not by itself: raise the budget or enable a cheaper provider |
| 503 | `policy_violation` | No provider allowed by the sovereignty policy for the incident's sensitivity (`GET /llm/policy`) | No: adjust the policy or the local provider |
| 503 | `overloaded` | Capacity exhausted | Yes, after `Retry-After` |
| 504 | `timeout` | The pipeline exceeded `DIAGNOSIS_TIMEOUT_SECONDS` | Yes |
| 500 | `internal_error` | Unexpected failure | Report the `error_id` |

`budget_exhausted`, `policy_violation` and `internal_error` carry `error_id`:
the same id is in the API log, with the full reason (which names internal
origins and is therefore kept out of the response).

### Reference document

The `matched_source` field indicates which document from the knowledge base was
used as reference. If you want to understand the diagnosis more deeply,
this document contains the complete case with causes, diagnosis and
typical resolution.

---

## Where logs are — by architecture layer

When something fails, each layer records in a different place. This section
says where to look, in the order an incident crosses the solution.

### 1. API and agent — one process, one stdout

All orchestration (`/diagnose`, the LangGraph graph, the rule engine, the MCP
in `/mcp` and the A2A in `/a2a`) runs in **a single process** and logs to its stdout
— MCP and A2A are mounted inside the FastAPI app, they are not separate
processes.

- **Local development:** the terminal where `uv run uvicorn` runs
  (or the file to which you redirected).
- **Staging/container:** `docker compose logs api`, or
  `docker logs integration-incident-copilot-api-1`.

What appears there:

- **HTTP access log with status.** A line
  `"POST /diagnose HTTP/1.1" 401 Unauthorized` is missing or wrong key
  (DA-18); `422` is body rejected by validation (ex.:
  invalid `interface_type`); `429` is rate limit (10/min per IP).
- **WARNING of key generated at startup.** If `API_KEY` is empty in `.env`,
  the process generates a random key and the log shows it **masked** — it
  is not recoverable from the log. Fix by setting `API_KEY` in `.env` (with
  `REQUIRE_AUTH=true` the process does not start without it).
- **LLM Gateway decisions:** routing by data sensitivity (DA-43),
  circuit breaker opening/closing and budget (DA-26) — at
  `INFO`/`WARNING` level.
- **Hybrid Inference fallbacks:** when local Ollama fails and the
  call falls back to cloud (DA-20).

**Log and prompt hygiene (DA-30):** logs and incident payload are sanitized and truncated to **3,000 characters** before entering the prompt (smart truncation) — the log never carries the entire payload;
PII is redacted before reaching the gateway.

### 2. LLM tracing — Langfuse

Each diagnosis has `trace_id` in the response. With `LANGFUSE_*` configured,
the entire pipeline appears in the Langfuse UI at `http://localhost:3000` —
search for the response `trace_id`: spans per node, prompt sent, tokens
and cost. Without `LANGFUSE_*` configured, there is no tracing (spans are
not sent anywhere) — the stdout of section 1 is all that exists.

### 3. Operational data — Postgres + Grafana dashboards

It is not raw log: it is the **queryable** history of each diagnosis.

- **`incidents`** — diagnosis + verification + correlation with the
  integrated system (DA-50). Screen: `/admin/incidents`.
- **Real token metering** by origin/model (DA-46/48, no estimate).
  Screen: `/admin/usage`.
- **`system_contracts`** — SAP contract baseline and drift (DA-52).
- **Grafana** (`observability` profile): `http://localhost:3002` — 4
  dashboards (SOC, iPaaS, COI, Systems); the 45 queries are validated
  against the real Postgres in CI.

### 4. Asynchronous processes — separate containers

- **RQ worker** (`async` profile, executes `/diagnose/async`):
  `docker compose logs worker`.
- **AMQP 1.0 consumer** (Solace, `app/events/amqp_consumer.py`): stdout
  of its process.
- **Reporter:** in addition to the log, writes ready reports in `reports/`
  (Markdown/Excel daily).

### 5. Local infrastructure — host containers

- **Qdrant:** Qdrant container log — index/embedding errors appear
  here (degraded RAG, not API error).
- **Ollama:** the **host** service responds on `11434` (the compose
  `ollama` service only starts with `--profile container-ollama`) — model/memory
  errors appear in the service log, not in the API log.
- **Neo4j** (opt-in, GraphRAG): `docker compose --profile graphrag logs
  neo4j` — off by default; without it the copilot only loses the history
  in graph, not the diagnosis.

### 6. Staging/production on Kyma (DA-24)

Kubernetes standard — the access log is the same as section 1, only changes where it lives:

```bash
kubectl logs -n integration-incident-copilot deployment/integration-incident-copilot
kubectl logs -n integration-incident-copilot deployment/integration-incident-copilot-worker
```

The namespace and deployments (`integration-incident-copilot`,
`integration-incident-copilot-worker`) are in the manifests of
`deploy/kyma/`.

---

## Known limitations

**The agent diagnoses, does not correct** — the next steps are recommendations;
execution is always by the responsible analyst/architect.

**The knowledge base is limited** — the agent only knows what is
indexed in `data/sample_docs/`. Very specific customer customization incidents or
errors from old versions may not have correspondence.

**Connectors in mock mode** — without credential configured, the connector returns
simulated data. The diagnosis works, but is based on example data —
and the evidence is marked `simulated` so you know it.

**SuccessFactors without end-to-end validation** — the connector has full settings and
pipeline, but was never validated against a real tenant.

**No access to real-time logs** — the agent does not read SM21, ST22 or the
Integration Suite monitoring. The system data comes from the connector via
API/RFC, not log reading.

**Not an ITSM ticket system** — incidents **are** persisted (with
`incident_id`, verification and correlation with systems in `/admin/incidents`),
but there is no native integration with ITSM (ServiceNow/Jira) to open or
update tickets: ServiceNow is a connector of **incident source**, not
destination.

---

## Glossary

**IDoc** (Intermediate Document) — standard SAP message format for data exchange
between systems via EDI or ALE. Each IDoc has a type (ex: ORDERS05,
MATMAS) and a numeric status indicating its processing state.

**RFC** (Remote Function Call) — SAP proprietary protocol for function calls
between ABAP systems. Uses port 3300 (instance 00) by default.

**OData** — standardized REST protocol (Open Data Protocol) used by SAP
Gateway to expose ABAP data via HTTP/JSON.

**iFlow** — integration flow in SAP Integration Suite (CPI). Each iFlow
orchestrates the transformation and routing of messages between systems.

**OAuth2 Client Credentials** — machine-to-machine authentication flow where
the client system authenticates with client_id and client_secret to obtain an
access token.

**Security Material** — SAP Integration Suite credential repository
where passwords, certificates and tokens are stored for use in iFlow adapters.

**SM59** — SAP transaction to manage RFC destinations (connections to external
systems).

**BD87** — SAP transaction to reprocess IDocs with application errors.

**WE05** — SAP transaction for IDoc monitoring.

**RAG** (Retrieval-Augmented Generation) — technique that combines document
retrieval (vector search) with text generation by LLM. The agent uses RAG
to ground the diagnosis in documented cases, not the model's "general knowledge".

**XSUAA** — SAP BTP (Business Technology Platform) authentication and authorization
service, based on OAuth2/OIDC.

**MCP** (Model Context Protocol) — protocol to expose tools to
AI agents. The copilot is MCP server in `/mcp`, with fail-closed capability catalog (DA-27/19).

**A2A** (Agent2Agent) — protocol for different AI agents to cooperate.
The copilot is A2A agent in `/a2a` (JSON-RPC 2.0), with public agent card in
`/.well-known/agent-card.json`.

**CloudEvents** — CNCF specification for describing events in a common
format. The webhook `POST /events/incident` (DA-23) receives it.

---

*Integration Incident Copilot · github.com/marcos-slima/integration-incident-copilot*
