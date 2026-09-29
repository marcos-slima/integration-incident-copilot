# Integration Incident Copilot — User Guide

> Guia de operação · Para arquitetos de integração, analistas de suporte e times de sustentação SAP

---

## O que é

O **Integration Incident Copilot** é um agente de diagnóstico de incidentes de
integração. Dado um incidente — descrição em texto livre e, opcionalmente,
dados estruturados de um sistema SAP ou multi-vendor — ele recupera o caso de
troubleshooting mais relevante de uma base de conhecimento, analisa o contexto
via LLM e retorna uma causa raiz provável com próximos passos concretos.

Não é uma ferramenta de correção automática. É um acelerador de diagnóstico —
reduz o tempo gasto em triagem inicial de incidentes de integração, que em
landscapes SAP complexos pode envolver múltiplos sistemas, logs distribuídos
e conhecimento específico de protocolo (IDoc, RFC, OData, OAuth2).

---

## Para quem

- **Arquitetos de integração SAP** que precisam de um ponto de entrada rápido
  para incidentes de CPI/Integration Suite, iFlow, RFC e IDoc
- **Analistas de suporte N2/N3** que recebem chamados de integração sem
  contexto suficiente
- **Times de sustentação SAP** que mantêm landscapes heterogêneos (ECC,
  S/4HANA, SuccessFactors, sistemas não-SAP)
- **Desenvolvedores SAP BTP/CAP** que integram serviços via OData v4,
  Event Mesh e XSUAA
- **Outros agentes de IA** — via MCP e A2A, o copilot é uma ferramenta que
  um agente maior pode chamar (ver "Superfícies de operação")

---

## Como funciona

O pipeline real tem mais guardrails do que um RAG simples, e cada um é
visível na resposta:

**1. Supervisor (determinístico, sem LLM)** — classifica o incidente em
domínio (`sap`, `saas` ou `generic`) a partir de `interface_type` e
palavras-chave. É código, não modelo: o roteamento é barato, explicável e
testável.

**2. Conector** — se um sistema de origem e identificador forem informados,
o agente busca dados estruturados do incidente naquele sistema (código de
erro, status, mensagem). Sem credencial configurada, o conector opera em
modo demo/mock e a evidência é marcada `simulated`. São 9 conectores:
OData, RFC, ServiceNow, Salesforce, Workday, Ariba, SuccessFactors, CAP e
API Management.

**3. Rule engine (21 regras, sem LLM)** — antes de chamar o modelo, um
catálogo determinístico de erros conhecidos SAP/integração é consultado.
Se o incidente casa com uma regra (ex: IDoc status 51, `RFC_COMM_FAILURE`,
HTTP 401 OAuth2), a resposta volta **sem consumir um único token** — rápida
e com `prompt_digest`/`prompt_version` em `null` na resposta, porque não
houve prompt nenhum (DA-53).

**4. Recuperação (RAG híbrido + reranker)** — o texto é buscado por
similaridade densa E por termos exatos (BM25), fundidos via RRF e
reordenados por um cross-encoder. Termos técnicos exatos
(`RFC_COMM_FAILURE`, `status 51`) pesam tanto quanto a semântica.

**5. Diagnóstico (LLM com guardrails de código)** — o documento recuperado
fundamenta o modelo (`qwen3-coder-next`, MoE 80B/3B ativos). A confiança é
ajustada deterministicamente em código, nunca no prompt: identificador não
reconhecido pelo conector limita a confiança a 0.4; sem documento acima do
threshold, a 0.3.

**6. Evidence layer** — cada afirmação na resposta carrega proveniência:
conector real (`system_observed`), mock (`simulated`), documento RAG
(`retrieved_document`), busca web (`web_untrusted`) ou só o texto do usuário
(`user_reported`). O campo `is_grounded` nunca é autoavaliação do LLM.

---

## Operar

### Subir o servidor

