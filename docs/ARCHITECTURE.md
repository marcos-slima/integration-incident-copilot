# Arquitetura Detalhada

Visao tecnica do que existe hoje no codigo - nao um plano aspiracional.
Para o "porque" de cada decisao (problemas reais encontrados e como
foram resolvidos), ver a secao "Decisoes de Arquitetura" no
[README](../README.md); este documento e o "o que" e "onde".

## Fluxo

```mermaid
flowchart TD
    A["IncidentRequest<br/>FastAPI POST /diagnose<br/>OU A2A message/send"] --> S["<b>supervisor</b><br/>classifica o dominio (DA-22)<br/>deterministico, sem LLM"]
    S --> B["<b>connector</b><br/>SAP/nao-SAP (app/connectors/)<br/>mock ou real"]
    B --> C["<b>retrieve</b><br/>Qdrant hibrido dense+sparse BM25<br/>incidents + reference_library<br/>fusao RRF + reranker cross-encoder"]
    C --> W["web_search<br/>SAP Community/GitHub (fallback)"]
    W --> D{"GraphRAG<br/>opt-in?"}
    D -->|"sim"| E["graph_enrich<br/>historico da interface no Neo4j"]
    D -->|"nao (default)"| R{"agent_domain?<br/>(DA-22)"}
    E --> R
    R -->|"sap"| F1["<b>sap_diagnose</b><br/>especialista SAP<br/>LLM Gateway + guardrails"]
    R -->|"saas / generic"| F2["<b>saas_diagnose</b><br/>especialista multi-fornecedor<br/>LLM Gateway + guardrails"]
    F1 --> G{"GraphRAG<br/>opt-in?"}
    F2 --> G
    G -->|"sim"| H["graph_write<br/>grava no Neo4j"]
    G -->|"nao (default)"| I["<b>report</b><br/>monta o Markdown final"]
    H --> I
    I --> J["END"]

    style D fill:#f5f5f5,stroke:#999
    style G fill:#f5f5f5,stroke:#999
    style R fill:#f5f5f5,stroke:#999
    style S fill:#fff3cd,stroke:#e0a800
    style B fill:#e8f0fe,stroke:#4285f4
    style C fill:#e8f0fe,stroke:#4285f4
    style F1 fill:#e8f0fe,stroke:#4285f4
    style F2 fill:#e8f0fe,stroke:#4285f4
```

Os nodes `graph_enrich`/`graph_write` (GraphRAG) so entram no grafo
quando `GRAPH_RAG_ENABLED=true` - com a flag desligada (default), o
grafo compilado tem a mesma sequencia de nodes de antes da fase
GraphRAG, byte a byte (ver secao GraphRAG abaixo). O `supervisor` e o
roteamento condicional para `sap_diagnose`/`saas_diagnose` (DA-22,
secao Multi-agent abaixo) rodam SEMPRE, com ou sem GraphRAG - e a unica
mudanca estrutural que se aplica nos dois modos do grafo.

Cada etapa e um node do grafo (definidos em `app/agent/nodes.py`, orquestrados em `app/agent/graph.py`), instrumentado com
`@observe` (Langfuse). O estado (`CopilotState`) flui entre nodes; o
grafo e compilado uma vez (`get_graph()`, singleton em processo).

Dois "consumidores" chamam a mesma orquestracao (`run_diagnosis`), sem
nenhuma logica duplicada entre eles: o endpoint REST `/diagnose`
(`app/main.py`) e a camada A2A (`app/a2a/`, ver secao propria abaixo).

## Camadas

| Camada | Onde | Responsabilidade |
|---|---|---|
| API | `app/main.py` | FastAPI, `/health`, `/diagnose`, Agent Card A2A, servidor MCP (`/mcp`); rate limiting 10/min por IP (slowapi); API Key via `X-API-Key` (API_KEY no .env, ou gerada automaticamente no startup se ausente - DA-18) |
| A2A | `app/a2a/` | Camada de interoperabilidade externa (Agent Card, task manager, JSON-RPC), chama a mesma orquestracao do `/diagnose` |
| Orquestracao | `app/agent/graph.py` · `app/agent/nodes.py` · `app/agent/state.py` | Grafo LangGraph (orquestrador ~136 linhas), nodes (connector/retrieve/web_search/diagnose/report), tipos (CopilotState, DiagnosisModel) |
| LLM Gateway | `app/llm/factory.py` | Escolhe o `BaseChatModel` (Ollama/OpenAI/Azure OpenAI) a partir de `Settings` |
| RAG | `app/rag/` | Ingestao (`ingest.py`) com pymupdf4llm + schema rico; retrieval unificado (`retriever.py`) — hybrid search + reranker cross-encoder; GraphRAG opt-in (`graph_store.py`) via Neo4j |
| Conectores | `app/connectors/` | Um por sistema externo (OData, RFC, ServiceNow, Salesforce, Workday, SAP Ariba, SAP CAP, SAP API Management); interface comum em `base.py` |
| Config | `app/config.py` | Unica fonte de verdade (`.env` + defaults), nunca hardcoded espalhado |
| Modelos | `app/models.py` | Contratos Pydantic da API (`IncidentRequest`/`DiagnosisResponse`) |

Esta nao e uma Clean Architecture "de livro" com pastas
`domain/application/infrastructure` separadas - e uma separacao
pragmatica por responsabilidade, que ja evita a mistura de
preocupacoes que aquele padrao existe para prevenir (a logica de
prompt/guardrail, por exemplo, sao funcoes puras em `app/agent/nodes.py`,
testaveis sem subir API nem grafo).

**Nota honesta sobre `app/services/`:** a pasta existe (criada cedo,
"para quando precisar") mas continua vazia - propositalmente. Hoje
`run_diagnosis()` (em `app/agent/graph.py`) ja cumpre o papel de "camada de
servico": e a unica funcao que os dois consumidores existentes
(`/diagnose` e `app/a2a/task_manager.py`) chamam, sem duplicar logica
entre eles. Criar uma classe/modulo `IncidentDiagnosisService` que so
delegasse para essa mesma funcao seria indirecao sem beneficio real -
exatamente o tipo de "camada vazia por vaidade arquitetural" que este
documento critica no `genai-engineering-template` (`src/application/`
la tambem vazio, mas la sem nada que cumprisse o papel por baixo). Se
um dia houver mais de uma logica de orquestracao real para coordenar
(nao so repassar uma chamada), a pasta ganha conteudo entao - nao antes.

## LLM Gateway - por que e como

Ver `app/llm/factory.py` e a Decisao de Arquitetura #10 no README. Em
uma frase: `Settings.llm_provider` decide entre Ollama (default,
local-first, sem custo de API), OpenAI ou Azure OpenAI, sem o resto do
codigo (`app/agent/nodes.py`, prompt, guardrails) precisar saber qual foi
escolhido - todos implementam a mesma interface `BaseChatModel` do
LangChain.

## Hybrid Inference - fallback de resiliencia (DA-20)

Quarto item do roadmap "Projeto evolucao planejada" (apos AI Gateway/
Evidence Layer, fechamento de autenticacao do A2A e servidor MCP).
Criterio escolhido: RESILIENCIA, nao roteamento por qualidade/
complexidade (as duas alternativas descartadas - escalar por
`evidence_strength` baixo, ou rotear por complexidade do caso antes de
chamar o LLM - custam uma segunda chamada de LLM em parte dos casos e
exigem calibrar um limiar; resiliencia so entra em acao quando o
provider primario esta genuinamente indisponivel).

