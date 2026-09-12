# Processo de Desenvolvimento — SAP Integration Copilot

> Este documento mapeia o processo real seguido na construção do
> projeto, fase por fase, com objetivo, atividades, artefatos
> gerados e critério de saída de cada uma. É complementar às
> **Decisões de Arquitetura** (README) — lá está *o que* foi
> decidido; aqui está *como* o trabalho foi conduzido.
>
> As fases refletem a ordem cronológica real de execução, não uma
> reconstrução idealizada — inclui os pontos onde algo quebrou e
> precisou ser revisto, porque isso também é processo.

## Visão geral

| # | Fase | Status |
|---|---|---|
| 1 | Setup & Fundação | ✅ Concluída |
| 2 | Construção do Agente | ✅ Concluída |
| 3 | Qualidade & Confiabilidade | ✅ Concluída |
| 4 | Observabilidade & Configuração | ✅ Concluída |
| 5 | Segurança & Release | ✅ Concluída |
| 6 | Governança & Documentação | 🔄 Contínua |
| 7 | Domínio Real (conectores SAP de verdade) | ✅ Concluída — ver Fase 9 |
| 8 | Acessibilidade & Multi-Vendor (LLM Gateway, ServiceNow real) | ✅ Concluída |
| 9 | Fechamento das Fases Futuras (multi-vendor completo, GraphRAG, A2A) | ✅ Concluída |

---

## Fase 1 — Setup & Fundação

**Objetivo:** estabelecer o ambiente de desenvolvimento local e
validar a viabilidade técnica básica (RAG) antes de investir em
orquestração.

**Atividades realizadas:**
- Diagnóstico do ambiente Ubuntu (hardware, ROCm, Ollama, Docker)
- Ambiente Python isolado via `uv` (Python 3.12, dependências travadas)
- Stack Docker (Qdrant, Neo4j, Langfuse completo) com correção de
  problema real (ClickHouse exigindo `CLICKHOUSE_CLUSTER_ENABLED=false`
  em setup single-node)
- Bootstrap do repositório (`pyproject.toml`, estrutura de pastas, git)
- Prova de conceito do RAG com 4 documentos de exemplo — validação
  isolada do retriever antes de integrar a qualquer agente

**Artefatos:** `~/ai-lab`, `~/ai-stack`, repositório inicial,
`app/rag/ingest.py`/`retriever.py` (v1)

**Critério de saída:** retriever encontrando o documento certo com
score alto para os 4 casos de teste, de forma isolada — decisão
consciente de validar a peça mais arriscada (recuperação de
informação) antes de construir o resto em cima dela.

## Fase 2 — Construção do Agente

**Objetivo:** orquestrar o RAG num agente funcional, incluindo
simulação de dados de sistema (não só texto digitado).

**Atividades realizadas:**
- Grafo LangGraph linear (`retrieve → diagnose → report`)
- Decisão consciente de separar bases de conhecimento (incidentes
  curados vs. biblioteca de referência de 36GB) para não diluir
  precisão do retriever
- Conectores SAP mock (OData + RFC) com interface comum
  (`SAPConnector`), permitindo trocar por implementação real sem
  reescrever o grafo
- Node de conector inserido **antes** do retrieve — dados do sistema
  têm prioridade sobre descrição textual do usuário

**Artefatos:** `app/agent/graph.py` (v1), `app/connectors/`

**Critério de saída:** pipeline completo (conector → RAG → LLM →
relatório) funcionando ponta-a-ponta para os 4 cenários de incidente.

## Fase 3 — Qualidade & Confiabilidade

**Objetivo:** identificar e corrigir falhas reais de comportamento do
agente antes de considerá-lo confiável — esta foi a fase mais longa e
mais valiosa do processo.

**Atividades realizadas (na ordem em que os problemas apareceram):**
1. Bug de mistura de contexto (LLM combinando causa raiz de
   documentos diferentes) → corrigido restringindo o contexto ao
   documento top-1
2. Não-determinismo com `temperature=0` sem seed → corrigido com
   `seed=42`, validado com 5 execuções idênticas