```bash
uv run uvicorn app.main:app --reload   # desenvolvimento
```

- UI web: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Liveness/readiness: `GET /health` e `GET /ready`

### Autenticação (leia antes do primeiro curl)

**Todo endpoint de diagnóstico exige `X-API-Key`.** Se `API_KEY` estiver vazia
no `.env`, uma chave aleatória é gerada no startup e logada em nível WARNING
(DA-18) — o endpoint nunca fica aberto sem chave. Para uso estável (ou para
clientes MCP), fixe a chave no `.env` (ver `.env.example`).

Chaves por superfície:

| Superfície | Header | Setting |
|---|---|---|
| `/diagnose`, `/llm/policy`, `/incidents/{id}/verify`, `/mcp` | `X-API-Key` | `API_KEY` |
| `/a2a` | `X-A2A-Api-Key` | `A2A_API_KEY` |
| `POST /events/incident` | `X-Api-Key` (Event Mesh) | `EVENTS_API_KEY` |
| `/admin` e `/admin/api/*` | `X-Admin-Api-Key` | `ADMIN_API_KEY` |

### Interface web

A UI em `http://localhost:8000` tem três telas:

**Diagnóstico** — formulário com o campo **API Key** (a chave fixada em
`API_KEY` no `.env` do servidor; o backend responde **401** sem ela — DA-18),
descrição do incidente, sistema de origem (os 9 conectores) e identificador
opcional.

**Histórico** — diagnósticos da sessão atual, com confiança e causa raiz.

**Status da stack** — estado dos conectores (real vs. demo/mock) e da
infraestrutura (LLM, RAG, GraphRAG, A2A).

### API REST

```bash
curl -X POST http://localhost:8000/diagnose \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "description": "IDoc travado com status 51 no sistema de destino",
    "interface_type": "rfc",
    "identifier": "RFC-IDOC-51-DEMO"
  }'
```

Resposta (campos principais):

```json
{
  "probable_root_cause": "O material 4711 não está cadastrado no centro 1000...",
  "model_confidence": 0.95,
  "diagnosis_confidence": 0.95,
  "next_steps": ["Verificar existência do material 4711 via MM03", "..."],
  "matched_source": "idoc_status_51.md",
  "evidence_strength": 0.83,
  "llm_model": "qwen3-coder-next:latest",
  "prompt_version": "v1.3",
  "prompt_digest": "sha256:...",
  "incident_id": "8f14e45f-ea...",
  "trace_id": "...",
  "report_markdown": "..."
}
```

Campos que merecem atenção:

- **`diagnosis_confidence` vs `model_confidence`** — a primeira já passou
  pelos guardrails; a segunda é o que o modelo disse de si. Se divergirem,
  algum guardrail agiu.
- **`prompt_digest`/`prompt_version` em `null`** — a rule engine respondeu
  sem LLM. Não é erro: é o caminho rápido e determinístico.
- **`incident_id`** — guarde-o para o loop de verificação (abaixo).
- Request aceita ainda: `logs`, `payload`, `sensitivity_level`
  (`public`/`internal`/`confidential`/`secret` — governa rota do LLM, DA-43),
  `connector_source_system` (`system_key` do catálogo admin, melhora a
  correlação do incidente, DA-50).

**Diagnóstico assíncrono** — mesmo corpo, resposta imediata com `job_id`:

```bash
curl -X POST http://localhost:8000/diagnose/async -H "X-API-Key: $API_KEY" ...
curl http://localhost:8000/diagnose/async/{job_id}
```

### Linha de comando

```bash
uv run python -m app.agent.graph \
  --interface rfc \
  --id RFC-IDOC-51-DEMO \
  --debug \
  "IDoc travado com status 51"
```

`--interface` aceita os mesmos 9 conectores da API; `--model` sobrescreve o
LLM; `--debug` imprime o prompt enviado. Não exige API key (chama o grafo
direto).

### MCP — para agentes de IA usarem o copilot