`app/llm/factory.py::invoke_with_hybrid_fallback()` roda a chamada com
`settings.llm_provider` (Ollama, tipicamente) e, SE
`settings.llm_fallback_provider` estiver configurado (`.env`, vazio por
default = comportamento identico a antes desta fase) E a falha for de
TRANSPORTE (`ConnectionError`/`httpx.ConnectError`/
`httpx.TimeoutException` - Ollama fora do ar, timeout de rede), refaz a
MESMA chamada com o provider de fallback antes de desistir. Erro de
APLICACAO (JSON malformado, prompt invalido) NUNCA aciona o fallback -
subir normalmente evita mascarar um bug real atras de uma segunda
chamada de LLM (custo/latencia desnecessarios). Os sub-agentes de
diagnostico (`sap_diagnosis_node`/`saas_diagnosis_node`, ver DA-22
logo abaixo) usam isso, via `_run_diagnosis_agent()`, para a chamada
ao agente ReAct; qual provider respondeu de fato fica exposto em
`DiagnosisResponse.llm_provider_used` - transparencia, nao so um
fallback silencioso.

## Conectores - mock vs. real, hoje

Todo conector agora segue o MESMO criterio: configuracao ausente = modo
demo/mock; configuracao presente = chamada real. Nenhum exige mudar
codigo Python para ativar - so preencher variaveis no `.env`.

| Conector | Estado hoje | Falta so |
|---|---|---|
| `ODataConnector` | **Real** (OAuth2 client_credentials + OData v2) quando `ODATA_SERVICE_URL` configurado | Um tenant CPI/Integration Suite real para validar contra producao |
| `RFCConnector` | **Real, validado contra ABAP Cloud Developer Trial real** (A4H rel 754, `RFC_SYSTEM_INFO` via `pyrfc` 3.3.1 + SDK 7.50 PL19) | `BAPI_IDOC_STATUS` nao disponivel no Trial — criar funcao Z ou usar landscape real para validar BAPI especifica |
| `ServiceNowConnector` | **Real, validado contra ServiceNow PDI real** (Table API via HTTP, Basic Auth) | Nada - segundo conector com validacao ponta-a-ponta contra sistema real |
| `SalesforceConnector` | **Real, validado contra Salesforce Developer Edition real** (OAuth2 Client Credentials + SOQL) | Nada - primeiro conector com validacao ponta-a-ponta contra sistema real, nao so mock |
| `WorkdayConnector` | **Real** (OAuth2 + REST) quando `WORKDAY_TENANT` configurado | Um tenant Workday real |
| `AribaConnector` | **Real** (OAuth2 + REST) quando `ARIBA_BASE_URL` configurado | Acesso a Ariba Network/API Business Hub |
| `CAPConnector` | **Real, validado contra SAP CAP real** (OData v4 + XSUAA client_credentials, BTP Trial) | Nada - terceiro conector com validacao ponta-a-ponta contra sistema real |
| `APIManagementConnector` | ⚠️ **Implementado com schema ESPECULATIVO** (OAuth2 Client Credentials + endpoint assumido por analogia a produtos similares - NAO confirmado contra documentacao real do SAP API Management) | Validar contrato real da Analytics API contra um tenant de verdade; corrigir endpoint/schema conforme necessario |

**Nota sobre a assimetria SuccessFactors↔Workday:** o cenario de
referencia "SuccessFactors↔Workday" e representado hoje SO pelo lado
Workday - "SuccessFactors" aparece apenas como contexto narrativo no
payload mock do `WorkdayConnector` (`grep -rn "SuccessFactors" app/`
confirma isso: zero classe/modulo, so docstring/comentario). Nao ha
`SuccessFactorsConnector` implementado. Isso e uma decisao implicita,
nao documentada ate agora - registrada aqui para nao parecer descuido.