3. Guardrail determinístico para dados de fallback do conector → não
   depender só da autoavaliação do LLM para uma propriedade de
   segurança
4. Suíte de testes automatizada (16 testes: unitários de conector,
   integração de retriever, end-to-end do grafo) formalizando os
   cenários validados manualmente
5. Regressão real pega pelo próprio `pytest` após uma mudança de
   prompt (whitespace) — confirmou o valor da suíte
6. Comparação formal de modelo via `promptfoo` (pipeline real, não
   LLM isolado) → decisão por `qwen2.5-coder:32b`, com dado
   observável (comportamento sob incerteza), não intuição

**Artefatos:** `tests/`, `promptfooconfig.yaml`,
`scripts/promptfoo_provider.py`

**Critério de saída:** 16/16 testes passando de forma estável e
reprodutível, com a escolha de modelo embasada em comparação
documentada.

## Fase 4 — Observabilidade & Configuração

**Objetivo:** eliminar dependência de mim (ou de qualquer pessoa) para
inspecionar o que o sistema está fazendo e por quê.

**Atividades realizadas:**
- Identificação de gap real: `.env` existia desde o início, mas nunca
  era lido pelo código (URLs/modelo hardcoded)
- `app/config.py` centralizando configuração via `pydantic-settings`,
  com comando de inspeção (`python -m app.config`) verificável pelo
  próprio desenvolvedor
- Debugger visual configurado (`.vscode/launch.json`) — depuração
  independente de assistência externa
- Tracing Langfuse instrumentado (`@observe` por node + `CallbackHandler`
  no LLM), incluindo bug de flush ausente em execuções via `pytest`
  (8 de 16 traces chegando) corrigido via fixture `autouse`

**Artefatos:** `app/config.py`, `.vscode/launch.json`, tracing ativo

**Critério de saída:** configuração e comportamento do sistema
verificáveis pelo desenvolvedor sozinho, com evidência visual real
(trace no Langfuse, não print de terminal).

## Fase 5 — Segurança & Release

**Objetivo:** publicar o projeto com confiança de que nenhum segredo
vazou e que existe verificação automática contínua.

**Atividades realizadas:**
- `gitleaks`: varredura de todo o histórico do git (não só estado
  atual) — confirmado limpo
- `pre-commit`: hooks automáticos (lint/format, detecção de segredo,
  bloqueio de arquivo grande) — pegou problemas reais de lint na
  primeira execução, corrigidos antes do commit
- Publicação no GitHub (`gh repo create --push`)
- GitHub Actions: CI rodando lint + testes unitários a cada push,
  badge de status real no README

**Artefatos:** `.pre-commit-config.yaml`, `.github/workflows/tests.yml`,
repositório público

**Critério de saída:** repositório público, badge de CI verde,
histórico de commits limpo confirmado por ferramenta.

## Fase 6 — Governança & Documentação (contínua)

**Objetivo:** manter a documentação como reflexo fiel do estado real
do código — não maior, não menor.

**Atividades realizadas:**
- Seção "Decisões de Arquitetura" no README, registrando os 4 bugs
  reais encontrados nas Fases 3-4
- Documento consolidado de ferramentas de sustentação/desenvolvimento
- Auditoria e correção de documentação defasada (status de
  ferramentas já implementadas listadas como pendentes; menção
  imprecisa ao uso do Neo4j)
- Este documento

**Prática estabelecida:** ao final de qualquer sessão com mudança de
código relevante, verificar se a documentação ainda bate com a
realidade antes de considerar o trabalho concluído.

## Fase 7 — Domínio Real (concluída na Fase 9)

**Objetivo original:** substituir os conectores mock por integração
real com um sistema SAP (mesmo que sandbox/trial), fechando a lacuna
entre "demo bem construída" e "ferramenta que toca produção".

