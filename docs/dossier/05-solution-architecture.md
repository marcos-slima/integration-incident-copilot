# 05 — Solution Architecture

## 5.1 Architectural style

IIC is a **modular monolith hosting a deterministic agentic pipeline**:

- **One deployable image, two runtime roles** (API and RQ worker). There is no network hop between pipeline stages.
- **An explicit state machine** (LangGraph `StateGraph` over a typed `CopilotState`) instead of an LLM deciding the control flow. The LLM is called inside **one node type** and only when deterministic paths are exhausted.
- **Ports and adapters at the volatile edges**: connectors (`SAPConnector` ABC + registry), LLM providers (`BaseChatModel` through `app/llm/factory.py`), embeddings (`ollama` or `fastembed`), event transport (webhook, AMQP 1.0).
- **Optional infrastructure with graceful fallback**: Qdrant, Redis, Postgres, Neo4j and Langfuse can each be missing without breaking startup. Every one of them is lazily imported and either degrades or switches to an in-memory variant.

### Why this style, and not the alternatives

| Alternative | Why it was not chosen | Recorded where |
|---|---|---|
| **Microservices** (one service per stage) | The stages share one request-scoped state and run sequentially in under a few seconds. Splitting them adds latency, serialisation and distributed failure modes with no independent scaling need. Scale is handled by replicating the whole API behind an HPA | Dossier analysis; consistent with the single-image Kyma bundle (DA-24) |
| **Autonomous LLM agent** (the LLM plans which tools and steps to use) | Routing, rule matching and escalation must be reproducible and auditable (DA-22, DA-44). An LLM planner makes the control flow probabilistic and multiplies cost per incident | DA-22 ("no LLM in classification"), DA-3 (guardrails in code) |
| **Pure workflow / rules engine, no AI** | Covers known errors only. Free-text incidents and unseen combinations need language understanding. The hybrid keeps rules **first** and uses the LLM for the long tail | DA-33 (rule engine as pre-filter) |
| **SaaS AIOps or SAP AI Core-native** | Excludes ECC on-premise and teams without a BTP AI budget, and ties the solution to one vendor | `README.md`, `docs/TCO_SAP_AI_CORE_VS_SELF_HOSTED.md` |

## 5.2 Architecture principles

| # | Principle | Meaning in the code | Evidence |
|---|---|---|---|
| P1 | **Deterministic first, probabilistic last** | Supervisor, rules, escalation and evidence are plain code; the LLM handles only what remains | ✅ `app/agent/supervisor.py::classify_domain`, `app/agent/rules.py::match_known_error`; invariants 2, 6 |
| P2 | **Evidence over self-assessment** | Confidence is bounded by observed evidence; the LLM's self-reported confidence is never used for trust or escalation | ✅ `app/agent/nodes.py::_assemble_evidence`; DA-15/16, invariant 4 |
| P3 | **Fail-closed for security, fail-soft for availability** | Policy, capability registry, sovereignty and auth deny by default. Infrastructure outages degrade the feature, not the request | ✅ `app/mcp/policy.py`, `app/llm/gateway.py::PolicyViolationError` vs DA-21/65 |
| P4 | **A single choke point for every LLM call** | Every inference goes through `invoke_via_gateway` (policy, budget, circuit, metering, audit) | ✅ invariant 3 |
| P5 | **SAP is one connector among equals** | Domain = configuration (YAML skills), not hardcoded SAP branches | ✅ `data/skills/diagnose_incident.yaml`; DA-22/59/63 |
| P6 | **Capability by real origin, not label** | Routing and sovereignty use the provider's real URL | ✅ DA-43/45, invariant 9 |
| P7 | **Clone and run** | Works with Python + Qdrant only; everything else is opt-in | ✅ `README.md` Quickstart; NFR-16 |
| P8 | **Documentation is tested** | Quality gates fail the build when docs, DAs, env vars or diagrams drift from the code | ✅ `app/evaluation/gates.py`; DA-51 |
| P9 | **Distinguish transport failure from application error** | Fallback only on transport failure; bugs and invalid output always propagate | ✅ `app/llm/factory.py::TRANSPORT_FAILURE_EXCEPTIONS`, `app/llm/failures.py::classify_failure`; DA-20/68 |
| P10 | **Recommend, don't act** | No write tools; any future write tool needs a registry entry, a risk level and approval | ✅ `app/mcp/policy.py`; DA-27 |

