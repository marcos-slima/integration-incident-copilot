# Architecture Decisions

> English translation of [`docs/DECISOES_DE_ARQUITETURA.md`](../DECISOES_DE_ARQUITETURA.md) · source commit `cde10c9` · [Português](../DECISOES_DE_ARQUITETURA.md)

Record of architecture decisions (ADs) for the Integration Incident Copilot:
the real problem encountered, the solution adopted, how it was validated, and the
accepted limitations. It is the "why" of the project; the "what" and "where" are in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

> Moved from `README.md` on 2026-10-07: the README became the repository's
> presentation page. The content of the sections did not change. The gates
> `implemented_das_documented` and `das_index_current` (DA-51) now read this
> file. DAs 32 to 35 have the prose in `ARCHITECTURE.md`, declared
> alternative location.
>
> Numbered sections are the record at the time of the decision: test numbers and
> names cited there are from the time. The current state is in the code, in
> `ARCHITECTURE.md` and in the tests.

## Index

Record of real problems encountered during development and
how they were solved — engineering process, not just the final
result.

> `CLAUDE.md` without its own section fails the build. `—` = registered and still without
> prose. `informal note` = real decision that never received a DA. DAs grouped
> in a section (e.g., DA-15/16/17, DA-46/47/48) share its number.

> Sections not yet translated (after §23) link to the Portuguese record,
> [`DECISOES_DE_ARQUITETURA.md`](../DECISOES_DE_ARQUITETURA.md).