Ficou registrada por várias sessões como "próxima fase natural do
roteiro, não iniciada". Foi fechada em duas etapas: RFC (`use_real`)
na Fase 8, OData (`use_real`) na Fase 9 — ver seção correspondente
abaixo. "Real" aqui significa código de produção completo e testado
via mock de transporte (HTTP/RPC), não validação contra um sistema SAP
de produção real (que este projeto nunca teve acesso a um) — essa
ressalva permanece, documentada explicitamente em vez de escondida.

## Fase 8 — Acessibilidade & Multi-Vendor (LLM Gateway, ServiceNow real)

**Objetivo:** viabilizar o posicionamento de "acesso à IA para quem
não pode adotar SAP AI Core" com código real, não só intenção de
negócio — LLM plugável (não travado em Ollama) e pelo menos um
conector não-SAP genuinamente funcional.

**Atividades realizadas:**
- `app/llm/factory.py` — LLM Gateway plugável (Ollama/OpenAI/Azure
  OpenAI) reaproveitando o `BaseChatModel` do LangChain, sem interface
  própria reinventada
- `ServiceNowConnector` — primeiro conector não-SAP com chamada HTTP
  real (Table API), escolhido por ter a API pública mais simples de
  validar com `httpx.MockTransport`
- `RFCConnector.use_real` — caminho real via `pyrfc`/`BAPI_IDOC_STATUS`,
  com detecção de feature e `ConfigurationError` claro na ausência do
  SDK
- `docker-compose.yml` self-contained (Ollama + Qdrant + API, sem
  depender do stack pessoal `~/ai-stack`)
- Documentação: `ARCHITECTURE.md` preenchido, `TCO_SAP_AI_CORE_VS_SELF_HOSTED.md`,
  tutorial completo da fase

**Gap identificado ao final desta fase (corrigido na Fase 9):** o CI
(`.github/workflows/tests.yml`) só rodava `tests/test_connectors.py` —
os 6 testes novos do LLM Gateway (`test_llm_factory.py`) nunca eram
executados automaticamente, só localmente.

**Critério de saída:** LLM Gateway com 3 provedores testados, 1
conector não-SAP real validado, stack local reproduzível sem ambiente
pessoal.

## Fase 9 — Fechamento das Fases Futuras (multi-vendor completo, GraphRAG, A2A)

**Objetivo:** fechar, com código real e testado (ou com estrutura real
pronta para ativar, quando o "real de verdade" dependia de infra
externa indisponível neste ambiente), tudo que estava documentado como
"reservado para uso futuro" ou "arquivado" em fases anteriores — em
vez de deixar esses itens como débito técnico permanente.

**Atividades realizadas:**
1. **Multi-vendor completo:** `SalesforceConnector`, `WorkdayConnector`,
   `AribaConnector` implementados no mesmo padrão do `ServiceNowConnector`
   (OAuth2 client credentials + REST real, mock só sem configuração) —
   fecha os 4 cenários de referência do posicionamento do produto.
   `ODataConnector` ganhou o mesmo esqueleto `use_real` que o
   `RFCConnector` já tinha, fechando uma assimetria entre os dois
   conectores SAP.
2. **GraphRAG (Neo4j) deixou de ser só campo de configuração:**
   `app/rag/graph_store.py` implementa escrita/consulta real do grafo
   de incidentes, ligado por uma única flag (`GRAPH_RAG_ENABLED`) e
   testado com uma sessão Neo4j fake — desligado por default (decisão
   de negócio inalterada), mas com estrutura real, não mais um
   parágrafo de intenção.
3. **Camada A2A implementada:** `app/a2a/` (Agent Card, task manager,
   servidor JSON-RPC 2.0), satisfazendo os pré-requisitos que mantinham
   a proposta arquivada. A ressalva sobre a GA inbound do Joule
   (Q4/2026) foi preservada explicitamente, não removida.
4. **Gap de CI corrigido:** `.github/workflows/tests.yml` passou a
   rodar `pytest tests/ -m "not integration"` (toda a suíte
   não-integração) em vez de só um arquivo — fechando o gap
   identificado ao final da Fase 8.