## 5.3 Building blocks

| Block | Responsibility | Key modules |
|---|---|---|
| **Channel adapters** | Translate REST, CloudEvents, AMQP 1.0, MCP and A2A into one `IncidentRequest` | `app/main.py`, `app/events/`, `app/mcp/server.py`, `app/a2a/` |
| **Orchestrator** | Admission control, timeout, graph execution, response assembly, persistence | `app/agent/graph.py::run_diagnosis` |
| **Domain router** | Deterministic domain classification from the skill registry | `app/agent/supervisor.py`, `app/mcp/skill_registry.py` |
| **Fact layer** | Read-only connectors with circuit breaker, token cache, identifier validation | `app/connectors/` |
| **Knowledge layer** | Hybrid retrieval, reranking, reference fallback, ingestion, GraphRAG, taxonomy | `app/rag/`, `app/ontology/` |
| **Reasoning layer** | Rule engine, ReAct diagnosis, personas, versioned prompt | `app/agent/rules.py`, `app/agent/nodes.py`, `app/agent/prompts.py` |
| **Trust layer** | Guardrails, evidence assembly, two confidences, escalation, HITL, XAI explanation | `app/agent/nodes.py`, `app/agent/escalation.py` |
| **AI control plane** | Sensitivity, sovereignty, policy engine, budget, failover, metering | `app/llm/` |
| **Governance & admin** | Model/credential registry, systems catalogue, users, web-search sources, knowledge versions, compliance export | `app/admin/` |
| **Observability** | Langfuse spans, Prometheus metrics, Grafana dashboards, reports | `app/metrics.py`, `deploy/grafana/`, `scripts/generate_reports.py` |
| **Contract monitoring** | OData `$metadata` drift detection | `app/contracts/` |

## 5.4 Quality-attribute scenarios

Written in the ATAM form *source → stimulus → response → measure*. "Measure" cites the test or evidence that demonstrates it.

| QA | Stimulus | Expected response | Measure / evidence |
|---|---|---|---|
| Availability | Qdrant goes down during a diagnosis | Retrieval returns `[]`, rules still answer, the response is produced in degraded mode | `tests/test_da65_rag_degradation.py` |
| Availability | Local Ollama unreachable, data `public` | Circuit opens, next eligible provider answers | `tests/test_llm_gateway.py::test_public_data_falls_back_to_cloud_on_transport_failure` |
| Security / sovereignty | Ollama down, data from a real connector (`confidential`), only cloud configured | Request fails with `PolicyViolationError`; nothing is sent to the cloud | `tests/test_llm_gateway.py::test_confidential_data_with_only_cloud_configured_raises_policy_violation` |
| Integrity of answers | LLM cites a document it never received | `matched_source` nulled, confidence capped at 0.3, escalation `abstained` | `tests/test_casos_de_uso.py::test_uc02_fonte_inventada_pelo_llm_e_descartada` |
| Cost | Known IDoc 51 error | Diagnosis returned with zero LLM calls | `tests/test_casos_de_uso.py::test_uc01_idoc_51_resolvido_pelo_rule_engine` |
| Reliability (events) | The same CloudEvent is delivered twice | One diagnosis | `tests/test_casos_de_uso.py::test_uc07_webhook_cloudevents`, `tests/test_idempotency.py` |
| Overload | More than 4 concurrent diagnoses per process | Extra requests rejected with 503 + `Retry-After` instead of piling up | `app/agent/graph.py` (`_graph_invoke_semaphore`), `app/main.py` |
| Scalability | CPU above 70 % | HPA adds replicas; state is shared through Redis | 📏 k3d: 1→2 replicas at 398 %/70 % |
| Maintainability | A developer changes prompt text | Gate `prompt_digest_measured` fails until promptfoo is re-baselined | DA-53, invariant 20 |
| Extensibility | A new connector is added | Gate `connector_reachable` fails until all 9 surfaces are wired | DA-59, invariant 23 |

