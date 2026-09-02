# Arquitetura Detalhada

Visao tecnica do que existe hoje no codigo - nao um plano aspiracional.
Para o "porque" de cada decisao (problemas reais encontrados e como
foram resolvidos), ver a secao "Decisoes de Arquitetura" no
[README](../README.md); este documento e o "o que" e "onde".

## Fluxo

```
IncidentRequest (FastAPI POST /diagnose)
        │
        ▼
   LangGraph: connector -> retrieve -> diagnose -> report -> END
        │              │            │           │
        │              │            │           └─ monta o Markdown final
        │              │            └─ LLM Gateway (app/llm/factory.py)
        │              │               + guardrails deterministicos
        │              └─ Qdrant (app/rag/retriever.py), score_threshold
        └─ conector SAP/nao-SAP (app/connectors/), mock ou real
```

Cada etapa e um node do grafo (`app/agent/graph.py`), instrumentado com
`@observe` (Langfuse). O estado (`CopilotState`) flui entre nodes; o
grafo e compilado uma vez (`get_graph()`, singleton em processo).

## Camadas

| Camada | Onde | Responsabilidade |
|---|---|---|
| API | `app/main.py` | FastAPI, `/health` e `/diagnose`; sem logica de negocio |
| Orquestracao | `app/agent/graph.py` | Grafo LangGraph, prompt, guardrails |
| LLM Gateway | `app/llm/factory.py` | Escolhe o `BaseChatModel` (Ollama/OpenAI/Azure OpenAI) a partir de `Settings` |
| RAG | `app/rag/` | Ingestao (`ingest.py`) e busca (`retriever.py`) via Qdrant |
| Conectores | `app/connectors/` | Um por sistema externo (OData, RFC, ServiceNow); interface comum em `base.py` |
| Config | `app/config.py` | Unica fonte de verdade (`.env` + defaults), nunca hardcoded espalhado |
| Modelos | `app/models.py` | Contratos Pydantic da API (`IncidentRequest`/`DiagnosisResponse`) |

Esta nao e uma Clean Architecture "de livro" com pastas
`domain/application/infrastructure` separadas - e uma separacao
pragmatica por responsabilidade, que ja evita a mistura de
preocupacoes que aquele padrao existe para prevenir (a logica de
prompt/guardrail, por exemplo, sao funcoes puras em `graph.py`,
testaveis sem subir API nem grafo).

## LLM Gateway - por que e como

Ver `app/llm/factory.py` e a Decisao de Arquitetura #10 no README. Em
uma frase: `Settings.llm_provider` decide entre Ollama (default,
local-first, sem custo de API), OpenAI ou Azure OpenAI, sem o resto do
codigo (`graph.py`, prompt, guardrails) precisar saber qual foi
escolhido - todos implementam a mesma interface `BaseChatModel` do
LangChain.

## Conectores - mock vs. real, hoje

| Conector | Estado hoje | Caminho para "real" |
|---|---|---|
| `ODataConnector` | Mock | `httpx`/`requests` contra SAP Gateway/Integration Suite, OAuth2 |
| `RFCConnector` | Mock; `use_real=True` tem esqueleto de `BAPI_IDOC_STATUS` documentado | Exige `pyrfc` + SAP NetWeaver RFC SDK (binario da SAP, fora do PyPI) |
| `ServiceNowConnector` | **Real** (Table API via HTTP) quando `SERVICENOW_INSTANCE_URL` configurado; mock so na ausencia disso | Ja funcional - so falta credencial de um cliente |

Por que RFC (nao so OData) importa para o posicionamento do produto:
clientes ainda em ECC on-premise, sem BTP/Integration Suite, tipicamente
so tem RFC/BAPI como via de automacao - e essa e a base de clientes que
nao consegue adotar SAP AI Core (que exige HANA Cloud). Ver
[TCO_SAP_AI_CORE_VS_SELF_HOSTED.md](TCO_SAP_AI_CORE_VS_SELF_HOSTED.md).

## RAG

Duas collections Qdrant independentes (`app/rag/ingest.py`):
`sap_incident_docs` (usada pelo fluxo de diagnostico) e
`sap_reference_library` (livros/estudo pessoal, nao entra no
diagnostico). Ingestao e idempotente (reprocessar um arquivo substitui
os pontos antigos, nao duplica) e resumivel (estado salvo em disco).

**Nota honesta:** `Settings` tem campos para Neo4j
(`neo4j_uri`/`neo4j_user`/`neo4j_password`) e o stack Docker pessoal
(`~/ai-stack`) sobe um container Neo4j, mas nenhum codigo do pipeline
de RAG atual (`ingest.py`/`retriever.py`) usa esse grafo hoje - e
reservado para uma evolucao futura (GraphRAG relacionando
interfaces/documentos), nao uma feature existente. O
`docker-compose.yml` deste repositorio (self-contained, ver abaixo)
deliberadamente nao inclui Neo4j, para nao sugerir uma dependencia que
o codigo ainda nao exercita.

## Rodando sem depender do `~/ai-stack` pessoal

O `docker-compose.yml` na raiz deste repositorio sobe Ollama + Qdrant +
a API num unico `docker compose up -d`, sem depender do stack completo
de observabilidade (`~/ai-stack`, com Langfuse/Postgres/ClickHouse/
Redis/MinIO) usado no ambiente de desenvolvimento pessoal. Isso importa
porque este projeto tambem funciona como demonstracao para terceiros
(cliente, entrevistador) - que nao tem, nem deveriam precisar montar,
o ambiente pessoal do autor so para rodar o projeto uma vez. Langfuse
continua opcional: sem as chaves configuradas, o app roda normalmente,
so sem tracing.

## Testes

Testes unitarios (`tests/test_connectors.py`, `test_llm_factory.py`,
`test_api.py::test_health_endpoint`) rodam sem nenhuma infraestrutura
externa - inclusive o caminho HTTP real do `ServiceNowConnector`, via
`httpx.MockTransport`. Testes marcados `@pytest.mark.integration`
(`test_graph_e2e.py`, `test_retriever.py`, os `/diagnose` de
`test_api.py`) exigem Qdrant + Ollama rodando e sao pulados
automaticamente (nao falham) quando essa stack nao esta acessivel -
ver `tests/conftest.py`.