Por que ainda nao foi fechado: SuccessFactors expoe OData v2 (SFAPI)
com autenticacao via SAML bearer assertion, mais complexa que o
padrao OAuth2 client_credentials ja usado nos demais conectores -
exigiria um mecanismo de auth novo, nao reuso do que ja existe.
Registrado como proximo item de backlog de conectores, nao
implementado nesta fase (mesma disciplina de "um conector por vez,
validado, antes do proximo" aplicada aos demais).


"Real" aqui quer dizer: o codigo de producao (fetch de token OAuth2,
montagem do header, parsing da resposta) e exercitado de verdade nos
testes via `httpx.MockTransport` simulando a API documentada de cada
fornecedor - nao existe, para nenhum destes tres ultimos (Salesforce/
Workday/Ariba) nem para o RFC, uma conta/tenant real disponivel para
validar contra producao. Essa e a mesma ressalva ja feita sobre
`RFCConnector._fetch_real` desde a Fase 8, agora estendida a todos os
conectores no mesmo padrao - nao e uma limitacao nova, e a mesma
limitacao aplicada com consistencia.

Por que RFC (nao so OData) importa para o posicionamento do produto:
clientes ainda em ECC on-premise, sem BTP/Integration Suite, tipicamente
so tem RFC/BAPI como via de automacao - e essa e a base de clientes que
nao consegue adotar SAP AI Core (que exige HANA Cloud). Ver
[TCO_SAP_AI_CORE_VS_SELF_HOSTED.md](TCO_SAP_AI_CORE_VS_SELF_HOSTED.md).

## RAG

Duas collections Qdrant (`app/rag/ingest.py`) — ambas participam do
fluxo de diagnostico a partir da v2.0:

- `sap_incident_docs` — documentos de troubleshooting (`.md`), hybrid
  search (dense + BM25 esparso, fusao RRF)
- `sap_reference_library` — PDFs tecnicos SAP (guias, notas, livros),
  busca densa; schema rico: `source`, `text`, `filename`, `page_number`,
  `document_id`, `chunk_index`, `file_hash`, `title`, `category`,
  `ingested_at` (parser: `pymupdf4llm`, preserva estrutura Markdown)

**Retrieval unificado (`_retrieve_unified`):** consulta as duas
collections em paralelo, funde os resultados por score composto
(`alpha=0.7 × cosine + 0.3 × rrf_normalizado`) e passa os candidatos
para o **reranker semantico** (`cross-encoder/ms-marco-MiniLM-L-6-v2`
via `sentence-transformers`) que reordena por relevancia real ao par
`(query, chunk)` — muito mais preciso que similaridade de cosseno pura.

**Ingestao:** idempotente por IDs deterministicos
(`md5(document_id::chunk_index)`) — sem delete-before-upsert. Estado
salvo por hash de conteudo (`hash:filename`) em vez de path, detectando
mudancas mesmo com renomeacao de arquivo.

## GraphRAG (Neo4j) - opt-in, nao no caminho default

`app/rag/graph_store.py` implementa o que antes era so "reservado para
uso futuro": grava cada diagnostico concluido no Neo4j como um grafo
(`Incident -AFFECTS-> Interface -RUNS_ON-> System`, `Incident
-HAS_ROOT_CAUSE_IN-> Document`) e consulta esse grafo por incidentes
anteriores na MESMA interface antes de gerar um novo diagnostico -
contexto de recorrencia ("essa RFC destination ja teve 3 incidentes
antes, sempre pela mesma causa") que a busca vetorial no Qdrant nao da,
porque Qdrant acha o documento de CONHECIMENTO mais parecido, nao o
HISTORICO relacional de uma interface especifica.

Continua **desligado por default** (`GRAPH_RAG_ENABLED=false`) - a
decisao de manter assim nao mudou (ver Decisao de Arquitetura #9): o
Qdrant ja resolve o caso de uso principal, e o grafo so agrega valor
depois de meses de historico real acumulado, nao com os 8 documentos de
demonstracao deste repositorio. A diferenca em relacao a antes desta
fase e que agora **existe codigo real, testado (com driver fake, ver
`tests/test_graph_store.py`), pronto para ligar** quando fizer sentido:

1. `docker compose --profile graphrag up -d neo4j` (nao sobe com
   `docker compose up` default - profile dedicado, ver `docker-compose.yml`)
2. `GRAPH_RAG_ENABLED=true` no `.env`

Nao ha um passo 3 manual: desde a Decisao de Arquitetura #19 (DA-21),
`ensure_constraints()` roda sozinho no `lifespan` do FastAPI quando a
flag esta ligada (ver `app/main.py`) - o antigo
`uv run python -m app.rag.graph_store --init` continua disponivel para
uso manual/explicito, mas deixou de ser obrigatorio.

Nao ha nada para descomentar no Python - so essa flag + a infra de fato
existir. Com a flag desligada, `build_graph()` monta exatamente a mesma
sequencia de nodes de antes desta fase (ver `app/agent/graph.py`),
custo zero. **Nao testado contra um Neo4j real** (sem Docker daemon
disponivel no ambiente onde isso foi construido) - mesma ressalva
honesta do `RFCConnector._fetch_real`.

### Hardening operacional (DA-21): degradacao graciosa, dedup e limpeza

Ligar GraphRAG significa que o Neo4j passa a estar no caminho critico
de CADA diagnostico (`graph_enrich_node` antes, `graph_write_node`
depois - ver `app/agent/graph.py`). Sem cuidado, uma instabilidade
pontual do Neo4j (restart, rede, pool esgotado) derrubaria o
diagnostico inteiro por causa de uma camada que deveria ser so um
enriquecimento, nao uma dependencia rigida. Tres reforcos, todos
cobertos por teste com driver fake (`tests/test_graph_store.py`,
`tests/test_nodes_graph_degradation.py`):

- **Degradacao graciosa por tipo de excecao** - `GRAPH_UNAVAILABLE_EXCEPTIONS`
  (`neo4j.exceptions.DriverError` + `TransientError`) delimita exatamente
  o que conta como "infra indisponivel agora, siga sem grafo": conexao
  recusada, timeout, servidor em restart. `graph_enrich_node` cai para
  "sem historico" (`graph_history: []`) e `graph_write_node` pula a
  escrita, ambos logando um `warning` - o diagnostico principal (RAG +
  LLM) segue intacto. Deliberadamente NAO inclui `Neo4jError` em geral:
  um `ConstraintError`/`CypherSyntaxError` sinaliza um bug NOSSO
  (Cypher ou schema errado), nao indisponibilidade de infra, e continua
  propagando normalmente - mesmo principio que separa falha de
  transporte de erro de aplicacao no Hybrid Inference (DA-20).
- **Formatacao com deduplicacao por recorrencia** -
  `format_graph_context_for_prompt()` agrupa ocorrencias CONSECUTIVAS
  da mesma causa raiz numa unica linha com contador ("ja ocorreu 3x"),
  em vez de repetir a mesma linha e desperdicar orcamento de prompt numa
  interface "flapping" (falhando repetidamente pela mesma causa).
  Agrupamento e so entre vizinhos - a lista ja vem mais-recente-primeiro,
  e uma causa diferente intercalada quebra o agrupamento, para nao
  esconder que algo diferente aconteceu no meio.
- **Utilitario manual de limpeza** - `prune_ungrounded_hypotheses()`
  (CLI: `--prune-ungrounded --older-than-days N`, default 90) remove
  hipoteses NAO confirmadas (`is_grounded=false`) antigas, que so
  acumulam ruido no grafo sem nunca terem sido corroboradas. Incidentes
  com causa raiz confirmada (`is_grounded=true`) nunca sao tocados, sob
  nenhuma idade. E manutencao explicita do operador - nunca chamada
  automaticamente por nenhum node ou pelo `lifespan`, mesmo principio de
  "nada e deletado sem o operador pedir" usado em outras partes deste
  projeto.

**Nao-objetivo explicito desta fase**: nenhuma das tres mudancas acima
foi validada contra um Neo4j real (mesma limitacao de ambiente das
demais integracoes reais deste projeto - sem Docker disponivel onde
isso foi construido). A cobertura de teste e com driver fake
(`FakeSession`, mesmo padrao usado nos conectores HTTP com
`httpx.MockTransport`); validar contra um Neo4j real fica como proximo
passo do lado do operador/autor, fora deste ambiente de desenvolvimento.

## Multi-agent - supervisor + especialistas por dominio (DA-22)

Antes desta fase, um unico node (`diagnose_node`) tratava QUALQUER
incidente com uma persona fixa de "especialista em integracao SAP" -
incoerente com o principio de design deste projeto de que SAP e um
conector entre iguais, nao o eixo arquitetural (ver Decisao de
Arquitetura #8/#13 no README). Um incidente de webhook do Salesforce
recebia a mesma expertise "OData/IDoc/RFC/CPI" que um incidente de RFC.

**Decisao:** um `supervisor_node` (`app/agent/supervisor.py`) roda
PRIMEIRO no grafo (antes ate do `connector`) e classifica
deterministicamente o dominio do incidente:

- `interface_type` em `{odata, rfc, cap}` -> `"sap"`
- `interface_type` em `{servicenow, salesforce, workday, ariba}` ->
  `"saas"`
- sem `interface_type` (fluxo por descricao livre): palavra-chave SAP
  na descricao (`idoc`, `iflow`, `cpi`, `rfc`, `bapi`, `abap`, `btp`,
  etc.) -> `"sap"`; senao -> `"generic"`

A classificacao e CODIGO, nao uma chamada de LLM - mesmo principio ja
aplicado aos guardrails de confianca (DA-15): decisao estrutural
barata, deterministica e 100% testavel sem depender de infraestrutura
de IA. `app/agent/graph.py::_route_to_specialist` le `agent_domain` do
estado e direciona o grafo (via `add_conditional_edges`) para UM dos
dois sub-agentes especialistas - nunca os dois no mesmo incidente, sem
duplicar custo de chamada de LLM:

- `sap_diagnosis_node` - persona SAP (OData, IDoc, RFC, CPI/Integration
  Suite, BTP)
- `saas_diagnosis_node` - persona multi-fornecedor (ServiceNow,
  Salesforce, Workday, Ariba, APIs REST/OAuth2 em geral); tambem cobre
  `"generic"` (nenhum dominio identificado), aplicando o mesmo
  raciocinio generalista de troubleshooting de integracao

Os dois sub-agentes compartilham o mesmo nucleo (`_run_diagnosis_agent`
em `app/agent/nodes.py`) - agente ReAct, Hybrid Inference (DA-20),
parsing de JSON e guardrails de confianca (DA-15) permanecem
IDENTICOS; a unica diferenca entre eles e a persona/expertise injetada
no prompt. Qual dominio foi usado fica exposto em
`DiagnosisResponse.agent_domain` (mesma filosofia de transparencia do
`llm_provider_used`, DA-20) e aparece no relatorio Markdown final.

**Validacao:** `tests/test_supervisor.py` (classificacao pura, sem
LLM) e `tests/test_nodes_multiagent.py` (cada sub-agente recebe a
persona certa, o roteamento condicional manda para o node certo -
incluindo o caso de seguranca `agent_domain` ausente cair no
especialista generalista em vez de quebrar - e `agent_domain` chega
ate `DiagnosisResponse`), todos mockando `invoke_with_hybrid_fallback`
diretamente (sem Ollama real, mesmo padrao de `test_llm_factory.py`).
`build_graph()` foi verificado manualmente compilando com sucesso nos
dois modos (GraphRAG ligado/desligado), confirmando os nodes esperados
no grafo resultante.

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

## A2A (Agent2Agent) - interoperabilidade externa

`app/a2a/` implementa a proposta arquivada em
[docs/proposals/a2a-interoperability-layer.md](proposals/a2a-interoperability-layer.md)
(ler esse documento para o contexto de negocio completo e a ressalva
sobre a GA inbound do Joule, prevista para Q4/2026 e ainda nao
disponivel). Em resumo tecnico:

- **Agent Card** (`app/a2a/agent_card.py`), publicado em
  `GET /.well-known/agent-card.json` (path padrao do protocolo A2A)
- **Task manager** (`app/a2a/task_manager.py`) - traduz uma mensagem
  A2A em `IncidentRequest` e chama `run_diagnosis()`, a MESMA funcao
  usada pelo `/diagnose` REST; nao ha logica de diagnostico duplicada
- **Servidor JSON-RPC 2.0** (`app/a2a/server.py`), montado em
  `POST /a2a`, com os metodos `message/send` e `tasks/get`

Simplificacao deliberada: dos 8 estados de task que o protocolo A2A
define, so os 4 que este agente sincrono e autocontido realmente
alcanca sao implementados (`submitted -> working -> completed|failed`)
- `input_required`/`auth_required`/`canceled`/`rejected` nao se aplicam
a um agente que nao pede dado adicional a meio do processo nem tem
fluxo de autorizacao interativo. Autenticacao e uma chave estatica via
header (`A2A_API_KEY`). Desde a DA-18, essa chave NUNCA fica vazia em
memoria: se nao vier do `.env`, `app/main.py::_ensure_api_keys_configured`
gera uma aleatoria no startup e avisa no log - o endpoint nunca fica
silenciosamente aberto. Uma chave de OAuth2/JWT entre agentes continua
sendo o gap de producao real (exigiria um fluxo de autorizacao
interoperavel entre agentes de fornecedores diferentes), nao uma
limitacao escondida.

Testado com FastAPI `TestClient` + um `diagnosis_fn` stub injetado no
`TaskManager` (mesmo padrao de injecao de dependencia dos conectores
HTTP) - inclui um teste que prova que uma falha na orquestracao vira
task com `status.state == "failed"`, nao um erro HTTP 500, que e o
comportamento correto de um agente A2A (erro de negocio, nao de
transporte).

## MCP (Model Context Protocol) - capability catalog, nao so "conectar um LLM a uma ferramenta"

`app/mcp/server.py` expoe o Copilot como SERVIDOR MCP (nao cliente -
decisao explicita, ver docstring do modulo para a leitura alternativa
descartada), montado em `POST /mcp/` (com barra final - `app.mount()`
redireciona 307 a partir de `/mcp` sem barra, comportamento padrao do
Starlette, nao especifico do MCP). Terceiro item do roadmap "Projeto
evolucao planejada", depois de AI Gateway minimo/Evidence Layer (DA-15/
16/17) e do fechamento de autenticacao do A2A (DA-18) - a especificacao
MCP de 2026 caminha para stateless scaling, cache de capability catalog
e autorizacao empresarial, o que aproxima MCP de infraestrutura de
producao em vez de um protocolo isolado.

Duas ferramentas, ambas READ-ONLY ("leitura primeiro" - o roadmap e
explicito sobre isso):

- `diagnose_incident` - chama a MESMA `run_diagnosis()` usada por
  `/diagnose` e `/a2a`; nao ha logica de diagnostico duplicada pela
  terceira vez.
- `list_connectors` - inspeciona `settings` (sem nenhuma chamada de
  rede) e informa, por `interface_type`, se o conector esta configurado
  para dados reais ou opera em modo demo/mock.

Autenticacao: reusa `settings.api_key` (o MESMO X-API-Key de
`/diagnose`, DA-18) via `RequireApiKeyMiddleware`, um middleware ASGI
simples - o SDK MCP oferece `AuthSettings`/`TokenVerifier` (OAuth2)
para autorizacao enterprise real, mas isso seria sobre-engenharia
tendo REST e A2A ja usando API Key estatica; manter um unico mecanismo
de autenticacao em toda a superficie HTTP em vez de dois.

Detalhe de implementacao que vale registrar (pegou um teste real nesta
fase): `StreamableHTTPSessionManager.run()` - que gerencia as sessoes
MCP - so pode ser chamado UMA vez por instancia de processo; por isso
seu ciclo de vida entra no `lifespan` do app FastAPI raiz (`app.mount()`
NAO propaga eventos de lifespan para sub-apps automaticamente), e por
isso os testes em `tests/test_mcp.py` usam um UNICO
`with TestClient(app) as ...` para toda a suite daquele arquivo.

## Event Mesh - ingestao orientada a evento (DA-23)

Ate esta fase, o Copilot so reagia a chamadas EXPLICITAS: `POST
/diagnose` humano, mensagem A2A, ou tool call MCP. Fechando o sexto
item do roadmap arquitetural, `POST /events/incident`
(`app/main.py` + `app/events/`) permite que um sistema de monitoracao
externo (CPI, Solution Manager, um listener de fila/IDoc) dispare o
diagnostico automaticamente, publicando um evento em vez de esperar
alguem chamar a API.

**Formato do evento:** [CloudEvents](https://cloudevents.io/) -
`type`/`source`/`id`/`time`/`data` - o mesmo formato que o SAP Event
Mesh usa em modo **REST/Webhook push subscription** (alem do AMQP 1.0
nativo). `data` carrega exatamente os mesmos campos de
`IncidentRequest` (a informacao e a MESMA que um humano digitaria em
`/diagnose`, so que originada automaticamente). Hoje so um `type` e
reconhecido - `com.sap.integration.incident.detected.v1` - modelado
como `Literal` em `IncidentEventEnvelope` (`app/models.py`): qualquer
outro valor e rejeitado com `422` automaticamente pelo Pydantic, em
vez de tentar interpretar silenciosamente um payload de formato
desconhecido.

**Por que webhook e nao um consumidor AMQP:** nao e um atalho para
evitar montar um broker real - webhook e um modo de entrega de
PRIMEIRA CLASSE do proprio SAP Event Mesh, documentado ao lado do
AMQP, e e o unico que da para exercitar de ponta a ponta com testes
reais (TestClient HTTP) sem depender de infraestrutura externa - mesma
logica pragmatica ja aplicada ao GraphRAG (DA-21) e ao MCP (DA-19).

**Decisao de auth:** `X-Event-Mesh-Api-Key` e uma chave DEDICADA,
separada de `X-API-Key` (`/diagnose`) e `X-A2A-Api-Key` (`/a2a`) -
mesmo padrao de geracao automatica no startup se nao configurada
(DA-18). Isolamento deliberado: o webhook secret normalmente fica
configurado num sistema de monitoracao externo (fora do controle
direto deste projeto), entao um vazamento ali nao deve comprometer os
outros dois canais de acesso.

`app/events/consumer.py::handle_incident_event()` converte o evento em
`IncidentRequest` e chama a MESMA `run_diagnosis()` usada por
`/diagnose` e pela camada A2A - nenhuma logica de diagnostico
duplicada, so mais um ponto de entrada.

**Nao-objetivo explicito desta fase:** processamento assincrono/fila
real (hoje e sincrono - o webhook so retorna quando o diagnostico
termina, sujeito ao mesmo rate limit de 10/min de `/diagnose`) e
consumo AMQP direto do SAP Event Mesh - se o volume de eventos ou a
necessidade de backpressure justificar, isso e evolucao natural futura,
nao um gap escondido.

**Validacao:** `tests/test_events.py` cobre o mapeamento evento ->
IncidentRequest, a chamada a `run_diagnosis()`, autenticacao (401 sem
chave/chave errada), rejeicao de `type` desconhecido (422), limite de
tamanho da descricao (422, mesma regra de `/diagnose`) e geracao
automatica da chave no startup - tudo com `run_diagnosis` mockado, sem
depender de Ollama/Qdrant reais.

## Deploy em produção - SAP BTP Kyma Runtime (DA-24)

Fecha o último item do roadmap arquitetural consolidado deste projeto.
`deploy/kyma/` (manifests + `README.md` próprio com o passo a passo)
contém um Deployment (2 réplicas, probes em `/health`, usuário
não-root), Service, HorizontalPodAutoscaler (2-6 réplicas por CPU),
ConfigMap (config não sensível) e um `secret.example.yaml` - TEMPLATE,
nunca aplicado direto, com todo valor prefixado `CHANGE-ME` (testado em
`tests/test_kyma_manifests.py::test_secret_example_has_no_real_looking_values`).

**APIRule** (módulo API Gateway do Kyma) expõe o Service pelo Istio
Gateway gerenciado, com `accessStrategy: noop` - o Copilot já tem sua
própria autenticação por API key em cada endpoint (DA-18/DA-23), então
não duplica autenticação na camada de rede. Evoluir para `jwt`
(validando tokens XSUAA do BTP) seria a evolução natural de uma
integração mais profunda com serviços BTP (Destination service,
XSUAA) - escopo explicitamente descartado nesta fase em favor de só
empacotar o deploy (ver decisão de escopo no README, ### 22).

**Correções feitas no `Dockerfile` nesta mesma fase** (descobertas ao
revisar o empacotamento para produção, corrigidas na origem em vez de
contornadas só nos manifests - mesmo princípio de "sem débito técnico"
aplicado o resto da sessão):

- `COPY pyproject.toml uv.lock` + `uv sync --frozen` - build
  reproduzível (antes, `uv sync` sem lockfile no build resolvia contra
  as versões mais recentes compatíveis com `pyproject.toml`, não contra
  as travadas no lockfile commitado)
- usuário não-root (`appuser`, uid 1000) - boa prática de segurança
  para clusters com PodSecurityStandards restritivos
- `CMD` chama `.venv/bin/uvicorn` diretamente - corrige na origem o bug
  documentado em `docs/DEPLOY.md` (`uv run` ressincronizando dependências
  de dev a cada start, derrubando o container em rede restrita);
  `docker-compose.yml` não precisa mais do `command:` override que
  contornava isso

**Não-objetivos explícitos desta fase** (mesma honestidade já aplicada
ao GraphRAG/DA-21 e ao MCP/DA-19): nenhum manifest foi validado contra
um cluster Kyma real (nenhum cluster acessível neste ambiente de
desenvolvimento) - o schema do CRD `APIRule` já mudou de versão mais de
uma vez na história do Kyma, então o `apiVersion` usado deve ser
conferido contra o cluster alvo antes de aplicar de verdade; nenhum
build/push de imagem Docker foi testado (Docker não disponível neste
ambiente); Qdrant e Neo4j (GraphRAG, que continua opt-in) não são
implantados por este bundle - são pré-requisitos externos, documentados
em `deploy/kyma/README.md`.

**Validação:** `tests/test_kyma_manifests.py` (11 testes) garante que
todo YAML do bundle é sintaticamente válido e internamente consistente
- mesmo namespace em todos os recursos namespaced, probes de saúde em
`/health` (nunca um endpoint autenticado, para não travar o rollout em
`CrashLoopBackOff` por falta de Secret), Pod rodando não-root, HPA/APIRule
apontando para o Deployment/Service certos, e o `kustomization.yaml`
referenciando só arquivos que existem (excluindo deliberadamente o
template de Secret).

**Follow-up pós-DA-24 - multi-stage build do frontend no `Dockerfile`:**
a DA-24 tocou o `Dockerfile` duas vezes (reprodutibilidade e usuário
não-root) mas deixou passar um débito já autodenunciado em
`docs/DEPLOY.md` ("Melhoria pendente: mover isso para um multi-stage
build no Dockerfile"): a imagem copiava `static/` pronto (`COPY static/
static/`), mas `static/dist/` (o build do frontend React/Vite) está no
`.gitignore` e só existia se alguém rodasse manualmente `npm run build`
+ `cp` antes de `docker build` - um `git clone` limpo seguido de
`docker build -t ... .` (exatamente o passo 1 do `deploy/kyma/README.md`)
falhava ou gerava uma imagem sem frontend. Corrigido com um segundo
estágio (`node:22-slim AS frontend-build`) que roda `npm ci && npm run
build` a partir do código-fonte em `frontend/`, e o estágio final copia
`frontend/dist` para `static/dist` via `COPY --from=frontend-build`.
`docs/DEPLOY.md` seção 2 atualizada para refletir que não há mais passo
manual; `.dockerignore` adicionado (não existia) para não mandar
`frontend/node_modules/`, `.venv/` e `.git/` para o contexto de build.
Validado rodando `npm ci && npm run build` isoladamente (produz o
`dist/` esperado: `assets/`, `favicon.svg`, `icons.svg`, `index.html`) -
o build de imagem completo em si não foi testado (Docker não disponível
neste ambiente, mesma limitação já registrada acima).

Junto com esse fix, um segundo problema encontrado na mesma revisão foi
corrigido: o modelo LLM default estava inconsistente entre
`app/config.py` (`qwen3-coder-next:latest`, fonte canônica) e
`.env.example`/`docker-compose.yml` (ambos `qwen2.5-coder:32b`) - os
dois arquivos alinhados ao valor canônico do `config.py`.

## Evidence/Trust Layer + correcao do threshold do RAG (DA-25)

Uma segunda revisao arquitetural externa, feita apos o roadmap
consolidado (AI Gateway → A2A → MCP → Hybrid Inference → GraphRAG →
Multi-agent → Event Mesh → BTP/Kyma), apontou um backlog priorizado
(P0-P3). Esta fase ataca os dois itens P0 restantes (os outros dois,
Docker multi-stage e `uv.lock`, ja estavam corrigidos - ver secao
"Follow-up pos-roadmap" acima).

**1. Threshold do RAG aplicado cedo demais (antes do reranker).**
`app/rag/retriever.py::_retrieve_hybrid` descartava candidatos com
cosseno denso abaixo de `score_threshold` (0.5) ANTES do cross-encoder
(reranker) ter qualquer chance de avaliar a relevancia semantica de
verdade - um documento com BM25/RRF excelente (match de termo exato,
ex: "IDoc status 51") mas cosseno moderado (ex: 0.47) era descartado
sem nunca chegar ao reranker. Corrigido invertendo a ordem:

```
dense + sparse -> RRF -> candidate pool -> cross encoder -> evidence threshold -> top K
```

- `_retrieve_hybrid` agora so descarta ruido semantico extremo
  (`MIN_CANDIDATE_FLOOR = 0.05`), nao mais o threshold real de
  confianca; o pool de candidatos da fusao RRF tambem cresceu
  (`fusion_limit = max(top_k * 5, HYBRID_PREFETCH_LIMIT)` em vez de
  `limit=top_k`), porque antes o proprio Qdrant ja truncava a fusao
  para so `top_k` resultados antes de qualquer filtragem - o reranker
  nunca via mais candidatos do que o resultado final ja teria mesmo
  assim.
- `_retrieve_unified` agora reranqueia TODO o pool de candidatos
  (inclusive quando ha so 1, caso central do bug relatado) e so DEPOIS
  aplica a decisao de confianca, via `_evidence_admission_score()`: um
  hit e admitido se o cosseno OU o `rerank_score` (clampado para
  [0, 1], mesma convencao ja usada em `_compute_evidence_strength`)
  atingir `score_threshold` - o reranker ganhou um caminho proprio
  para "salvar" um documento que o cosseno sozinho descartaria.
- O gatilho do fallback para `reference_library` (DA-17) continua
  olhando para o melhor cosseno de `incidents_hits` antes do rerank -
  nao mudou de criterio, so passou a conviver com candidatos fracos
  que antes eram descartados cedo demais e agora entram no pool do
  reranker.
- Validado com `tests/test_retriever_evidence_threshold.py` (8 testes,
  mockando os limites de infraestrutura - Qdrant/reranker - sem
  depender de infra real): cobre o caso central (cosseno fraco +
  rerank forte -> admitido), o caso de controle (ambos fracos ->
  rejeitado) e os dois ramos do fallback de `reference_library`.

**2. Evidence/Trust Layer.** A resposta do diagnostico (`DiagnosisResponse`)
ganhou um campo `evidence: list[Evidence]` (`app/models.py`) - uma
entrada por fonte REAL consultada nesta execucao (conector, RAG,
GraphRAG, busca web, descricao do usuario), cada uma com um
`trust_level` decidido pelo TIPO da fonte:

```
system_observed (conector real)
  > retrieved_document (RAG/GraphRAG)
  > web_untrusted (busca web, nao curada)
  > user_reported (descricao do usuario, nunca verificada)
```

(`simulated` substitui `system_observed` quando o conector retornou
dado mock/fallback.) A lista e montada de forma inteiramente
DETERMINISTICA em `app/agent/nodes.py::_assemble_evidence(state)` - o
LLM nunca declara/cita suas proprias fontes, mesmo principio ja usado
em `evidence_strength` (DA-15) e nos demais guardrails deste projeto
("guardrails em codigo, nao em prompt"; ver `learnings.md` do projeto).
`_assemble_evidence` e chamada tanto em `report_node` (para a nova
secao "Evidencias" do `report_markdown`) quanto em
`graph.py::run_diagnosis` (para popular `DiagnosisResponse.evidence`)
- funcao pura e barata, entao chamada duas vezes em vez de adicionar
mais uma chave ao `CopilotState` so pra passar o mesmo dado adiante.

Isso resolve diretamente o ponto mais forte da revisao externa: antes,
a resposta era "o LLM deu uma resposta"; agora e "o LLM produziu uma
hipotese sustentada por evidencias rastreaveis", cada uma com uma
fonte e um nivel de confianca explicitos - pre-requisito arquitetural
citado pela propria revisao para quando o MCP ganhar tools de
ESCRITA (prompt injection deixa de ser so um problema de qualidade de
resposta e passa a ser um problema de autorizacao operacional).

**Nao-objetivo desta fase:** a Evidence/Trust Layer, sozinha, ainda
nao incluia o modelo `VERIFIED_AS` (verificacao humana/sistema
separada da hipotese do LLM) proposto pela revisao para o GraphRAG -
implementado depois, como item proprio (ver secao "GraphRAG - modelo
VERIFIED_AS (DA-28)" abaixo).

Validado com `tests/test_evidence.py` (13 testes) - cada fonte
isoladamente, a combinacao de todas, validacao do modelo `Evidence`
contra os dicts montados por `_assemble_evidence`, e a secao nova do
`report_markdown`.

## AI Gateway v1 - policy, circuit breaker e budget (DA-26)

Terceiro item da segunda revisao arquitetural externa (P1): o "LLM
Gateway" existente (`app/llm/factory.py`) era, tecnicamente, so um LLM
Provider Factory / Abstraction Layer - decidia QUAL `BaseChatModel`
instanciar, mas nao tinha, de forma centralizada, policy, budget nem
circuit breaker de verdade. `app/llm/gateway.py` (novo) adiciona essas
tres coisas SOBRE a Hybrid Inference ja existente (DA-20) - auth
continua na borda HTTP (X-API-Key por endpoint, DA-18/DA-23), nao
duplicada aqui.

`app/agent/nodes.py::_run_diagnosis_agent` (usado pelos dois
sub-agentes especialistas, DA-22) agora chama
`gateway.invoke_via_gateway()` em vez de
`factory.invoke_with_hybrid_fallback()` diretamente - e o UNICO ponto
de entrada de chamada LLM no grafo.

**1. Data Classification + Policy Routing.** Todo incidente e
classificado deterministicamente (`classify_sensitivity`, mesmo sinal
ja usado em `evidence_strength`/DA-15 e no Evidence/Trust Layer/DA-25:
dado real de conector, nao mock/fallback) como `confidential` ou
`public`. Dado `confidential` NUNCA pode ser roteado para um provider
cloud (`openai`/`azure_openai`) - nem como fallback. Isso fecha um gap
real: o setup default de Hybrid Inference e primario=`ollama` (local)
+ fallback=`openai`/`azure` (cloud) - sem esta policy, um Ollama fora
do ar faria um incidente com dado real de producao SAP vazar para um
provider externo. Se a policy nao deixa nenhum provider candidato (ex:
`llm_provider="openai"` sem fallback local configurado, e o incidente
e confidencial), a chamada falha explicitamente com
`PolicyViolationError` - nunca silenciosamente tenta cloud mesmo
assim.

**2. Circuit Breaker.** `CircuitBreaker` (in-memory, por provider, por
processo) substitui o try/except direto da DA-20: depois de
`settings.llm_gateway_circuit_failure_threshold` (default 3) falhas de
transporte CONSECUTIVAS, o provider fica "aberto" por
`settings.llm_gateway_circuit_cooldown_seconds` (default 30s) -
chamadas seguintes pulam esse provider sem tentar, em vez de esperar o
mesmo timeout de rede de novo contra algo que ja sabemos que esta
fora. Reseta para fechado no primeiro sucesso.

**3. Budget.** `_estimate_cost_usd` estima o custo (heuristica de
~4 caracteres/token x tabela de preco aproximada por provider -
`ollama` sempre `0.0`) ANTES de cada chamada; se ultrapassar
`settings.llm_gateway_max_cost_usd` (default 0.50, deliberadamente
permissivo - existe pra pegar um caso patologico, nao pra orcamento
fino de producao), a chamada e rejeitada com `PolicyViolationError`
sem nunca invocar o provider.

**4. Audit log.** Uma linha de log estruturado por tentativa
(`logger.info`/`warning`, nunca `print`) com provider, sensibilidade,
decisao (`status=success|failure|circuit_open|budget_rejected`),
latencia e custo estimado.

**Nao-objetivos explicitos desta v1** (backlog em aberto, ver
`learnings.md` do projeto): IAM/auth (ja resolvido na borda HTTP, nao
duplicado aqui); PII/DLP de verdade (um scanner de dados sensiveis no
CONTEUDO do prompt - `sanitize_untrusted_input` protege contra prompt
injection, nao e a mesma coisa que um scanner de PII); tenant
isolation (projeto ainda single-tenant); circuit breaker compartilhado
entre replicas (e in-memory por processo - os 2+ pods do deploy Kyma,
DA-24, nao compartilham esse estado entre si; precisaria de um backend
tipo Redis para isso em producao multi-instancia).

Validado com `tests/test_llm_gateway.py` (20 testes) - policy de
roteamento, circuit breaker (abre/fecha/expira cooldown, isolado por
provider) e budget, sem depender de nenhum provider real (mesmo padrao
ja usado em `test_llm_factory.py` para `invoke_with_hybrid_fallback`).

## Capability Registry + Agent Execution Policy (DA-27)

Ultimo item P1 da segunda revisao arquitetural externa. O servidor
MCP (`app/mcp/server.py`, DA-19) so tinha autenticacao de TRANSPORTE
(X-API-Key compartilhada) - nenhuma distincao entre tools por risco.
Hoje isso nao e um problema pratico (as duas tools expostas,
`diagnose_incident` e `list_connectors`, sao 100% read-only), mas a
revisao apontou o motivo de resolver isso ANTES de qualquer tool de
escrita: um prompt injection contra um LLM que so faz `diagnosis` tem
como pior consequencia um diagnostico errado; contra um LLM com
`tool selection -> tool execution`, a consequencia pode ser alterar
SAP, reiniciar um iFlow ou executar uma operacao real - prompt
injection deixa de ser um problema de qualidade de resposta e vira um
problema de AUTORIZACAO OPERACIONAL.

`app/mcp/policy.py` (novo) cria a base para isso:

- **Capability Registry** (`CAPABILITY_REGISTRY`): uma entrada
  `ToolPolicy` por tool exposta - `risk_level`, `destructive`,
  `scopes_required`, `approval_required`, `data_sensitivity` (mesma
  classificacao confidential/public do AI Gateway, DA-26) e
  timeout/retries.
- **enforce(tool_name, context)**: FAIL-CLOSED - uma tool SEM entrada
  no registry e negada por padrao (`PolicyDeniedError`), nunca
  permitida por omissao. Verifica scopes concedidos no
  `ExecutionContext` e, quando a tool exige `approval_required=True`,
  se ja foi explicitamente aprovada.
- `diagnose_incident` e `list_connectors` (`app/mcp/server.py`) agora
  chamam `enforce()` como primeira linha - nenhuma tool roda sem
  passar pelo registry primeiro, inclusive qualquer tool futura.

Com as duas tools atuais sendo read-only e de baixo risco,
`DEFAULT_EXECUTION_CONTEXT` (usado quando o caller nao passa um
contexto explicito) sempre permite ambas - o comportamento observavel
do servidor MCP NAO muda nesta fase. O valor esta em preparar o
terreno: qualquer tool de escrita futura (ex: `restart_iflow`,
`close_ticket`) precisa OBRIGATORIAMENTE de uma entrada no
`CAPABILITY_REGISTRY` antes de ser exposta - esquecer isso resulta em
`PolicyDeniedError` em runtime, nao em uma tool silenciosamente
liberada.

**Nao-objetivos explicitos desta v1** (backlog em aberto, ver
`learnings.md` do projeto): granularidade de scope POR CHAVE de API
(hoje ha uma unica `X-API-Key` compartilhada por todo o servidor MCP -
multiplas chaves com scopes diferentes exigiria um esquema de
credenciais mais rico, ex: OAuth2/TokenVerifier, que `app/mcp/server.py`
ja documenta como sobre-engenharia para o estagio atual); fluxo de
aprovacao humana de verdade (o campo `approved` de `ExecutionContext`
existe para `enforce()` ja saber verificar isso, mas nenhum mecanismo
real ainda o preenche com `True`).

Validado com `tests/test_mcp_policy.py` (9 testes) - registro das duas
tools atuais, fail-closed para tool nao registrada, enforcement de
scope e de aprovacao (usando uma tool hipotetica de escrita registrada
so dentro do teste, para nao afetar `CAPABILITY_REGISTRY` fora dele).

## GraphRAG - modelo VERIFIED_AS (DA-28)

Penultimo item do backlog da segunda revisao externa (P2). Motivo:
`is_grounded` (DA-16, ver secao GraphRAG acima) e um proxy
AUTOMATICO - `evidence_strength >= GROUNDED_EVIDENCE_THRESHOLD` no
momento do diagnostico - ainda e a hipotese do LLM, so que com
evidencia forte o suficiente pra nao ser descartada de cara. Sem uma
distincao explicita entre "hipotese com boa evidencia" e "fato
confirmado por alguem que investigou depois", o grafo corre o risco de
virar um loop de retroalimentacao epistemico: a hipotese de hoje vira
"historico" (fato) pro proximo diagnostico na mesma interface, sem
nunca ter sido de fato confirmada - exatamente o risco que a revisao
apontou como o mais serio do GraphRAG.

**Escopo escolhido:** so o modelo `VERIFIED_AS` (verificacao pontual
por incidente), nao o grafo de topologia completo
(`System->API->iFlow->Event->Credential`) que a revisao tambem cita
como evolucao possivel do schema. Motivo: este repositorio nao tem
NENHUMA fonte de dados real para popular uma topologia (os 8-conectores
mock nao expõem esse nivel de detalhe de infraestrutura) - inventar
dados so pra ter um schema mais rico seria pior que nao ter a
funcionalidade (mesmo principio de "nao simular alem do que da pra
defender" usado em outras partes deste projeto).

**Modelagem:**

```
(Incident)-[:VERIFIED_AS {verified_by, verified_at}]->(RootCause {text})
```

Um no `RootCause` separado (em vez de so propriedades planas no
`Incident`) permite, no futuro, agregar quantos incidentes distintos
compartilham a mesma causa raiz confirmada, sem reprocessar texto
livre.

- **`verify_incident(incident_id, verified_root_cause, verified_by,
  session=None)`** (`app/rag/graph_store.py`) - o UNICO caminho que
  marca `Incident.verified = true`. Chamada explicita apenas -
  `verified` nunca e inferido por score/threshold, ao contrario de
  `is_grounded`. `verified_root_cause` pode DIVERGIR de `root_cause` (a
  hipotese original do LLM) - e justamente o caso mais importante de
  capturar (o LLM errou, um humano corrigiu).
- **`POST /incidents/{incident_id}/verify`** (`app/main.py`, novo
  endpoint, mesma autenticacao `X-API-Key` de `/diagnose`) - superficie
  HTTP para um operador (ou outro sistema, ex: ticket fechado com causa
  raiz confirmada) registrar a verificacao. 404 explicito (nao
  silencioso) se GraphRAG estiver desligado ou o `incident_id` nao
  existir no grafo - ao contrario da maioria das funcoes deste modulo
  (no-op silencioso por design quando GraphRAG esta desligado), este e
  um endpoint chamado INTENCIONALMENTE esperando um efeito.
- **Pre-requisito resolvido junto:** `DiagnosisResponse.incident_id`.
  Antes desta mudanca, o id gravado no Neo4j era gerado DENTRO de
  `graph_write_node` e descartado ali mesmo - nunca chegava ao caller
  da API, entao nao havia como saber qual id referenciar em
  `/incidents/{id}/verify`. Corrigido na origem: o id agora e gerado
  uma unica vez em `graph.py::run_diagnosis()`, passado pelo
  `CopilotState` (`incident_id`), usado por `graph_write_node` (em vez
  de gerar um novo ali), e devolvido em `DiagnosisResponse.incident_id`
  - `None` quando GraphRAG esta desligado OU o incidente nao tinha
  `interface_type`/`identifier` suficientes pra ter sido gravado
  (`upsert_incident_graph` e no-op nesse caso; devolver um id mesmo
  assim seria enganoso).
- **`graph_context()`**: o filtro de admissao (antes so `is_grounded`)
  passa a ser `is_grounded OR verified` - uma verificacao humana/sistema
  explicita e evidencia mais forte que o proxy automatico, entao nunca
  deveria ficar de fora do contexto injetado no prompt so porque o
  diagnostico ORIGINAL teve baixa confianca.
- **`format_graph_context_for_prompt()`**: tres niveis de confianca no
  texto injetado no prompt, do mais forte ao mais fraco - "causa raiz
  VERIFICADA" (`verified=true`) > "causa raiz confirmada anteriormente"
  (`is_grounded=true`, automatico) > "HIPOTESE NAO CONFIRMADA" (nenhum
  dos dois). Quando verificado, a linha usa `verified_root_cause` (a
  causa CONFIRMADA) em vez de `root_cause` (a hipotese original do LLM,
  que pode ter sido corrigida) - o LLM nao deve ver as duas misturadas
  sem saber qual e o fato.

**Nao-objetivo explicito desta fase:** nenhum fluxo de UI/operador para
CHAMAR `/incidents/{id}/verify` foi construido - o endpoint existe e e
testado, mas hoje so pode ser chamado via API diretamente (curl,
Postman, um sistema externo de tickets). Um painel/fluxo de verificacao
humana fica como proximo passo natural, fora do escopo desta fase (que
era resolver o RISCO ARQUITETURAL apontado pela revisao, nao construir
UI).

Validado com testes novos em `tests/test_graph_store.py`
(`verify_incident` - desligado, incidente nao encontrado, escrita bem
sucedida, `verified_by` default; filtro de `graph_context` incluindo
verificado mesmo sem `is_grounded`; formatacao com o terceiro nivel de
confianca, incluindo o caso de nao agrupar um incidente verificado com
um nao-verificado que tenha a mesma causa raiz), `tests/test_api.py`
(endpoint `/verify` - 404 desligado, 404 incidente inexistente, 200
sucesso, autenticacao) e `tests/test_nodes_multiagent.py` +
`tests/test_nodes_graph_degradation.py` (`incident_id` threading de
`run_diagnosis()` ate `graph_write_node`) - 180 testes passando no
total (`-m "not integration"`).

**Atualizacao (avaliacao externa, medio prazo item 5 - "Metricas e
feedback"):** o endpoint `/verify` ganhou um segundo efeito
independente do grafo - um score booleano `diagnosis_correct` no trace
Langfuse original (`DiagnosisResponse.trace_id`, capturado em
`run_diagnosis()` independente de GraphRAG). GraphRAG desligado deixou
de ser 404 automatico - vira 400 SO se tambem nao houver trace_id/
correct informados (nada para registrar em lugar nenhum); GraphRAG
ligado com incident_id inexistente continua 404. Ver
`app/models.py::VerifyIncidentRequest` (campos `correct`/`trace_id`
novos) e `app/main.py::verify_incident_endpoint`.

## Benchmark cientifico de rerankers (DA-29)

Ultimo item do backlog da segunda revisao arquitetural externa. O
reranker de producao (`cross-encoder/ms-marco-MiniLM-L-6-v2`, ver
secao RAG acima) nunca tinha sido comparado formalmente contra
alternativas - inclusive multilingues, relevante porque as queries
reais sao majoritariamente em portugues e o baseline foi treinado so
em ingles (MS MARCO).

`scripts/benchmark_rerankers.py` reranqueia o corpus inteiro de
`data/sample_docs/` (chunked com os MESMOS parametros de producao -
`MarkdownTextSplitter`, `chunk_size=500`, `chunk_overlap=50`) contra
os 13 casos in-scope de `data/eval/rag_eval_dataset.json`, para 4
modelos candidatos - `ms-marco-L6` (baseline), `ms-marco-L12` (mesma
familia, mais profundo), `mmarco-mMiniLMv2` (multilingue, treinado no
mMARCO) e `bge-reranker-base` (multilingue, maior). Metricas puras
(Hit@1, Recall@5, MRR@5, nDCG@5 com relevancia binaria) isoladas em
`app/rag/eval_metrics.py` - testadas sem depender de nenhum modelo
carregado (`tests/test_eval_metrics.py`, 15 testes) - mais latencia
media/p95, delta de RSS do processo (proxy de RAM via `psutil`) e
contagem de parametros (proxy de custo computacional).

**Resultado**: `mmarco-mMiniLMv2` supera o baseline em toda metrica de
qualidade (Hit@1 0.85->0.92, MRR@5 0.92->0.96, nDCG@5 0.94->0.97) e
empata em qualidade com `bge-reranker-base` (mesmos 4 numeros) sendo
3.5x mais rapido (1195ms vs. 4171ms) com menos da metade dos
parametros. Metodologia completa, resultados por query, nota de
recursos do ambiente onde rodou (2 vCPUs/~3.8GB RAM/~3.7GB disco, sem
GPU) e a recomendacao (NAO aplicada nesta fase - troca de uma linha em
`RERANKER_MODEL`, documentada e pendente de decisao do operador) em
`docs/RERANKER_BENCHMARK.md`. Dados brutos por query em
`data/eval/reranker_benchmark_results.json`.

**Nao-objetivo explicito**: `BAAI/bge-reranker-v2-m3` (multilingue mais
forte, ~2.2GB) nao foi testado por risco de OOM/disco cheio na maquina
especifica onde isso rodou - candidato natural pra uma rodada futura
com mais recursos. O script em si (baixa modelos reais do HF Hub, mede
latencia real) nao roda no CI/suite de testes - so a logica pura de
metricas e testada automaticamente, mesmo tratamento dado a
`scripts/eval_rag.py`.

Com isso, TODOS os itens do backlog priorizado pela segunda revisao
arquitetural externa (P0/P1/P2) estao fechados.

## Testes

Testes unitarios (`tests/test_connectors.py`, `test_llm_factory.py`,
`test_graph_store.py`, `test_a2a.py`,
`test_api.py::test_health_endpoint`) rodam sem nenhuma infraestrutura
externa - inclusive os caminhos HTTP reais dos conectores
(ServiceNow/OData/Salesforce/Workday/Ariba), via `httpx.MockTransport`,
e o GraphRAG, via uma sessao Neo4j fake. Testes marcados
`@pytest.mark.integration` (`test_graph_e2e.py`, `test_retriever.py`,
os `/diagnose` de `test_api.py`) exigem Qdrant + Ollama rodando e sao
pulados automaticamente (nao falham) quando essa stack nao esta
acessivel - ver `tests/conftest.py`. O CI (`.github/workflows/tests.yml`)
roda `pytest tests/ -m "not integration"` - toda a suite nao-integracao,
nao mais um arquivo especifico (gap corrigido nesta fase).

**Atualizacao (avaliacao externa, medio prazo item 6 - "Fila
assincrona"):** novo `POST /diagnose/async` enfileira o diagnostico
via RQ (mesmo Redis usado pela persistencia de tasks A2A, item 2
acima - ver `app/queue.py`) e devolve `{"job_id", "status": "queued"}`
(202), em vez de bloquear a requisicao ate o LLM terminar. `GET
/diagnose/async/{job_id}` faz o polling do resultado
(`{"job_id", "status", "result", "error"}`). `POST /diagnose` sincrono
continua existindo sem nenhuma mudanca. Sem `REDIS_URL` configurada,
os dois endpoints assincronos devolvem 503 em vez de degradar
silenciosamente. O processamento de verdade depende de um worker RQ
rodando (`docker compose --profile async up -d redis worker`, novo
servico "worker" em docker-compose.yml, mesma imagem da API) - sem
ele, jobs enfileirados ficam presos em "queued" indefinidamente.

Design: `DiagnosisQueue` (app/queue.py) e uma camada fina, testavel
via injecao de fakes (mesmo espirito de `RedisTaskStore`,
app/a2a/task_store.py) - a funcao efetivamente executada pelo worker
(`run_diagnosis_job`) precisa ser importavel por dotted-path
("app.queue.run_diagnosis_job"), requisito do RQ para desserializar o
job no processo do worker.

**Validacao:** `tests/test_queue.py` (camada `DiagnosisQueue` testada
com fakes, sem Redis/RQ reais) e `tests/test_api.py` (endpoints
`/diagnose/async`, incluindo 503 sem `REDIS_URL` e 404 para job
inexistente).