O servidor MCP mora em `http://localhost:8000/mcp` (Streamable HTTP) e usa o
**mesmo `X-API-Key`** de `/diagnose`. Ferramentas são negadas por default —
o catálogo de capacidades é fail-closed (DA-27). Configuração de cliente
(ex.: `.mcp.json`) está em `CLAUDE.md` (DA-19): **fixe `API_KEY` no `.env`**
ou o cliente não autentica.

### A2A — agente falando com agente

`POST /a2a` implementa JSON-RPC 2.0 (Agent2Agent), autenticado por
`X-A2A-Api-Key`. O agent card público — o que outro agente lê para
descobrir como chamar este — está em
`GET /.well-known/agent-card.json` (com URL absoluta quando
`A2A_BASE_URL` está configurada, exigência do spec A2A 0.3).

### Eventos — diagnóstico orientado a evento

- **Webhook:** `POST /events/incident` recebe CloudEvents (`EVENTS_API_KEY`)
  e dispara `run_diagnosis()` sem ninguém chamar curl (DA-23).
- **AMQP 1.0:** consumidor assíncrono via Solace Cloud (`app/events/
  amqp_consumer.py`), para quem já tem event mesh corporativo.

### Fechar o loop: verificação

Depois de resolver o incidente, **registre o desfecho** — é isso que separa
um assistente de um sistema que aprende:

```bash
curl -X POST http://localhost:8000/incidents/{incident_id}/verify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "root_cause": "Material 4711 ausente no centro 1000",
    "verified_by": "human",
    "correct": true
  }'
```

- `verified_at` é **sempre** gravado; `correct` é opcional — deixe `null`
  quando só a causa foi confirmada, mas o desfecho ainda não (forçar `true`
  infla a acurácia dos dashboards, DA-50).
- `verified_by`: `human` ou `system`.
- Opcionalmente `trace_id` para pontuar o trace no Langfuse.

O histórico verificado aparece em `http://localhost:8000/admin/incidents`,
correlacionado ao sistema integrado quando `connector_source_system` foi
informado.

### Administração

A UI admin (`/admin`, header `X-Admin-Api-Key`) expõe:

- **Models** — registro de modelos/credenciais por ORIGEM, com status
- **Usage** — metering de tokens **reais** (usage_metadata), não estimativa
- **Systems** — catálogo de sistemas integrados e sua correlação com
  incidentes (DA-49/50)
- **Incidents** — histórico persistido de diagnósticos com verificação

Operações auxiliares por script:

- **Drift de contrato SAP** (DA-52): `uv run python
  scripts/check_contract_drift.py` — compara o `$metadata` vivo com a
  baseline; só mudança **breaking** abre incidente.
- **Relatórios**: `uv run python scripts/generate_reports.py`

### Ensinando o copilot: nova base de conhecimento

O copilot só conhece o que está indexado. Caso novo resolvido → documento em
`data/sample_docs/` → reindexar:

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

---

## Como construir um bom prompt

A qualidade do diagnóstico depende diretamente da qualidade da descrição do
incidente. O agente faz busca vetorial — quanto mais contexto técnico
específico você fornecer, mais precisa será a recuperação do caso relevante.

### Princípio geral

> Descreva o incidente como você descreveria para um colega especialista em
> integração SAP que não tem acesso ao sistema no momento — com o erro exato,
> o sistema envolvido, e o que você já tentou.

### O que incluir

**Código de erro ou status** — é o sinal mais forte para a recuperação. Se
você tem um código, coloque-o na descrição, mesmo que já esteja no
identificador.

Ruim:
> "iFlow não está funcionando"

Bom:
> "iFlow retornando HTTP 401 ao tentar autenticar via OAuth2 no SAP Integration Suite"

---

**Nome do protocolo ou adaptador** — RFC, IDoc, OData, SOAP, REST, AS2. O
agente tem documentos específicos por protocolo.