| DA | Section | What is it |
|---|---|---|
| 1 | [1](#1-hallucination-by-context-mixing-da-1) | RAG top-1 (avoids context mixing) |
| 2 | [2](#2-non-determinism-with-temperature0-da-2) | `seed=42` required for Ollama determinism |
| 3 | [3](#3-deterministic-guardrail-for-fallback-data-da-3) | Guardrails in code, not in prompt |
| 4 | [4](#4-formal-model-comparison-qwen330b-a3b-vs-qwen25-coder32b-da-4) | Model comparison via promptfoo: `qwen2.5-coder:32b` won against |
| 8 | [8](#8-second-model-comparison-qwen3635b-a3b-evaluated-and-rejected-da-8) | Model comparison via promptfoo: `qwen2.5-coder:32b` won against |
| 12 | [47](../DECISOES_DE_ARQUITETURA.md#47-a-troca-de-modelo-que-não-tinha-número-da-12) | Final switch of canonical model to `qwen3-coder-next:latest` (MoE 80B/3B, 262K ctx) — technical tie 10/10, decided by roadmap |
| 14 | [14](#14-a2a-agent2agent-layer-implemented-with-the-caveat-of-ga-preserved-da-14) | A2A (Agent2Agent) JSON-RPC 2.0 layer |
| 15 | [15](#15-evidencetrust-layer-between-ragconnectors-and-the-llm-da-151617) | Evidence/Trust Layer deterministic |
| 16 | [15](#15-evidencetrust-layer-between-ragconnectors-and-the-llm-da-151617) | `is_grounded` via evidence_strength (never LLM self-assessment) |
| 17 | [15](#15-evidencetrust-layer-between-ragconnectors-and-the-llm-da-151617) | Fallback to reference_library when evidence is weak |
| 18 | [16](#16-authentication-of-diagnose-and-a2a-always-required-with-automatic-key-generation-da-18) | Auth X-API-Key mandatory in `/diagnose` and `/a2a` |
| 19 | [17](#17-mcp-server-model-context-protocol---capability-catalog-read-first-da-19) | MCP Server (capability catalog) |
| 20 | [18](#18-hybrid-inference---resilience-fallback-between-llm-providers-da-20) | Hybrid Inference: local Ollama → cloud fallback |
| 21 | [19](#19-graphrag-as-layer-of-operational-knowledge---hardening-da-21) | GraphRAG hardening: `(DriverError, TransientError)` vs `Neo4jError` |
| 22 | [20](#20-multi-agent---supervisor--specialists-by-domain-da-22) | Multi-agent: supervisor → sap/saas/generic (no LLM) |
| 23 | [21](#21-event-mesh---event-driven-ingestion-da-23) | Event Mesh via CloudEvents webhook → `run_diagnosis()` |
| 24 | [22](#22-deploy-in-production---sap-btp-kyma-runtime-da-24) | Deploy SAP BTP Kyma Runtime |
| 25 | [24](../DECISOES_DE_ARQUITETURA.md#24-evidencetrust-layer--correção-do-threshold-do-rag-antes-do-reranker-da-25) | Evidence/Trust Layer v2 + RAG threshold post-reranker |
| 26 | [25](../DECISOES_DE_ARQUITETURA.md#25-ai-gateway-v1---policy-de-roteamento-circuit-breaker-e-budget-da-26) | AI Gateway v1: policy + circuit breaker + budget |
| 27 | [26](../DECISOES_DE_ARQUITETURA.md#26-capability-registry--agent-execution-policy-da-27) | Capability Registry FAIL-CLOSED |
| 28 | [27](../DECISOES_DE_ARQUITETURA.md#27-graphrag---modelo-verified_as-da-28) | GraphRAG model `VERIFIED_AS` + endpoint `/incidents/{id}/verify` |
| 29 | [28](../DECISOES_DE_ARQUITETURA.md#28-benchmark-científico-de-rerankers-da-29) | Reranker benchmark → mmarco-mMiniLMv2 wins (+7pp Hit@1) |
| 30 | [29](../DECISOES_DE_ARQUITETURA.md#29-redaction-de-pii-antes-do-prompt-e-do-langfuse-com-truncamento-inteligente-e-backoff-da-30) | Expanded PII redaction + smart log truncation + exponential backoff |
| 32 | ARCHITECTURE | Async AMQP 1.0 consumer for Solace Cloud / SAP Event Mesh (protocol fixed by DA-40) |
| 33 | ARCHITECTURE | Deterministic Rule Engine (pre-LLM filter; 22 rules in `KNOWN_ERROR_RULES`) |
| 34 | ARCHITECTURE | SuccessFactors EC connector (OAuth2 Client Credentials + OData v2 PerPerson) |
| 35 | ARCHITECTURE | `/health` as real readiness probe (GET on services) + Rule Engine catalog expansion |
| 38 | [43](../DECISOES_DE_ARQUITETURA.md#43-backend-de-embedding-trocável-para-o-ci-não-depender-de-ollama-da-38) | `EMBEDDING_BACKEND=fastembed` for RAG eval job in CI, which doesn't have Ollama |
| 39 | [44](../DECISOES_DE_ARQUITETURA.md#44-soberania-de-dados-no-ai-gateway-o-modo-é-política-não-preferência-da-39) | Data sovereignty policy in AI Gateway (`strict` / `cloud_with_dlp`) |
| 40 | [45](../DECISOES_DE_ARQUITETURA.md#45-aiormq-não-é-amqp-10-a-falha-era-silenciosa-da-40) | Migration aiormq (AMQP 0.9.1) → python-qpid-proton (AMQP 1.0), with asyncio wrapper |
| 41 | [46](../DECISOES_DE_ARQUITETURA.md#46-circuit-breaker-com-backend-redis-compartilhado-da-41) | Circuit breaker with shared Redis backend (fallback in memory without infra mandatory) |
| 42 | [42](../DECISOES_DE_ARQUITETURA.md#42-escala-calibrada-por-sigmoid-para-o-rerank-score-da-42) | Scale calibrated by sigmoid for rerank score (no consumer uses the raw score) |
| 43 | [30](../DECISOES_DE_ARQUITETURA.md#30-governança-de-soberania-de-dados-por-origin-real-da-43) | Data sovereignty by real origin, fail-closed |
| 44 | [31](../DECISOES_DE_ARQUITETURA.md#31-sinal-determinístico-de-escalonamento-em-três-tiers-da-44) | Deterministic escalation signal in 3 tiers (prep. tier 3) |
| 45 | [32](../DECISOES_DE_ARQUITETURA.md#32-universalidade-de-provider-rota-auditada-capacidades-por-origin-identidade-de-embedding-da-45) | Provider universality: audited route + capabilities by origin |
| 46 | [33](../DECISOES_DE_ARQUITETURA.md#33-registro-gerenciado-de-modelos-credenciais-cifradas-e-metering-real-da-464748) | Managed registration of models/credentials by ORIGIN (LLM_REGISTR |
| 47 | [33](../DECISOES_DE_ARQUITETURA.md#33-registro-gerenciado-de-modelos-credenciais-cifradas-e-metering-real-da-464748) | Encrypted credentials at rest with Fernet (master key in .env, nu |
| 48 | [33](../DECISOES_DE_ARQUITETURA.md#33-registro-gerenciado-de-modelos-credenciais-cifradas-e-metering-real-da-464748) | REAL token metering (usage_metadata, not estimate) persisted |
| 49 | [34](../DECISOES_DE_ARQUITETURA.md#34-catálogo-de-sistemas-integrados-gerenciados-pela-superfície-admin-da-49) | Integrated systems catalog (`integration_systems`) in admin surface |
| 50 | [35](../DECISOES_DE_ARQUITETURA.md#35-correlação-de-incidentes-com-o-catálogo-de-sistemas--verificação-persistida-da-50) | Correlation `incidents` ↔ catalog by `system_key` (exact, from |
| 51 | [36](../DECISOES_DE_ARQUITETURA.md#36-quality-gates-transformar-alegações-de-qualidade-em-invariantes-verificadas-da-51) | Quality gates: machine-verified evaluation invariants (d |
| 52 | [37](../DECISOES_DE_ARQUITETURA.md#37-detecção-de-drift-de-contrato-sap-baseline-severidade-e-incidente-da-52) | SAP contract drift detection: probe `$metadata` (interface se |
| 53 | [38](../DECISOES_DE_ARQUITETURA.md#38-prompt-de-diagnóstico-como-artefato-versionado-com-gate-contra-o-prompt-medido-da-53) | Diagnostic prompt as versioned artifact: `PromptSpec` (vers |
| 54 | [39](../DECISOES_DE_ARQUITETURA.md#39-login-de-sessão-para-a-ui-web--cada-credencial-na-camada-dela-da-54) | Session login for web UI (`/auth/login` + HttpOnly cookie; X-API-Key continues for machines) |
| 55 | [40](../DECISOES_DE_ARQUITETURA.md#40-manutenção-de-usuários-pelo-admin-com-ativação-por-e-mail-e-telefone-da-55) | User maintenance by admin with two-step activation: token by email → code by phone (out-of-band until SMTP/SMS) |
| 56 | [41](../DECISOES_DE_ARQUITETURA.md#41-conector-sap-popi-o-middleware-on-premise-que-não-é-rest-first-da-56) | SAP PO/PI on-premise connector: Native Basic Auth against Message Monitor, with optional OAuth2 when API Management is in front |
| 57 | [48](../DECISOES_DE_ARQUITETURA.md#48-fontes-de-busca-web-como-configuração-e-approved-que-não-era-no-op-da-57) | Web search sources as configuration (`web_search_sources`): `WEB_SEARCH_POLICY=approved` ceases to be no-op |
| 58 | [49](../DECISOES_DE_ARQUITETURA.md#49-mapa-de-cobertura-dedicated-generic-e-absent-da-58) | SAP product × mechanism coverage map, calculated from versioned data: 3 levels (`dedicated` / `generic` / `absent`) instead of a boolean |
| 59 | [50](../DECISOES_DE_ARQUITETURA.md#50-conectores-multi-vendor-fluxo-completo-padrão-comum-e-documentação-da-59) | Multi-vendor connectors: full flow, common standard, 8-surface checklist when adding connector, consolidated doc `/docs/CONNECTORS.md` |
| 60 | [51](../DECISOES_DE_ARQUITETURA.md#51-criptografia-em-repouso-de-evidence_json-com-fernet-da-60) | Encryption at rest of `evidence_json` with Fernet (`LLM_CREDENTIALS_MASTER_KEY`) + idempotent migration `009_encrypt_evidence_json.py` |
| 61 | [52](../DECISOES_DE_ARQUITETURA.md#52-taxonomia-de-erros-sap-com-ttlrdflib-skos-para-rule-engine-da-61) | SAP error taxonomy with TTL/RDFLib (SKOS) for Rule Engine: dynamic loading vs hardcoded, zero breaking changes (DA-61 Phase 1) |
| 62 | [53](../DECISOES_DE_ARQUITETURA.md#53-neo4j-loader-para-skos-taxonomy-import-da-62) | Neo4j loader for SKOS taxonomy import (idempotent upsert, Cypher queries) |
| 63 | [54](../DECISOES_DE_ARQUITETURA.md#54-skills-declarativas--yaml-como-fonte-de-verdade-para-capability-e-roteamento-da-63) | Declarative skills — YAML as source of truth for capability and routing (DA-63) |
| 65 | [55](../DECISOES_DE_ARQUITETURA.md#55-degradação-graciosa-do-rag-quando-qdrant-cai-da-65) | Graceful degradation of RAG: Qdrant falls → `_retrieve_hybrid` and `_retrieve_dense_only` return `[]` → rule engine still responds known errors; full flow `/diagnose → run_diagnosis` pending integration test |
| — | — | DA-64: number unused (intentional skip) |

> **Prose ordering:** §§42–§47 (DA-42, DA-38, DA-39, DA-40,
> DA-41, DA-12) were retro-registered in a block, out of ascending
> DA order. The section number is the position in the file, not the DA number. DA-32 to
> DA-35 have the prose in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Decisions

### 1. Hallucination by context mixing (DA-1)

**Problem:** when passing the 3 most relevant documents (RAG top-3)
entirely in the prompt, the LLM occasionally combined root cause from
different documents (e.g., mixing IDoc and OData concepts into a
single answer), even with explicit instruction not to do so.

**Solution:** restrict the context passed to the LLM to only the
**most relevant document** (full text), citing the others only
by name, without content. Eliminated the possibility of mixing at the root,
by design, instead of relying on prompt instruction.

### 2. Non-determinism with temperature=0 (DA-2)

**Problem:** the same prompt, run twice with `temperature=0.0`
in Ollama, produced different answers — including a complete hallucination
in one of the runs. `temperature=0` does not guarantee full determinism
without an explicit `seed`.

**Solution:** fix `seed=42` in the `ChatOllama` call. Validated with
5 identical consecutive runs of the same scenario previously considered
unstable.

### 3. Deterministic guardrail for fallback data (DA-3)

**Problem:** when an SAP connector does not recognize an identifier
(mock scenario not mapped), the LLM sometimes still tried to link
to a specific document in the knowledge base with moderate-high confidence,
even when prompted not to do so.

**Solution:** do not rely solely on LLM self-assessment for this
security property. The code deterministically checks if the
connector returned fallback data (`ConnectorResult.is_fallback`)
and, in that case, **imposes a confidence ceiling (0.4)** independent of
what the model reports.

### 4. Formal model comparison (qwen3:30b-a3b vs qwen2.5-coder:32b) (DA-4)

**Context:** problems 1 and 3 above occurred specifically with
the `qwen3:30b-a3b` (MoE, ~3B active parameters). Before assuming that
the model was the root cause, a formal comparison was made using
[promptfoo](https://www.promptfoo.dev/), running the **real
pipeline** (connector + RAG + guardrails) against the two models,
not the LLM in isolation.

**Result:** in cases with clear match, the two models had equivalent
performance. In the critical case — unknown system identifier, no real
match in the knowledge base — the `qwen2.5-coder:32b` recognized alone the absence of
match (`matched_source: null`), while the `qwen3:30b-a3b`
tried to link to a specific document anyway (only not visible as a problem
because of the guardrail in item 3).

**Decision:** `qwen2.5-coder:32b` (dense, 32B parameters) adopted as
production model of the graph. Validated with the complete test suite
(16/16 `pytest`) after the switch. Accepted trade-off: longer inference time
(~2min49s vs ~1min20s in the 16 tests) in exchange for more reliable behavior
under uncertainty.

> **Superseded in Phase 12:** the production model was switched to
> `qwen3-coder-next:latest` after parity in promptfoo
> (10/10 PASS, `concurrency: 1`). The comparison above remains valid as
> historical record of the criterion used — `qwen2.5-coder:32b` is no longer
> the production model. Record of Phase 12 in the development process (internal document, outside the repository).

### 5. Real observability with Langfuse (informal note — no DA)

**Context:** Langfuse was configured from the beginning of the
project, but with no real code sending data there —
configuration present, tracing absent.

**Implemented:** each node of the graph (`connector`, `retrieve`,
`diagnose`, `report`) is instrumented with `@observe` (at the time; today the
`connector_node` has no decorator, `diagnose` node does not exist and spans
are `retrieve`, `graph_enrich`, `graph_write`, `web_search`,
`sap_specialist`, `saas_specialist`, `generic_specialist` and `report`, via
`@observe_span` in `app/agent/nodes.py`, under the root span
`sap_copilot_diagnosis` of `app/agent/graph.py`), and the LLM
call uses the LangChain `CallbackHandler` — capturing execution time,
tokens and the complete input/output payload of each step,
visible in `http://localhost:3000`.

**Bug found and fixed in the process:** the SDK did not do `flush()`
automatically before the process ended, so traces stayed in the buffer
and never reached Langfuse. Fixed by calling `get_client().flush()`
at the two points where the process can end: in the FastAPI shutdown lifespan (`app/main.py`) and at the end of CLI execution (`app/agent/graph.py`, block `if __name__ == "__main__"`) - not a test fixture (`pytest` brings up/tears down the app via `TestClient`, which already passes through the same lifespan).

### 6. Centralized configuration (eliminating hardcoded) (informal note — no DA)

**Problem found:** although a `.env` existed from the beginning
of the project, the code never read it — Qdrant URLs, LLM model and
other configurations were fixed as Python constants, scattered
in multiple files. Changing the model required editing source code
(`sed` directly in the file), not changing an environment variable.

**Solution:** `app/config.py`, a `Settings` class (via
`pydantic-settings`) as the single source of truth, read from `.env`. A
command (`uv run python -m app.config`) prints the effective configuration
at any time, with secrets masked — allows
checking what is actually configured without depending on source code
reading.

### 7. Security and CI before publication (informal note — no DA)

Before making the repository public:

- **`gitleaks`**: scan of **entire git history** (not just the
  current state) for leaked secrets — confirmed clean before
  the first push
- **`pre-commit`**: automatic hooks (lint/format via `ruff`,
  secret detection, block large file >5MB) running on
  every local commit, from then on
- **GitHub Actions**: CI workflow running lint + unit tests
  on every push/PR — the status badge at the top of `README.md` reflects the
  real result of the last execution, not a claim

### 8. Second model comparison: qwen3.6:35b-a3b evaluated and rejected (DA-8)

**Context:** months after the decision on `qwen2.5-coder:32b` (section 4),
Alibaba released the `qwen3.6:35b-a3b` (MoE, 36B total/3B active,
successor of the series that had been discarded in the first comparison).
Repeated the same formal process via `promptfoo`, against the same
real pipeline and the same 10 test cases — including the critical case
(`IDoc stuck` / RFC connector) repeated 3 times to measure
stability.

**Result:** in 7 of the 10 cases, equivalent or slightly superior performance
to the current model (more detailed answers,
confidence well calibrated in the case of unknown identifier security). However, in the critical case repeated 3 times, the
`qwen3.6:35b-a3b` **failed in all 3 executions identically**: the
model did not return a valid structured JSON
(`"Nao foi possivel estruturar a resposta do modelo"`,
`confidence: 0.0`), while the `qwen2.5-coder:32b` got it right all 3 times
with 90% confidence.

**Decision:** keep `qwen2.5-coder:32b` in production. A deterministic and reproducible failure
(3/3) in the most critical scenario of the
pipeline disqualifies the candidate, regardless of average performance
in other cases — reliability under the most demanding case weighs more
than average performance.

> **Superseded in Phase 12:** the production model was switched to
> `qwen3-coder-next:latest` (parity 10/10 in promptfoo), after this
> comparison. The criterion of this section — reproducibility in the critical case
> as disqualification requirement — remains valid and was reapplied in
> Phase 12.

**Value of the process, not just the result:** this comparison also
proves that the model decision is not static — it is revisited with
formal criterion whenever a relevant candidate emerges, with the same
methodology and the same real pipeline used since the first time,
generating comparable decisions over time.

### 9. Code review findings: global state, fragile parsing, missing limits (informal note — no DA)

An external code review identified 10 points; the triage separated
what was real from what was false alarm or had already been fixed:

- **False alarm:** claim that `report_node`/`run_diagnosis`
  would be absent from the file — not true, both exist and
  work (the reviewer probably saw a cut fragment, not the
  complete file)
- **Already fixed before review:** singleton in retriever and
  `ensure_collection` outside the batch loop (see previous sections)
- **Confirmed and fixed in this round:**
  - `LLM_MODEL` as module-level mutable global → injected via `state`/
    parameter in `run_diagnosis(..., llm_model=...)`, eliminating race risk
    between concurrent executions
  - Manual and fragile JSON parsing → real structured output, with the old manual parsing kept as *fallback*, no longer as the only path. The primary path is `create_react_agent(..., response_format=DiagnosisModel)`, which triggers an **additional call** to the LLM with `with_structured_output` (native provider tool-calling) and returns the `DiagnosisModel` already validated in `structured_response`; the regex only runs if this call fails
  - `confidence` without range validation → `Field(ge=0.0, le=1.0)` in the
    Pydantic schema **+** defensive clamp in code (same philosophy
    of layered guardrails already used for connector fallback,
    now extended)
  - `logs`/`payload` without size limit → `max_length` in Pydantic
    (rejects absurd input in API) and stricter truncation in prompt assembly (protects context/cost of LLM)
  - Zero HTTP layer test → `tests/test_api.py` with `TestClient`
  - `Dockerfile` did not copy `data/`, so the fallback of example
    documents would break in production → fixed, with explicit note
    that the 36GB library should never enter the image and that
    `.env` must be injected at runtime, not committed in the image
  - `@app.on_event` (deprecated, still functional but legacy) →
    migrated to FastAPI `lifespan` pattern
- **Additional finding during correction of the item above:** the first
  attempt to restore guidance on `matched_source` used
  `Field(description=...)` in the Pydantic schema, assuming that
  LangChain would inject that description as textual context to the LLM.
  **This had no effect at all** — confirmed because the model responses came out byte-identical before and after the change
  (expected with `temperature=0`/`seed` fixed only if the actual prompt sent did not change). Real cause: `with_structured_output`
  in Ollama uses the schema JSON to restrict the **type/format** of
  generation (decoding restricted by grammar), not to inject descriptions as readable instruction to the model. The correction that actually worked was returning the instruction as **explicit text
  in the prompt**, visually confirmed via `--debug` before running the
  complete suite again. Lesson: when adopting structured output via
  schema, explicit text in the prompt remains necessary for filling logic — the schema guarantees form, not content.
  **Update (2026-10-07):** this applied to `with_structured_output`
  in the Ollama of the time. Today the diagnosis uses
  `create_react_agent(response_format=DiagnosisModel)` via tool-calling,
  in which the `Field(description=...)` are part of the schema that the LLM reads —
  and therefore enter the prompt digest of DA-53
  (`app/agent/prompts.py`).

### 10. Pluggable LLM Gateway (not hardcoded in Ollama) (informal note — no DA)

**Context:** the project was born 100% Ollama/local by deliberate decision
(zero API cost to prototype). The product positioning evolved to enable AI for customers who cannot adopt SAP AI Core —
which does not mean that every customer will run 100% local: some already have OpenAI/Azure OpenAI contracted, or want
more capacity than local hardware can handle for a specific case. `diagnose_node` instantiated `ChatOllama` directly,
so changing providers would require editing the graph.

**Decision:** extract the provider choice to `app/llm/factory.py`
(`get_chat_model()`), selected via `Settings.llm_provider`
(ollama/openai/azure_openai). Deliberately **not** created a
custom interface (like a `LLMProvider.generate()` from scratch) — the
factory returns directly a `BaseChatModel` from LangChain, since all the rest of the graph (`with_structured_output`, Langfuse callbacks) already depends on the LangChain contract. Reuse the polymorphism that the lib already offers is less code and less bug surface than
reimplementing the same contract — a choice of "reuse vs.
reinvention", not just "add abstraction".

**Validation:** high and clear failure (`ConfigurationError`), never
silent, when the chosen provider does not have the necessary
configuration (e.g., `openai` without `OPENAI_API_KEY`) — same philosophy of
deterministic guardrails of sections 1 and 3.

### 11. Real connector for non-SAP system (ServiceNow) and honest RFC path (informal note — no DA)

**Context:** until here, the connectors (`ODataConnector`,
`RFCConnector`) were mocks assumed as such — correct for
prototyping, but insufficient to prove the promise of "SAP integration
+ non-SAP" that the current product positioning assumes.

**Decision:** `ServiceNowConnector` makes real HTTP calls against the
ServiceNow Table API (`GET /api/now/table/incident`) when
`SERVICENOW_INSTANCE_URL` is configured, falling back to demo/mock
mode only in the absence of this configuration — same principle of
SAP mock connectors (work without depending on real customer credentials), not
a technical limitation. Tested via `httpx.MockTransport`, exercising
real HTTP code (query parameters, authentication, response parsing,
network error handling) without needing a real ServiceNow instance.

In parallel, `RFCConnector` gained a `use_real=True` mode with
feature detection of `pyrfc` (SAP NetWeaver RFC SDK — SAP binary, outside PyPI): without the SDK installed, asking for `use_real=True` fails
with `ConfigurationError` explaining exactly what is missing, instead of
silently falling back to mock. RFC (not only OData) is the most
relevant path for the project's target audience: customers still on ECC
on-premise typically only have RFC/BAPI as automation path.

**Why this matters for positioning:** proof with code —
not just with docstring intent — that the "and other platforms" of the
project's value proposition is real: there is at least one non-SAP system
with real integration functional, alongside an SAP path
(RFC) clearly designed for the most restricted customer (ECC
on-premise), who is exactly who cannot afford SAP AI Core.

### 12. Closing multi-vendor connectors (Salesforce, Workday, SAP Ariba) and the real OData path (informal note — no DA)

**Context:** the previous section closed 1 of the 4 multi-vendor reference
scenarios of the product positioning (ServiceNow), chosen first because it has the simplest public API to implement for real
— not by business priority. This left an explicit technical debt:
Salesforce, Workday and SAP Ariba remained pure mock, and the
`ODataConnector` did not have even the skeleton `use_real` that the `RFCConnector`
already had.

**Decision:** the three remaining connectors (`SalesforceConnector`,
`WorkdayConnector`, `AribaConnector`) were implemented following
**exactly** the same criterion as the `ServiceNowConnector` — OAuth2 (client
credentials in all three cases) against the documented token endpoint
of each vendor, followed by the real REST call; absence of
configuration falls back to mock, presence activates the real path, without changing
any other file in the project. `ODataConnector` gained the same `use_real`/`ConfigurationError` pattern that the `RFCConnector` already had, closing
the asymmetry between the two SAP mock connectors.

**Validation:** each connector has test via `httpx.MockTransport`
simulating the two calls (OAuth2 token + resource), proving that the
production code (request assembly, header `Authorization: Bearer`,
response parsing, HTTP/network error handling) works for real
— without, for any of the three, a real account/sandbox available to
validate against production (same reservation already made for
`RFCConnector._fetch_real` since Phase 8, now consistent across the entire
project, not an isolated exception). **Update:** Salesforce was
later validated against a real Developer Edition (matrix in
`docs/ARCHITECTURE.md`).

**What this is NOT:** a claim that the 4 reference scenarios
(SuccessFactors↔Workday, Salesforce↔SAP, SAP Ariba↔S/4HANA,
ServiceNow↔SAP) are "production-ready" — they are ready for
**technical demonstration with real credentials in 10 minutes** (change
`.env`, no code changes), which is a much higher bar than "pretty mock",
but still below "tested against a real customer".

### 13. GraphRAG (Neo4j) stops being only a configuration field (informal note — no DA)

**Context:** since Phase 4, `Settings` had fields for Neo4j and the
documentation explicitly said "reserved for future use, no code uses this today" —
a configuration field without any implementation behind it, the exact type of thing
this project criticized in the `genai-engineering-template` (documentation describing
functionality that the code does not deliver).

**Decision:** implement real code (`app/rag/graph_store.py`) —
writes each diagnosis to Neo4j as relational graph
(Incident/Interface/System/Document) and queries this graph for
history of incidents on the same interface before generating a new
diagnosis — but keep **disabled by default**
(`GRAPH_RAG_ENABLED=false`). The business decision not to prioritize
GraphRAG did not change (Qdrant solves the main use case; graph only
pays off with months of real history accumulated); what changed is that
now there is a real and tested structure to enable when it makes
sense, instead of just a paragraph of intent.

**Validation:** `tests/test_graph_store.py` uses a FAKE Neo4j session
(implements only `.run()`, same spirit as `httpx.MockTransport`) to
prove that the correct Cypher queries are fired and the data returns
mapped correctly. Also validated that `build_graph()` produces the SAME
LangGraph as before this phase when the flag is disabled
(no new nodes are added) — zero behavior change in the default path.

**Honesty maintained:** not tested against a real Neo4j (without Docker
daemon available in the environment where this was built) — same
reservation already applied to `RFCConnector._fetch_real`. **Update:** today
the CI job `neo4j-smoke` runs `tests/test_graph_store_neo4j_smoke.py`
against a real Neo4j (service container).

### 14. A2A (Agent2Agent) layer implemented, with the caveat of GA preserved (DA-14)

**Context:** the original A2A proposal (internal document,
outside the repository) was archived before Phase 8, with two explicit
pre-requisites to get off the ground: SAP connectors closed and mature automated test suite. Phases 7/8 (and section 12 above)
satisfy both.

**Decision:** implement the A2A protocol subset needed
for the original acceptance criteria — Agent Card (`GET
/.well-known/agent-card.json`), task manager and JSON-RPC 2.0 server (`POST /a2a`, methods `message/send` and `tasks/get`) — in `app/a2a/`,
without depending on any external A2A SDK (the original proposal already
cited the immaturity of these SDKs as a risk to validate before
starting). The task manager calls the SAME function (`run_diagnosis`) that the
`/diagnose` REST does — zero duplicate diagnosis logic between the two
protocols.

**Deliberate simplification:** of the 8 task states of the A2A protocol,
only the 4 reachable by a synchronous and self-contained agent like this
were implemented (`submitted -> working -> completed|failed`).
Authentication is an optional static key via header, not OAuth2/JWT
— documented as production gap, not hidden. **Update
(2026-10-07):** the key ceased to be optional since DA-18 (generated at
startup if absent, `app/main.py::_ensure_api_keys_configured`), and
`message/send` accepts `blocking=false` (`app/a2a/task_manager.py`),
returning the task before completion.

**Validation:** `tests/test_a2a.py` proves the original acceptance criteria
mechanically — the A2A endpoint produces the same report that the
`/diagnose` does for the same input (via dependency injection of the
`diagnosis_fn` in the `TaskManager`, without needing a real LLM running for
the test), and an orchestration failure becomes `failed` task (business error),
not HTTP 500 (transport error) — the difference that matters for an external agent to know whether to retry or not.

**Caveat that DOES NOT change with this implementation:** Joule A2A support
remains unidirectional (outbound) today — the Agent Gateway that
would enable Joule to call this Copilot as peer (inbound) is
pre-GA, scheduled for Q4/2026. This endpoint is compatible with the
open A2A protocol (vendor-neutral standard, Linux Foundation), not an
integration already consumable by Joule.

### 15. Evidence/Trust Layer between RAG/connectors and the LLM (DA-15/16/17)

**Context:** an external architectural review pointed out three concrete,
not hypothetical, risks in an agent that correlates connector data + RAG + web search before calling the LLM: (1) unsanitized connector/RAG data arriving raw in the prompt (prompt injection surface); (2) `confidence` being only LLM self-report, without any objective floor; (3) GraphRAG could record an LLM hypothesis in the
graph and, in a future incident, it would return to the prompt as if it were confirmed historical fact
— an epistemic feedback loop.

**Decision:** controlled refactoring, preserving 100% of the existing stack
(LangGraph + Qdrant + Ollama + Langfuse + connectors + A2A) —
not a rewrite. `sanitize_untrusted_input` (already existing) now wraps ALL connector/RAG data before entering the prompt, not just part of it. `evidence_strength` — objective signal (real connector data
OR top-1 document retrieval score, never LLM self-report) —
becomes a hard ceiling on `confidence` (`min(confidence, evidence_strength
+ 0.25)`), exposed in the API (`DiagnosisResponse.evidence_strength`). In
Neo4j, every incident records its `evidence_strength`/`is_grounded`, and
`graph_context()` only brings to the prompt historical incidents
`is_grounded=true` by default — a weak hypothesis becomes `"HYPOTHESIS NOT CONFIRMED (low evidence - do not treat as fact)"` instead of
silently becoming "confirmed previous root cause" (text of the time; today the label is `"HYPOTHESIS NOT CONFIRMED from previous diagnosis
(low evidence - do not treat as fact)"` and, since AI-01 (2026-10-07),
even `is_grounded` incidents are labeled as unverified — only `verified` (DA-28) enters as fact; see
`app/rag/graph_store.py::format_graph_context_for_prompt`).

**Real bugs found along the way (not left as debt):** a broken prompt injection detection regex (`re.error: global flags
not at the start of the expression` — inline flags `(?i)` by default,
invalid when concatenated via `"|".join()`) and two ranking bugs in `app/rag/retriever.py` (RRF+cosine composite score leaking
to the field that should be pure cosine; fallback to reference library being applied always, not only in the absence of match) that inflated the confidence of diagnoses with little or no real evidence
— fixed at the root, not patched.

**Validation:** complete suite (71 tests) green, including two new tests
that prove that an unfounded hypothesis is filtered by default from
graph context and labeled as such when explicitly included.

### 16. Authentication of `/diagnose` and `/a2a` always required, with automatic key generation (DA-18)

**Context:** empty `API_KEY`/`A2A_API_KEY` in `.env` meant
authentication completely disabled — a silent gap, only visible
reading the source code, not a behavior documented as such.

**Decision:** `app/main.py::_ensure_api_keys_configured()` runs in
the FastAPI `lifespan` and ensures that neither of the two keys stays empty
in memory — if the operator did not configure one in `.env`, one is generated
(`secrets.token_urlsafe(32)`) and notified in `WARNING` in the startup log.
Preserves "clone and run" (zero mandatory config) without leaving the
endpoints open by default. Key comparison via
`secrets.compare_digest` (not `==`), to avoid leaking size/prefix via
timing attack.

**Validation:** `tests/test_api.py`/`tests/test_a2a.py` cover correct/incorrect/missing key in both endpoints, automatic generation when
missing, preservation when already configured, and the Agent Card reflecting the
`securityScheme` when `A2A_API_KEY` is set.

### 17. MCP Server (Model Context Protocol) - capability catalog read-first (DA-19)

**Context:** third item of the planned architectural roadmap (after
the Evidence Layer and the authentication closure of A2A) — MCP as
CAPABILITIES CONTRACT for external agents, not just a protocol
isolated of "connect an LLM to a tool". The MCP specification of
2026 explicitly moves towards stateless scaling, capability
catalog cache and enterprise authorization, which brings MCP closer to production infrastructure.

**Decision:** expose the Copilot as MCP SERVER (not client — the alternative reading, migrate the connectors to consume external MCP, is left for
a subsequent phase and deliberately out of this scope) in `POST /mcp/`,
via `app/mcp/server.py` (official `mcp` SDK, `MCPServer` class). Two
tools, both READ-ONLY by design ("read first" in
roadmap): `diagnose_incident` (calls the SAME `run_diagnosis()` of
`/diagnose`/`/a2a` — zero duplicate logic for the third time) and
`list_connectors` (inspects `settings` without any network call,
reports mock vs. real by `interface_type`). Authentication reuses the SAME
`X-API-Key` of `/diagnose` (DA-18) via a simple ASGI middleware, in
place of the SDK's `AuthSettings`/`TokenVerifier` OAuth2 — keep a single
authentication mechanism across the entire HTTP surface (REST + A2A + MCP),
not three.

**Implementation detail worth recording:**
`StreamableHTTPSessionManager.run()` can only run once per
process instance, and `app.mount()` does not propagate lifespan events
to sub-apps automatically — its lifecycle enters explicitly in the
FastAPI root app `lifespan`. And `POST /mcp` without trailing slash suffers
`307 Temporary Redirect` from Starlette before even reaching the authentication
check (standard `app.mount()` behavior, not MCP-specific) — documented in `docs/DEPLOY.md`, not left as surprise.

**Validation:** `tests/test_mcp.py` covers the two tools as direct Python functions (the `@mcp.tool()` decorator does not wrap the
original function) and the authentication boundary via real `TestClient` in the mounted app. The full round-trip of the MCP `initialize` handshake against a real `uvicorn` server was manually validated in this session — there is no automated test of this round-trip in the suite.

### 18. Hybrid Inference - resilience fallback between LLM providers (DA-20)

**Context:** fourth item of the planned architectural roadmap. Three
possible criteria to decide when to scale from local Ollama to
cloud: resilience (fallback on transport failure), quality
(scale by low `evidence_strength`, see DA-15) or routing by case
complexity before calling the LLM. Chosen: **resilience**
— the other two cost a second LLM call in some cases and
require calibrating a subjective threshold; resilience only acts when the
primary provider is genuinely unavailable.

**Decision:** `app/llm/factory.py::invoke_with_hybrid_fallback()` runs the
call with `settings.llm_provider` and, if `settings.llm_fallback_provider`
is configured (empty by default — behavior identical to before
this phase) **and** the failure is of transport (`ConnectionError`/
`httpx.ConnectError`/`httpx.TimeoutException` at the time — Ollama down,
network timeout; today `ConnectionError`, `httpx.TransportError`,
`openai.APIConnectionError` and `openai.InternalServerError`, see
`app/llm/factory.py::TRANSPORT_FAILURE_EXCEPTIONS`), repeats the SAME call with the fallback
provider before giving up. Application error (malformed JSON, invalid prompt)
never triggers the fallback — masking a real bug behind a second
LLM call would be worse than letting it surface. The diagnosis
sub-agents (`sap_diagnosis_node`/`saas_diagnosis_node`, see DA-22) were the consumers
at the time (today the three sub-agents call the AI Gateway
`app/llm/gateway.py::invoke_via_gateway`, which uses this function underneath —
no node calls it directly); which provider actually responded is exposed in
`DiagnosisResponse.llm_provider_used` — transparency, not a silent
fallback.

**Validation:** `tests/test_llm_factory.py` covers the four decisions
(uses primary when working, propagates error when no fallback
configured, provider switch on transport failure, gives up with
`ConfigurationError` when both fail) and, specifically, that an **application**
(error not transport) never triggers a fallback attempt — the case that would prove that the logic is masking bugs instead of
handling real unavailability.

### 19. GraphRAG as layer of operational knowledge - hardening (DA-21)

**Context:** fifth item of the planned architectural roadmap. GraphRAG
(`app/rag/graph_store.py`) already existed as real code since a previous
phase, but disabled by default (`GRAPH_RAG_ENABLED=false`, see
Decision #9) and without real Neo4j accessible in this development
environment (no Docker daemon). The option considered here was not
"validate against real Neo4j" (impossible in this environment) nor "enable by
default" (business decision of #9 still valid), but: **review
the existing code for design gaps that only appear in continuous operational use**
(not in a demo of a few incidents) and fix them
without depending on real infrastructure.

**Decision - three reinforcements, none changes behavior with the flag
disabled:**

1. **Graceful degradation by exception type** — `GRAPH_UNAVAILABLE_EXCEPTIONS`
   (`neo4j.exceptions.DriverError` + `TransientError`) is the catch-tuple
   used now in `graph_enrich_node`/`graph_write_node`
   (`app/agent/nodes.py`): an infrastructure failure of Neo4j
   (connection refused, timeout, server restart) no longer brings down the
   entire diagnosis — falls back to "no history"/"write skipped" with
   a warning log. Deliberately excludes `Neo4jError` in general: a
   `ConstraintError`/`CypherSyntaxError` is our bug (Cypher/schema
   wrong), not infrastructure unavailability, and should continue
   propagating — same principle of separating transport failure from application
   error used in Hybrid Inference (DA-20). `ensure_constraints()` also now runs alone in the FastAPI `lifespan` when the flag is
   enabled (`app/main.py`), eliminating the manual `--init` step
   without blocking startup if Neo4j is temporarily down.
2. **Formatting with deduplication by recurrence** —
   `format_graph_context_for_prompt()` groups CONSECUTIVE occurrences
   of the same root cause into a single line with counter ("already occurred 3x"),
   instead of repeating the same line and wasting prompt budget on a "flapping" interface (failing repeatedly for the same cause).
3. **Manual cleanup utility** — `prune_ungrounded_hypotheses()`
   (CLI `--prune-ungrounded --older-than-days N`) removes hypotheses NOT
   confirmed old; incidents with confirmed root cause are never
   touched, at any age, and the function is never called automatically.

**Validation:** `tests/test_graph_store.py` (consecutive vs.
non-consecutive dedup, prune count, guarantee that the prune query filters by `is_grounded=false`) and `tests/test_nodes_graph_degradation.py`
(new — `graph_enrich_node`/`graph_write_node` degrade in
`DriverError`/`TransientError` but propagate `CypherSyntaxError`), all
with fake Neo4j driver (`FakeSession`), same pattern as
`httpx.MockTransport` used in HTTP connectors. **Explicit non-goal:**
validation against a real Neo4j remains pending, due to limitation of this environment (no Docker) — decision explicitly accepted when scoping this phase.

### 20. Multi-agent - supervisor + specialists by domain (DA-22)

**Context:** sixth item of the planned architectural roadmap. The only
existing diagnosis node (`diagnose_node`) used a fixed persona
of "SAP integration specialist" for ANY connector — Salesforce webhook incident
received the same "OData/IDoc/RFC/CPI" expertise as an RFC incident. This contradicted the design principle already recorded in this log (#8/#13): SAP is one connector among equals, not the architectural axis of the product.

**Decision:** a `supervisor_node` (`app/agent/supervisor.py`) runs
FIRST in the graph — even before the `connector` — and classifies
deterministically (no LLM) the incident domain from
`interface_type` (or, in its absence, SAP keywords in
description). The graph (`app/agent/graph.py::_route_to_specialist`, via
`add_conditional_edges`) routes to ONE of the two specialist sub-agents
— never both in the same incident (at the time; today there are three:
`generic` gained its own node, `generic_diagnosis_node`):

- `sap_diagnosis_node` — SAP persona (OData, IDoc, RFC, CPI/Integration
  Suite, BTP)
- `saas_diagnosis_node` — multi-vendor persona (ServiceNow,
  Salesforce, Workday, Ariba, REST/OAuth2 APIs), also covering the
  case "domain not identified" with generalist reasoning (at the time;
  today this case goes to `generic_diagnosis_node`)

The two share the same core (`_run_diagnosis_agent`) — ReAct agent,
Hybrid Inference (DA-20), JSON parsing and confidence guardrails (DA-15) continue identical; only the persona/expertise of
the prompt changes. `DiagnosisResponse.agent_domain` exposes which domain was
used — same transparency philosophy of `llm_provider_used`
(DA-20), never a silent routing.

**Validation:** `tests/test_supervisor.py` (pure deterministic classification)
and `tests/test_nodes_multiagent.py` (correct persona by
sub-agent, conditional routing, safeguard against missing `agent_domain`, propagation to `DiagnosisResponse`) — mocking
`invoke_with_hybrid_fallback` directly (at the time; today the test mocks
`app.agent.nodes.invoke_via_gateway`, the AI Gateway of DA-26), without depending on real LLM.
`build_graph()` verified compiling successfully in both GraphRAG modes (enabled/disabled), confirming expected nodes.

### 21. Event Mesh - event-driven ingestion (DA-23)

**Context:** sixth item of the planned architectural roadmap. Until this
phase the Copilot only reacted to explicit calls (`/diagnose` human,
A2A, MCP) — to become a real copilot in production, it needs
to react to events published by monitoring systems (CPI, Solution
Manager, a queue/IDoc listener), not just wait for someone to call the API.

**Decision:** `POST /events/incident` (`app/main.py` + `app/events/`)
receives a [CloudEvents](https://cloudevents.io/) envelope — the format
that SAP Event Mesh uses in **REST/Webhook push subscription** mode
(in addition to native AMQP 1.0) — and fires `run_diagnosis()` automatically.
Webhook was chosen instead of an AMQP consumer because it is a first-class delivery mode of Event Mesh itself and the only one testable end-to-end without depending on a real broker — same pragmatic logic of DA-19/DA-21. Only `type ==
"com.sap.integration.incident.detected.v1"` is accepted today (`Literal`
in `IncidentEventEnvelope`); any other value becomes `422`
automatically. The envelope follows strict CloudEvents 1.0: `specversion`,
`type`, `source` and `id` are mandatory, and deduplication uses the pair
`source`+`id` (validation 2026-10-07, M-16). Authentication uses a **dedicated** key
(`X-Event-Mesh-Api-Key`, generated automatically if not configured,
same pattern as DA-18) — isolated from `API_KEY`/`A2A_API_KEY`, because the
webhook secret normally lives in an external system outside the direct control
of this project.

**Explicit non-goal:** processing is synchronous (subject to the same
rate limit as `/diagnose`) and there is no direct AMQP consumption — real
queue/backpressure would be a natural evolution if volume justifies, not
a hidden gap. **Update:** today the webhook responds `202` and
processes in background (RQ job with `REDIS_URL`, `BackgroundTasks` without it), and there is the AMQP 1.0 consumer (`app/events/amqp_consumer.py`,
DA-32/DA-40).

**Validation:** `tests/test_events.py` covers event→
`IncidentRequest` mapping, call to `run_diagnosis()`, authentication (401),
rejection of unknown `type` (422), size limit (422) and
automatic key generation — all mocked, without real Ollama/Qdrant.

### 22. Deploy in production - SAP BTP Kyma Runtime (DA-24)

**Context:** last item of the planned architectural roadmap. Until this
phase the project only ran via `docker-compose.yml` (local dev/demo) -
the real packaging for a SAP production environment was missing,
completing the journey "portfolio prototype → demonstrable product".

**Decision (scope chosen: "real Kyma deployment manifests", not
deeper integration with BTP services like XSUAA/Destination):**
`deploy/kyma/` brings Deployment (2 replicas, probes on `/health` — today
readiness on `/ready` and liveness on `/health`,
non-root user), Service, HorizontalPodAutoscaler (2-6 replicas by
CPU - direct response to a limitation already identified in the review of
DA-23: event spikes increase simultaneous calls to the LLM Gateway),
ConfigMap and a `secret.example.yaml` — template with all values
prefixed `CHANGE-ME`, never applied directly. The `APIRule` (Kyma API
Gateway module) uses `accessStrategy: noop`, since the Copilot has its
own API key authentication on each endpoint (DA-18/DA-23) — does not
duplicate authentication at the network layer.

When reviewing the packaging, three real problems in the `Dockerfile` were
fixed at the source (not just patched in the manifests): non-reproducible build (`uv sync` without lockfile in build), container running as root, and the bug already documented of `uv run` resynchronizing dev dependencies at each start (now `CMD` calls `.venv/bin/uvicorn` directly) — `docker-compose.yml` no longer needs the `command:` override that patched the last problem.

**Explicit non-goals:** no manifest was validated against a real Kyma cluster, nor Docker image actually built (without cluster
or Docker accessible in this development environment — same honesty
already applied to Neo4j/DA-21 and MCP/DA-19); the schema of the CRD `APIRule` should be checked against the target cluster before applying; Qdrant and
Neo4j remain external pre-requisites, not deployed by this bundle.

**Validation:** `tests/test_kyma_manifests.py` (11 tests) — all YAML
syntactically valid, namespace consistent across resources, probes
on `/health` (today `/ready` and `/health`; never authenticated endpoint), non-root Pod, HPA/APIRule
pointing to the correct resources, `kustomization.yaml` referencing
only existing files.

This closes the consolidated architectural roadmap of this project (AI Gateway
→ A2A/API auth → MCP → Hybrid Inference → GraphRAG → Multi-agent →
Event Mesh → BTP/Kyma), all executed in this same work session.

### 23. Post-roadmap follow-up — multi-stage build of frontend + model default alignment (informal note — no DA)

An external architectural review identified two real problems that
survived DA-24: (1) the `Dockerfile` did not build the frontend
(React/Vite) from source — it expected a ready `static/dist/`, which is in
`.gitignore` and does not exist in a clean clone, breaking exactly the build
flow described in `deploy/kyma/README.md` (this was already self-reported as
pending in `docs/DEPLOY.md`, but was not fixed during DA-24); (2) the default
LLM model diverged between `app/config.py`
(`qwen3-coder-next:latest`, canonical source) and `.env.example` /
`docker-compose.yml` (both `qwen2.5-coder:32b`).

Fixed with a second stage in the `Dockerfile`
(`node:22-slim AS frontend-build`, `npm ci && npm run build`) whose result is
copied to `static/dist` in the final stage via `COPY --from=frontend-build`;
`.dockerignore` added (it did not exist); `docs/DEPLOY.md` section 2 updated;
`.env.example` and `docker-compose.yml` aligned to the canonical model in
`config.py`. Validated by running `npm ci && npm run build` in isolation (it
generates the expected `dist/`) — a full Docker image build was not tested
(Docker unavailable in this environment, the same limitation already recorded
in DA-24).

---

## Recent decisions (DA-75–DA-92, 2026-10-20)

This section keeps the English record aligned with the Portuguese source in
[`../DECISOES_DE_ARQUITETURA.md`](../DECISOES_DE_ARQUITETURA.md). Status is the
real one after the 2026-10-20 technical audit; the detailed findings are in
`CHANGELOG.md`.

| DA | Status | Note |
|---|---|---|
| DA-75 | Delivered | Native LangGraph `interrupt()` + `Command(resume=...)`; `/incidents/{id}/review` resumes from checkpoint |
| DA-76 | Delivered | `ai_warning` in `DiagnosisResponse`, pt-BR/en catalog and report |
| DA-79 | Candidate | `tenant_id` schema and repository filters exist, but isolation is not fail-closed on every path |
| DA-82 | Schema-only | `snapshot_table`/`rollback_log`; endpoint removed until a real rollback mechanism exists |
| DA-83 | Delivered | `explanation` in `DiagnosisResponse` and `_build_xai_explanation` |
| DA-84 | Candidate | Table, ORM and repository; version/rollback endpoints are missing |
| DA-85 | Candidate | Table, ORM and repository; experiments endpoints and `_run_ab_test()` are missing |
| DA-86 | Delivered | Version/history/rollback endpoints; the history UI is still pending |
| DA-87 | Delivered | `/admin/metrics/domains` with a `days` window; duplicate `/admin/dashboard/domains` removed |
| DA-88 | Delivered | `app/scripts/load_test.py`, config and `tests/test_load_testing.py` (3 tests) |
| DA-89 | Delivered | PDF/XLSX compliance export |
| DA-90 | Delivered | GRC documentation and conformance tests |
| DA-91 | Delivered | RBAC tests assert exact statuses; DA-87/89 routes exercised |
| DA-92 | Recorded | Canonical model `qwen3.6:35b-a3b` since 2026-10-13; promptfoo re-baseline pending (DA-51) |