**Escopo deliberadamente fora desta fase:** a lista de ferramentas de
sustentação de infraestrutura em
`docs/ferramentas-sustentacao-ecossistema.md` (Ansible, Terraform,
Prometheus/Grafana, Loki, Dependabot, mypy, ADRs formais, etc.) não é
"fase futura" da arquitetura da *solução* — é uma lista de
oportunidades de tooling de *operação pessoal*, priorizada à parte
naquele documento. Implementar tudo ali junto com esta fase seria
exatamente o tipo de dispersão de escopo que este projeto já decidiu
evitar (ver instrução do autor citada informalmente nas decisões
recentes: foco no que gera prova de capacidade real, não em
completude por completude).

**Critério de saída:** 47 testes não-integração passando (22 de
conectores, incluindo os 3 novos + OData real; 9 de GraphRAG; 9 de
A2A; 6 de LLM Gateway; 1 de health check), `ruff check` limpo, CI
reproduzido localmente do zero (`rm -rf .venv && uv sync --extra openai`)
antes de cada commit.

## Nota de atualização — Fase 6 (continuação)

Uma rodada de **code review externo** (10 pontos levantados, 6
confirmados e corrigidos, 2 já resolvidos em rodada anterior, 1 falso
alarme, 1 imprecisão factual corrigida) foi tratada como parte
contínua da Fase 6 — reforça a prática de auditar criticamente
qualquer sugestão (própria ou externa) antes de aplicar, em vez de
aceitar ou rejeitar por autoridade da fonte. Ver seção 9 das
"Decisões de Arquitetura" no README para o detalhamento completo.

## Fase 10 — Validação Real Multi-Vendor + Sétimo Conector (CAP)

1. **`CAPConnector` implementado** (OData v4 + autenticação XSUAA via
   Client Credentials, Basic Auth no token endpoint) — sétimo conector
   do projeto, seguindo exatamente o mesmo padrão dos demais
   (config ausente = mock, config presente = chamada real).
2. **Quatro conectores validados ponta-a-ponta contra sistema real** (Salesforce, ServiceNow, CAP, RFC via ABAP Cloud Trial A4H) — nao
   só mock:** `SalesforceConnector` (Developer Edition gratuita),
   `ServiceNowConnector` (Personal Developer Instance gratuita), e
   `CAPConnector` (serviço SAP CAP real, deployado num BTP Trial
   single-tenant simplificado — HANA Cloud e XSUAA reais, não
   simulados). Cada validação documentada em `ARCHITECTURE.md` com o
   resultado real obtido, não só "testado".
3. **Dois bugs de isolamento de teste corrigidos:** os testes
   `*_demo_mode_*` de 5 conectores (ServiceNow×2, Salesforce, Workday,
   Ariba) e 2 do CAP dependiam implicitamente do `.env` local estar
   vazio — quebraram silenciosamente assim que a primeira credencial
   real (Salesforce) foi configurada. Corrigido com `monkeypatch`
   explícito forçando o campo de gating vazio, independente do `.env`
   real.
4. **Correção factual sobre RFC/`pyrfc`:** a própria SAP arquivou o
   `PyRFC` (fim de manutenção anunciado jul/2024, repositório arquivado
   maio/2026) — o caminho antigo ("esperar o SDK licenciado") já não é
   mais válido como estava documentado. Existe uma alternativa
   SDK-free (`open-rfc`), mas exclusiva de Node.js, sem equivalente
   Python. Achado adicional, mais importante para o posicionamento do
   produto: o bloqueio de acesso ao SDK é **pessoal ao autor** (sem
   S-user vinculado a contrato SAP) — um cliente real com licença SAP
   ativa baixa o mesmo SDK sem custo adicional, como parte da licença
   que já paga. O bloqueio documentado nunca foi comercial para o
   cliente-alvo do produto.
5. **Diagrama do README corrigido:** mostrava `RAG`, `GraphRAG` e
   `Conectores` como três ramos paralelos convergindo para um "LLM
   Gateway" final — o fluxo real é sequencial
   (`connector → retrieve → [graph_enrich] → diagnose → [graph_write] → report`),
   com o LLM Gateway invocado de dentro do node `diagnose`, não uma
   etapa própria depois de tudo convergir.