Ruim:
> "Erro de integração entre ECC e S/4HANA"

Bom:
> "Falha de comunicação RFC entre ECC 6.0 e S/4HANA — destino SM59 retornando connection refused na porta 3300"

---

**Status ou código específico do sistema** — para IDocs, o status numérico é
crítico (51, 02, 53). Para OData, o HTTP status code. Para RFC, o código de
exceção (RFC_COMM_FAILURE, SYSTEM_FAILURE).

Ruim:
> "IDoc com problema"

Bom:
> "IDoc ORDERS05 travado com status 51 — SAPSLL: documento de material não encontrado no centro de distribuição 1000"

---

**O que já foi tentado** — ajuda o agente a calibrar os próximos passos e
evitar sugerir o que você já fez.

Ruim:
> "RFC não conecta"

Bom:
> "RFC connection refused no destino SM59. Já verificamos que o dispatcher SAP está ativo via SM50. Suspeita de bloqueio de firewall na porta 3300."

---

**Sistema de destino** — especialmente em landscapes multi-sistema,
identificar o sistema alvo ajuda.

Ruim:
> "timeout ao chamar serviço OData"

Bom:
> "timeout ao chamar serviço OData /sap/opu/odata/sap/API_SALES_ORDER_SRV no SAP Gateway do S/4HANA — query sem filtro retornando mais de 50k registros"

---

### Exemplos completos

**Exemplo 1 — Incidente de autenticação OData**

```
Descrição: iFlow no SAP Integration Suite retornando HTTP 401 ao tentar
consumir serviço OData do S/4HANA. O adapter está configurado com
OAuth2 Client Credentials. A chamada funcionava ontem. Suspeita de
token expirado ou credencial inválida no Security Material.

Sistema: OData
Identificador: CPI-401-DEMO
```

Por que é bom: especifica o protocolo (OAuth2 CC), o sistema (Integration
Suite → S/4HANA), o código de erro (401), e uma hipótese.

---

**Exemplo 2 — IDoc travado**

```
Descrição: IDoc ORDERS05 com status 51 no sistema receptor S/4HANA.
Erro na aplicação: "Material 4711 não encontrado no centro 1000".
IDoc enviado via porta parceiro para destino RECEPCAO_01. Tentativa
de reprocessamento via BD87 falhando com o mesmo erro.

Sistema: RFC
Identificador: RFC-IDOC-51-DEMO
```

Por que é bom: tipo de IDoc, status exato, mensagem de erro literal,
sistema receptor, ação já tentada.

---

**Exemplo 3 — RFC connection refused**

```
Descrição: SM59 não consegue estabelecer conexão RFC com o sistema
de destino PRD. Teste de conexão retorna connection refused na
porta 3300. O sistema PRD está ativo (verificado via SSHADMIN).
Suspeita de regra de firewall alterada hoje durante janela de manutenção.

Sistema: RFC
Identificador: RFC-CONN-REFUSED-DEMO
```

Por que é bom: código de diagnóstico SAP (SM59), porta específica (3300),
ações de verificação já realizadas, contexto temporal (janela de manutenção).

---

**Exemplo 4 — Incidente sem identificador**

```
Descrição: iFlow de integração entre SuccessFactors e SAP HCM travando
com timeout após 30 segundos. O iFlow chama um serviço OData do SAP
HCM via Cloud Connector. Não há filtros na query — ela traz todos os
colaboradores ativos (aproximadamente 45.000 registros). O problema
começou após a base de colaboradores crescer acima de 40.000.

Sistema: Sem conector
```

Por que é bom: mesmo sem identificador, a descrição contém o suficiente
para o RAG recuperar o documento de timeout OData (volume de dados,
ausência de filtro, comportamento esperado).

---

### O que evitar

**Linguagem ambígua sem referência técnica**

> "o sistema está lento" — qual sistema? qual operação? qual protocolo?

**Só o código de erro sem contexto**