## 5.5 Capability switches (feature flags)

Default values come from `app/config.py`. "Kyma" is `deploy/kyma/configmap.yaml`.

| Flag | Default | Kyma | Effect | Maturity |
|---|---|---|---|---|
| `RULE_ENGINE_ENABLED` | `true` | — | Deterministic pre-filter | Production-ready |
| `LLM_PROVIDER` / `LLM_FALLBACK_PROVIDER` | `ollama` / empty | `openai` / `azure_openai` | Primary and fallback inference | Production-ready |
| `SENSITIVITY_DEFAULT` | `confidential` | (default) | Untagged text treated as confidential | Production-ready |
| `DATA_SOVEREIGNTY_MODE` | `strict` | `cloud_with_dlp` | Whether redacted confidential data may reach allowlisted cloud | Production-ready |
| `REFERENCE_LIBRARY_FALLBACK_ENABLED` | `true` | `false` | Third-party manuals as floor tier (licensing) | Ready; threshold needs re-measurement |
| `EMBEDDING_BACKEND` | `ollama` | `fastembed` | 768-d `nomic-embed-text` vs 384-d `bge-small-en-v1.5` (needs re-ingest) | Ready; identity guard (DA-45) |
| `WEB_SEARCH_ENABLED` / `WEB_SEARCH_POLICY` | `false` / `approved` | `true` / `public_only` | ReAct web tool: `approved` = approved sites only; `public_only` = only when no confidential data is in the state | Opt-in |
| `GRAPH_RAG_ENABLED` | `false` | `false` | Neo4j history in and out | Opt-in; CI smoke only |
| `CHECKPOINTING_ENABLED` | `false` | — | Durable graph state, real HITL pause | Opt-in |
| `ONTOLOGY_ENRICHMENT_ENABLED` | `false` | — | SKOS-aware retrieval node | ⚠️ Experimental, defects open (R5) |
| `LLM_REGISTRY_DB` | `false` | — | Models and credentials from Postgres instead of `.env` | Opt-in (requires DB) |
| `REQUIRE_AUTH` | `false` | `true` | Refuse to start without explicit keys (no ephemeral keys) | Must be `true` outside dev |

## 5.6 Cross-cutting concerns map

| Concern | Where it is handled | DA |
|---|---|---|
| Authentication | `app/main.py::verify_session_or_api_key`, `app/main.py::verify_event_mesh_api_key`, `app/admin/security.py::verify_admin_key`, `app/auth.py` | 18, 54 |
| Brute force and revocation | `app/auth_guard.py` (Redis-backed failure counters, session revocation) | 54, 55 |
| Rate limiting | `slowapi` with identity-based keys (`app/rate_limit.py`): 10/min on diagnosis surfaces, 5/min on auth endpoints | P1.3 review |
| Input hardening | Pydantic limits, `sanitize_untrusted_input`, identifier charset validation | 30, 52 |
| PII | `app/redaction.py` | 30, 80 |
| Secrets at rest | `app/admin/crypto.py` (Fernet) | 47, 60 |
| Resilience | `app/circuit_breaker.py` (Redis or memory), failure taxonomy, hybrid fallback | 20, 41, 68 |
| Observability | Langfuse spans (`observe_span`), Prometheus, Grafana, `trace_id` in the response | 70 |
| Internationalisation | `app/i18n/`, `IncidentRequest.locale` | 73, 74 |
| Explainability | `explanation` field (XAI), `ai_warning` | 76, 83 |