6. **Assimetria SuccessFactors/Workday documentada explicitamente:** o
   cenário de referência "SuccessFactors↔Workday" é representado hoje
   só pelo lado Workday — não havia `SuccessFactorsConnector`
   implementado, e isso nunca tinha sido registrado como decisão
   consciente. Corrigido em `ARCHITECTURE.md`, com o motivo (SFAPI usa
   SAML bearer assertion, mais complexo que o padrão client_credentials
   já usado) e status de backlog explícito.

**Escopo deliberadamente fora desta fase:** `APIManagementConnector`
(sinal de infraestrutura de API via Analytics do Integration Suite)
continua só documentado como próximo passo, não implementado — mesma
disciplina de "um conector por vez, validado, antes do próximo"
mantida desde a Fase 9. Validação real de ServiceNow/Workday/Ariba
adicionais e RFC continuam bloqueadas por falta de sandbox
gratuito/SDK acessível, não por falta de esforço.

**Critério de saída:** 65 testes não-integração passando, 3 de 7
conectores com execução real comprovada (não só mockada) documentada,
achado factual sobre `pyrfc` registrado com fonte verificada em
`app/connectors/rfc_connector.py`.

## Fase 11 — Desbloqueio Real do RFC + ABAP Cloud Developer Trial

1. **SAP NetWeaver RFC SDK 7.50 PL19 obtido e instalado** em
   `/usr/local/sap/nwrfcsdk/` via S-user com contrato SAP ativo —
   confirmando que o bloqueio documentado na Fase 10 era **pessoal**
   (ausência de S-user/contrato no portfolio individual), não técnico.
   Clientes reais com licença SAP usam o SDK sem custo adicional.

2. **`pyrfc` 3.3.1 instalado e validado contra Python 3.12** — o
   pacote foi retirado do índice padrão do PyPI (yanked), mas é
   instalável via versão específica (`uv pip install "pyrfc==3.3.1"`).
   Compilou contra o SDK real sem erros.

3. **ABAP Cloud Developer Trial 2025 rodando via Docker** —
   imagem oficial da SAP (`sapse/abap-cloud-developer-trial:2025`,
   22GB comprimida), sistema A4H, release 754, HANA 2.0. Scripts de
   start/stop criados em `~/start-abap-trial.sh` e
   `~/stop-abap-trial.sh`. Primeira inicialização: ~30-45 minutos.

4. **`RFCConnector(use_real=True)` validado ponta-a-ponta contra
   sistema ABAP real** — `RFC_SYSTEM_INFO` chamado com sucesso
   (`sysId=A4H`, `saprl=754`). `BAPI_IDOC_STATUS` indisponível no
   Trial (ABAP Cloud nao inclui BAPIs clássicas de IDoc por padrão);
   para validar a BAPI específica, criar função Z no Trial ou usar
   landscape de cliente real.

5. **`APIManagementConnector` implementado** (oitavo conector) com
   schema marcado explicitamente como ESPECULATIVO — endpoint e
   formato de resposta inferidos por analogia, não confirmados contra
   documentação oficial do SAP API Management. Validação real
   pendente.

6. **Correção de colisão de retrieval RAG** — adição do documento
   `cap_custom_purchase_approval_failure.md` à base de conhecimento
   causou regressão em dois testes (`odata_timeout_cpi.md` deixou de
   ser top-1 para a query "iFlow travando ao consumir OData sem
   retorno"). Corrigido reescrevendo o trecho de resolução do documento
   CAP para usar vocabulário mais específico de domínio CDS/CAP, sem
   termos genéricos que competiam com o documento correto.

**Critério de saída:** 68 testes passando (65 não-integração + 3 do
`APIManagementConnector`), `RFCConnector` com conexão real validada
contra ABAP Cloud Trial, quatro conectores com execução ponta-a-ponta
comprovada (Salesforce, ServiceNow, CAP, RFC).