> "erro 500" — 500 de qual sistema? OData, CPI, aplicação custom?

**Descrição em nível de negócio sem detalhe técnico**

> "a integração de pedidos de compra não está funcionando" — sem protocolo,
> sistema, código de erro ou comportamento específico

---

## Casos de uso típicos

### IDoc travado (status 51)

Ocorre quando o IDoc chega ao sistema receptor mas falha na aplicação —
geralmente dado mestre ausente (material, fornecedor, conta contábil) ou
validação de negócio.

O que informar: tipo do IDoc (ORDERS05, MATMAS, DEBMAS), status numérico
(51), mensagem de erro da transação WE05, sistema receptor.

Identificadores de demo: `RFC-IDOC-51-DEMO`

### Timeout OData

Ocorre quando uma query OData sem filtro traz volume excessivo de dados, ou
quando o sistema de backend está sobrecarregado.

O que informar: URL do serviço OData, tempo de timeout configurado,
presença ou ausência de filtros `$filter` e `$top`, volume estimado de
registros.

Identificadores de demo: sem identificador específico (use descrição textual)

### Falha de autenticação CPI (HTTP 401)

Ocorre quando o token OAuth2 expira e o adapter não renova automaticamente,
ou quando as credenciais do Security Material estão incorretas/expiradas.

O que informar: tipo de autenticação (OAuth2 CC, Basic Auth, Certificate),
sistema de destino, se houve rotação de credenciais recente.

Identificadores de demo: `CPI-401-DEMO`

### RFC connection refused

Ocorre quando o sistema SAP de destino está inacessível na porta 3300 —
instância parada, firewall, alteração de rede.

O que informar: host e porta do sistema de destino, resultado do teste SM59,
se o sistema está ativo (SM50/SM51), mudanças recentes de infraestrutura.

Identificadores de demo: `RFC-CONN-REFUSED-DEMO`

### Pool timeout de gateway RFC

Ocorre quando todas as conexões do pool de trabalho do SAP Gateway estão
ocupadas — geralmente sob carga alta ou processos RFC pendurados.

O que informar: mensagem de erro exata do gateway, horário do incidente
(pico de uso?), resultado de SM66 (processos ativos).

Identificadores de demo: sem identificador específico (use descrição textual)

---

## Interpretando o resultado

### Badge de confiança

O agente retorna `diagnosis_confidence` entre 0 e 1, exibido como badge
colorido na UI:

- **Alta (verde, ≥ 70%)** — documento recuperado com alta similaridade e caso
  bem coberto pela base. Siga os próximos passos com confiança.
- **Média (amarela, 40–69%)** — o documento é relevante mas o match não é
  perfeito. Use os próximos passos como ponto de partida.
- **Baixa (vermelha, < 40%)** — o incidente não corresponde bem a nenhum caso
  documentado, ou o identificador não foi reconhecido. Considere adicionar
  mais contexto à descrição.

### Confiança artificialmente limitada

Situações em que a confiança é reduzida automaticamente, independente da
análise do LLM:

- Identificador não reconhecido pelo conector → confiança máxima de **0.4**
- Nenhum documento recuperado acima do threshold → confiança máxima de **0.3**
- Conector em modo demo/mock → não limita a confiança, mas a evidência vem
  marcada `simulated`

### Evidência e níveis de confiança

A resposta traz `evidence`: a lista do que fundamenta o diagnóstico, cada
item com um `trust_level`:

| trust_level | Fonte |
|---|---|
| `system_observed` | Conector real (não mock) OU rule engine — o mais forte |
| `simulated` | Conector mock ou fallback — dado de exemplo, não real |
| `retrieved_document` | Chunk RAG (Qdrant) ou histórico GraphRAG |
| `web_untrusted` | Busca web — trate como pista, não como fato |
| `user_reported` | A descrição textual do incidente — o mais fraco |

