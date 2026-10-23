# AGENTS.md — Integration Incident Copilot

Instructions for AI coding agents (Claude Code, OpenCode, Codex, Aider, etc.).
Read before any edit. The authoritative design record lives in the files cited
below; this is the operational playbook.

---

## 1. What this project is

AI assistant for diagnosing enterprise-integration incidents. Multi-vendor by
design: SAP (OData, RFC/CPI, BTP) is one connector among ~10 (ServiceNow,
Salesforce, Workday, Ariba, SuccessFactors, CAP, APIManagement, SAP PO/PI).

---

## 2. Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Pydantic |
| Orchestration | LangGraph (`app/agent/graph.py`) |
| LLM | Ollama local (default) → OpenAI/Azure fallback. Canonical model `qwen3.6:35b-a3b` (35B MoE, 128k ctx) from 2026-10-13 — default in `app/config.py`, `.env`, `docker-compose.yml` and the promptfoo config. `qwen3-coder-next:latest` (MoE 80B/3B) was the previous pick (DA-4/8, DA-12); re-baseline pending (DA-51) |
| RAG | LangChain + Qdrant (hybrid dense+sparse BM25, RRF fusion) |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (DA-29 benchmark) — **do not change** |
| GraphRAG | Neo4j (`app/rag/graph_store.py`), opt-in `GRAPH_RAG_ENABLED=true` |
| Observability | Langfuse (optional when `LANGFUSE_*` set) |
| Package manager | `uv` — **never** `pip install` / `poetry add` directly |
| Tests | `pytest` + `pytest-asyncio` (strict mode) |
| Lint / format | `ruff` + `ruff-format` (pre-commit auto) |

---

## 3. Commands

```bash
# Activate venv in the current shell
PATH="$PWD/.venv/bin:$PATH"

# Unit tests (no infrastructure required)
uv run pytest tests/ -m "not integration" -v --tb=short

# Integration tests (requires Ollama + Qdrant running)
uv run pytest tests/ -v

# Run the dev server
uv run uvicorn app.main:app --reload

# Document ingestion
uv run python -m app.rag.ingest --target incidents --reset

# Quality gate (DA-51) — run before committing
PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py

# Migrations (requires Postgres running)
uv run alembic upgrade head
uv run alembic check
```

Infrastructure (all from `docker-compose.yml` in this repo):

```bash
docker compose --profile observability up -d qdrant postgres grafana redis
docker compose --profile graphrag up -d neo4j      # opt-in
```

Ollama is **native** to the host (`/usr/local/bin/ollama`), not a container.
The compose ` ollama` service (profile `container-ollama`) is opt-in and
points at `host.docker.internal`.

---

## 4. Key invariants — do not break without a formal DA

> Full list: see `CLAUDE.md` → "Invariantes que NÃO devem ser alterados
> sem DA formal". Summary:

1. `RERANKER_MODEL` — always `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (DA-29)
2. `rule_engine_enabled=True` by default (DA-33)
3. All LLM calls go through `llm/gateway.py` — never call `factory.invoke_with_hybrid_fallback` directly (invariant 3)
4. `_assemble_evidence()` never uses LLM self-assessment (DA-15/16, invariant 4)
5. `mcp/policy.py` is fail-closed (DA-27)
6. `classify_domain()` in `supervisor.py` is 100 % deterministic (DA-22)
7. `generic_diagnosis_node` exists for `agent_domain="generic"` — do not reuse `saas_diagnosis_node` (invariant 7)
8. Model name never appears in routing tables (invariant 8)
9. Capability is per **origin**, not per label (DA-45, invariant 9)
10. `llm_send_seed=None` = DA-2 active (seed=42); never read `None` as "don't send" (invariant 10)
11. `require_loopback=True` and `False` are mutually exclusive per route (invariant 11)
12. Incident ↔ system correlation is fail-closed on ambiguity (DA-50)
13. `verified` ≠ `verified_at` — don't coerce `diagnosis_correct` to True (invariant 13)
14. Quality gate runs **with the test suite** (DA-51)
15. Contract drift: four states; `unverified` never opens an incident (DA-52, invariant 15)
16. `system_contracts` is append-only, no FK (DA-52)
17. Only `breaking` drift opens an incident (invariant 17)
18. Fingerprint is never raw XML (invariant 18)
19. `get_sync_session_factory()` is keyed by URL; never cache `None` (invariant 19)
20. Prompt text changes require promptfoo re-baseline (DA-53, invariant 20)
21. `prompt_digest`/`prompt_version` NULL when rule engine terminates (invariant 21)
22. `evidence_strength` is FLOAT (migration 002) — never use text predicates (invariant 22)
23. New connectors require 9 surfaces (invariant 23)
24. `web_search_sources` is fail-closed with no code fallback (DA-57)
25. Coverage has 3 levels; `unknown` ≠ `none` (DA-58)

---

## 5. Architecture decisions (DAs) — quick reference

Every DA is registered as a row in the `CLAUDE.md` table (53 DAs on
2026-10-14; gate `da_registered` enforces that every DA referenced in code has
a row). The design prose lives in two places:

- `docs/DECISOES_DE_ARQUITETURA.md` — §§1–54 (order is editorial,
  not strictly ascending — see the navigation note after the index table)
- `docs/ARCHITECTURE.md` — DA-32 through DA-35 (alternative declared location)

> **Section ordering:** §§42–47 (DA-42, DA-38, DA-39, DA-40, DA-41, DA-12)
> were retro-documented as a block, so they appear out of DA-number order.
> The index table maps each DA to its correct section.

To register a new DA: follow `CLAUDE.md` → "Como registrar uma nova DA"
(5 steps; include migration filename if the DA created a schema).

---

## 6. File layout

```
app/
  agent/
    graph.py          # LangGraph + run_diagnosis() — entry point
    prompts.py        # DA-53: prompt artifact (version + digest)
    nodes.py          # All nodes: connector, retrieve, diagnose, report
    rules.py          # DA-33: Rule Engine (22 rules in KNOWN_ERROR_RULES)
    escalation.py     # DA-44: 3-tier escalation signal
    supervisor.py     # DA-22: domain classifier (deterministic); domain index
                      # from data/skills/ since DA-63
    state.py          # CopilotState (TypedDict)
  llm/
    factory.py        # DA-20: hybrid inference (Ollama → cloud)
    gateway.py        # DA-26: AI Gateway (policy + circuit breaker + budget)
  admin/              # DA-46/47/48/49/50/51/54/55: models + credentials +
                      # Fernet + metering + systems catalog + correlation
  mcp/
    server.py         # DA-19: MCP server (Streamable HTTP, /mcp)
    policy.py         # DA-27: Capability Registry (fail-closed)
    skill_registry.py # DA-63: loads data/skills/*.yaml (one parser for
                      # policy, supervisor and the quality gate)
  contracts/          # DA-52: SAP contract drift detection
  rag/
    retriever.py      # hybrid RAG + reranker
    graph_store.py    # GraphRAG (Neo4j)
    ingest.py         # document indexing
  evaluation/
    gates.py          # DA-51: deterministic quality gates
    coverage.py       # DA-58: product × mechanism map
  events/
    consumer.py       # DA-23: CloudEvents webhook → run_diagnosis()
  connectors/         # 10 connectors: odata, rfc, servicenow, salesforce,
                      # workday, ariba, successfactors, cap, apim, po
  webusers.py         # DA-55: UI user domain
  config.py           # Pydantic Settings — single source of truth
  main.py             # FastAPI app, routes, lifespan
alembic/versions/     # 001–013 (13 migrations; 013 = Grafana read-only role)
data/
  sample_docs/        # RAG knowledge base (.md)
  eval/               # RAG eval dataset + promptfoo baseline (versioned)
  sap_products.yaml   # DA-58: 27 products × 9 mechanisms
  connector_coverage.yaml  # DA-58: connector → product + mechanism
  skills/             # DA-63: declarative skills (capability contract +
                      # interface_type → domain); copied into the image
docs/
  DECISOES_DE_ARQUITETURA.md  # DA prose, §§1–54
  ARCHITECTURE.md             # DA-32–35 prose + system architecture
  QUALITY_GATES.md
  COVERAGE_MAP.md             # generated by scripts/coverage_map.py
  CASOS_DE_USO.md
  CONNECTORS.md               # DA-59 consolidated connector doc
scripts/
  quality_gate.py   # DA-51 CLI
  coverage_map.py   # generates docs/COVERAGE_MAP.md
  benchmark_rerankers.py
  validate_dashboards.py
  graph_diagram.py  # generates Mermaid in ARCHITECTURE.md + README.md