Um diagnóstico com evidência só `user_reported` está dizendo "estou
trabalhando com o que você me contou, nada mais".

### Documento de referência

O campo `matched_source` indica qual documento da base de conhecimento foi
usado como referência. Se você quiser entender o diagnóstico mais a fundo,
esse documento contém o caso completo com causas, diagnóstico e resolução
típica.

---

## Limitações conhecidas

**O agente diagnostica, não corrige** — os próximos passos são recomendações;
a execução é sempre do analista/arquiteto responsável.

**A base de conhecimento é limitada** — o agente só conhece o que está
indexado em `data/sample_docs/`. Incidentes muito específicos de customização
de cliente ou erros de versões antigas podem não ter correspondência.

**Conectores em modo mock** — sem credencial configurada, o conector retorna
dados simulados. O diagnóstico funciona, mas está baseado em dados de
exemplo — e a evidência é marcada `simulated` para você saber disso.

**SuccessFactors sem validação ponta a ponta** — o conector tem settings e
pipeline completos, mas nunca foi validado contra um tenant real.

**Sem acesso a logs em tempo real** — o agente não lê SM21, ST22 ou o
monitoramento do Integration Suite. O dado do sistema vem do conector via
API/RFC, não de leitura de log.

**Não é um sistema de tickets** — os incidentes **são** persistidos (com
`incident_id`, verificação e correlação com sistemas em `/admin/incidents`),
mas não há integração nativa com ITSM (ServiceNow/Jira) para abrir ou
atualizar chamados: ServiceNow é conector de **origem** de incidente, não
de destino.

---

## Glossário

**IDoc** (Intermediate Document) — formato de mensagem padrão SAP para troca
de dados entre sistemas via EDI ou ALE. Cada IDoc tem um tipo (ex: ORDERS05,
MATMAS) e um status numérico que indica seu estado de processamento.

**RFC** (Remote Function Call) — protocolo proprietário SAP para chamadas de
função entre sistemas ABAP. Usa a porta 3300 (instância 00) por padrão.

**OData** — protocolo REST padronizado (Open Data Protocol) usado pelo SAP
Gateway para expor dados ABAP via HTTP/JSON.

**iFlow** — fluxo de integração no SAP Integration Suite (CPI). Cada iFlow
orquestra a transformação e roteamento de mensagens entre sistemas.

**OAuth2 Client Credentials** — fluxo de autenticação machine-to-machine onde
o sistema cliente se autentica com client_id e client_secret para obter um
token de acesso.

**Security Material** — repositório de credenciais do SAP Integration Suite
onde são armazenadas senhas, certificados e tokens para uso nos adapters dos
iFlows.

**SM59** — transação SAP para gerenciar destinos RFC (conexões com sistemas
externos).

**BD87** — transação SAP para reprocessamento de IDocs com erros de
aplicação.

**WE05** — transação SAP para monitoramento de IDocs.

**RAG** (Retrieval-Augmented Generation) — técnica que combina recuperação de
documentos (busca vetorial) com geração de texto por LLM. O agente usa RAG
para fundamentar o diagnóstico em casos documentados, não em "conhecimento
geral" do modelo.

**XSUAA** — serviço de autenticação e autorização do SAP BTP (Business
Technology Platform), baseado em OAuth2/OIDC.

**MCP** (Model Context Protocol) — protocolo para expor ferramentas a
agentes de IA. O copilot é servidor MCP em `/mcp`, com catálogo de
capacidades fail-closed (DA-27/19).

**A2A** (Agent2Agent) — protocolo para agentes de IA diferentes cooperarem.
O copilot é agente A2A em `/a2a` (JSON-RPC 2.0), com agent card público em
`/.well-known/agent-card.json`.

**CloudEvents** — especificação CNCF para descrever eventos em um formato
comum. O webhook `POST /events/incident` (DA-23) a recebe.

---

*Integration Incident Copilot · github.com/marcos-slima/integration-incident-copilot*