```

---

## 7. Testing and quality gates

| Check | Command | When |
|---|---|---|
| Unit tests | `uv run pytest tests/ -m "not integration" -v --tb=short --cov=app --cov-fail-under=80` | every commit / CI |
| Quality gate (DA-51) | `PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py` | **before committing** |
| Migrations + 45 dashboard queries | `uv run alembic upgrade head && uv run alembic check` + `scripts/validate_dashboards.py` | CI job `migrations_and_dashboards` |
| Integration (Ollama + Qdrant) | `uv run pytest tests/ -v` | when infra available |

### Quality gate checks (DA-51, `app/evaluation/gates.py`)

- `implemented_das_documented` — every DA row in `CLAUDE.md` has a prose section
- `das_index_current` — index table in `DECISOES_DE_ARQUITETURA.md` matches the registered DAs
- `da_registered` — every DA referenced in `app/`, `scripts/`, `alembic/` has a `CLAUDE.md` row
- `prompt_digest_measured` — prompt in `app/agent/prompts.py` matches `data/eval/prompt_baseline.json`
- `connector_reachable` — every registered connector reaches each surface (registry, 2 Literals, routing domain in `data/skills/diagnose_incident.yaml` (DA-63; `apim` is generic by design), CLI, `CONNECTOR_TYPES`, UI dropdown, web-search seed, systems form) and the `Dockerfile` copies `data/skills/`
- `connector_coverage` — the 9th surface, `data/connector_coverage.yaml`, has its own gate (DA-58 coverage map consistency)
- `connector_validation_matrix` — `docs/ARCHITECTURE.md` covers every registered connector
- `candidate_das_fresh` — DAs marked "candidate" have no prose section yet

**Gate regex note:** the index-table header `| DA | Seção | O que é |` is what
`app/evaluation/gates.py::check_index_current` matches. It does not match the
old `| Número | Título | Seção |` header that was removed in this session.

---

## 8. Commit rules

```bash
# pre-commit: ruff, ruff-format, gitleaks, trim-whitespace
# ruff must be on PATH inside the venv:
PATH="$PWD/.venv/bin:$PATH" git commit -m "feat(DA-N): description"
```

- Use `git add` + re-commit if pre-commit reformats files.
- **Never** use values with the `sk-` prefix in tests (triggers gitleaks).
- **Never** use literal field names like `api_key` or `generic-api-key` as test
  values.
- **Sensitive material goes ONLY to `docs/secrets-local/`** (gitignored):
  passwords, keys, tokens, certificates and test URLs/users with real values.
  Never write that content to any other repository file, never `git add -f`
  that folder, never quote the values in commits, PRs or issues. Versioned docs
  name the variable (`API_KEY`), never its value. `docs/interno/` is for work
  notes **without** secrets.

---

## 9. Trust levels (Evidence Layer)

| trust_level | Source |
|---|---|
| `system_observed` | Real connector (not mock) OR Rule Engine (DA-33) |
| `simulated` | Connector mock or fallback |
| `retrieved_document` | RAG chunk (Qdrant) or GraphRAG history |
| `web_untrusted` | Web search results (DuckDuckGo) |
| `user_reported` | Text description from the user (weakest) |

---

## 10. Known limitations (accepted, do not regress)

- Integration tests (`-m integration`) need Qdrant + Ollama; auto-skipped
  without them.
- `StreamableHTTPSessionManager.run()` — only callable **once per process** (DA-19).
- GraphRAG (Neo4j) is opt-in; disabled by default in tests.
- `starlette.Mount()` does not propagate lifespan to sub-apps.
- Connector validation matrix is a **per-connector claim**: RFC, ServiceNow,
  Salesforce, and CAP were validated against real instances; OData, Workday,
  Ariba, SuccessFactors, and PO/PI were not (PO/PI has a non-public API).
  `docs/ARCHITECTURE.md` is the source of truth.

---

## 11. Operational principles (AI agents)

When answering questions about this project, **always**:

1. **Be factual, not presumptive.** Do not assume behavior without checking the
   source code or documentation first. If you are unsure, say so explicitly and
   verify before proceeding.
2. **Use local artifacts as your primary evidence.** The authoritative sources are:
   - `app/` — current source of truth (not memory or prior knowledge)
   - `docs/DECISOES_DE_ARQUITETURA.md` — architecture decisions prose
   - `CLAUDE.md` — DA index + invariantes
   - `AGENTS.md` — this file
3. **Never be lazy or skip verification.** If a claim depends on a specific file,
   read it. If it depends on a config value, grep for it. If it depends on a
   migration, check `alembic/versions/`. Cite line numbers when possible.
4. **Distinguish between what you verified vs. what you infer.** When reasoning
   from patterns (e.g., "this likely follows the same convention as X"), state
   that explicitly — do not present inference as fact.

---

## Environment notes

- `.env` is mode-dependent (native vs container). In native mode use
  `127.0.0.1` for service hosts; in container mode the compose injects
  service names (`postgres`, `qdrant`, `neo4j`).
- `NEO4J_AUTH` only applies on **first initialisation** of the volume.
- `GRAPH_RAG_ENABLED=false` is the default flag — the old `USE_GRAPH_RAG`
  does not exist.
- `EMBEDDING_BACKEND=fastembed` for CI RAG evaluation jobs without Ollama (DA-38).
