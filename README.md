# Integration Incident Copilot

[![tests](https://github.com/marcos-slima/integration-incident-copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/marcos-slima/integration-incident-copilot/actions/workflows/tests.yml)

> 📋 Veja o [processo de desenvolvimento](docs/PROCESSO_DESENVOLVIMENTO.md) seguido neste projeto, fase por fase.

Assistente de IA para diagnóstico de incidentes de integrações.
Recebe a descrição de um incidente, lê logs/payloads, consulta um
catálogo de APIs/documentos via RAG, identifica o provável ponto de
falha, sugere causa raiz e próximos passos, e gera um relatório em
Markdown.

**Por que este projeto existe:** o SAP AI Core exige HANA Cloud como
camada obrigatória (dezenas de milhares de euros/ano, independente do
consumo de IA), o que exclui estruturalmente quem ainda está em ECC
on-premise ou não tem orçamento/infra para BTP — cerca de 40-45% da
base de clientes SAP ECC no mundo, segundo Gartner/IDC. Este projeto é
a prova técnica de que dá para levar IA de diagnóstico real (RAG +
agente + conectores) para esse público, rodando local ou sobre um
provedor que o cliente já tenha — ver
[TCO_SAP_AI_CORE_VS_SELF_HOSTED.md](docs/TCO_SAP_AI_CORE_VS_SELF_HOSTED.md).
E não fica restrito a SAP: o mesmo contrato de conector (`app/connectors/`)
já cobre cinco sistemas de referência não-SAP/multi-vendor de verdade
(ServiceNow, Salesforce, Workday, SAP Ariba), não só mock — ver seção
"Conectores" em [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Arquitetura

```
Frontend/API client            Agente externo (A2A)
      │                               │
      ▼                               ▼
   FastAPI /diagnose          app/a2a/ (Agent Card + JSON-RPC)
      │                               │
      └───────────────┬───────────────┘
                       ▼
        Orquestração via LangGraph (app/agent/graph.py)
                       │
                       ▼
             connector (SAP + multi-vendor: OData/RFC/
          ServiceNow/Salesforce/Workday/Ariba — reais
                quando configurados, mock por default)
                       │
                       ▼
          retrieve (RAG híbrido dense+sparse BM25,
                Qdrant, fusão RRF, score_threshold)
                       │
                       ▼
        [graph_enrich] (GraphRAG opt-in, Neo4j,
                 desligado por default)
                       │
                       ▼
        diagnose (LLM Gateway — Ollama/OpenAI/
        Azure OpenAI — + guardrails determinísticos)
                       │
                       ▼
        [graph_write] (GraphRAG opt-in, Neo4j)
                       │
                       ▼
                    report
                       │
                       ▼
           Resposta + Relatório Markdown
```

Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para o detalhamento
por camada (API / A2A / orquestração / LLM Gateway / RAG+GraphRAG /
conectores).

## Stack

- **API**: FastAPI + Pydantic
- **A2A**: Agent Card + servidor JSON-RPC 2.0 (`app/a2a/`), em paralelo
  ao REST, mesma orquestração por trás — ver
  [proposta original](docs/proposals/a2a-interoperability-layer.md)
- **Orquestração**: LangGraph
- **LLM Gateway**: plugável — Ollama (default, local-first), OpenAI ou
  Azure OpenAI (`app/llm/factory.py`), sem trocar código do grafo
- **RAG**: LangChain + Qdrant (vector store) + GraphRAG opt-in via Neo4j
  (`app/rag/graph_store.py`, desligado por default)
- **Observabilidade**: Langfuse (opcional; tracing de todo o fluxo do
  agente quando configurado)
- **Conectores**: OData / RFC / ServiceNow / Salesforce / Workday / SAP
  Ariba — todos reais (chamada HTTP/OAuth2 de verdade) quando
  configurados, caem em mock só sem credencial/endpoint informado

## Desenvolvimento local

Opção 1 — self-contained, sem depender de infraestrutura pessoal
(recomendado para rodar/demonstrar este repositório isoladamente):

```bash
docker compose up -d      # sobe Ollama + Qdrant + a API
docker compose exec ollama ollama pull qwen2.5-coder:32b
docker compose exec ollama ollama pull nomic-embed-text
```

Opção 2 — ambiente de desenvolvimento local (fora de container):

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Pré-requisitos da Opção 2: Qdrant e Ollama acessíveis (localmente ou
via `~/ai-stack`, que também traz Neo4j reservado para uso futuro e o
stack completo do Langfuse — ver nota em
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).

## Status

Projeto em desenvolvimento — portfólio da trilha SAP Architect → AI
Architect.

## Decisões de Arquitetura

Registro dos problemas reais encontrados durante o desenvolvimento e
como foram resolvidos — processo de engenharia, não só o resultado
final.

### 1. Alucinação por mistura de contexto

**Problema:** ao passar os 3 documentos mais relevantes (RAG top-3)
inteiros no prompt, o LLM ocasionalmente combinava causa raiz de
documentos diferentes (ex: misturava conceitos de IDoc e OData numa
única resposta), mesmo com instrução explícita para não fazer isso.

**Solução:** restringir o contexto passado ao LLM a apenas o
**documento mais relevante** (texto completo), citando os demais só
pelo nome, sem conteúdo. Eliminou a possibilidade de mistura na raiz,
por design, em vez de depender de instrução de prompt.

### 2. Não-determinismo com temperature=0

**Problema:** o mesmo prompt, rodado duas vezes com `temperature=0.0`
no Ollama, produzia respostas diferentes — incluindo uma alucinação
completa numa das execuções. `temperature=0` não garante determinismo
total sem um `seed` explícito.

**Solução:** fixar `seed=42` na chamada ao `ChatOllama`. Validado com
5 execuções idênticas seguidas do mesmo cenário antes considerado
instável.

### 3. Guardrail determinístico para dados de fallback

**Problema:** quando um conector SAP não reconhece um identificador
(cenário simulado/mock não mapeado), o LLM às vezes ainda tentava
vincular a um documento específico da base de conhecimento com
confiança moderada-alta, mesmo orientado por prompt a não fazer isso.

**Solução:** não depender só da autoavaliação do LLM para essa
propriedade de segurança. O código verifica deterministicamente se o
conector retornou um dado de fallback (`ConnectorResult.is_fallback`)
e, nesse caso, **impõe um teto de confiança (0.4)** independente do
que o modelo reportar.

### 4. Comparação formal de modelos (qwen3:30b-a3b vs qwen2.5-coder:32b)

**Contexto:** os problemas 1 e 3 acima ocorreram especificamente com
o `qwen3:30b-a3b` (MoE, ~3B parâmetros ativos). Antes de assumir que
o modelo era a causa raiz, foi feita uma comparação formal usando
[promptfoo](https://www.promptfoo.dev/), rodando o **pipeline
completo real** (conector + RAG + guardrails) contra os dois modelos,
não o LLM isolado.

**Resultado:** nos casos com correspondência clara, os dois modelos
tiveram desempenho equivalente. No caso crítico — identificador de
sistema desconhecido, sem correspondência real na base de
conhecimento — o `qwen2.5-coder:32b` reconheceu sozinho a ausência de
correspondência (`matched_source: null`), enquanto o `qwen3:30b-a3b`
tentou vincular um documento específico mesmo assim (só não virou
problema visível por causa do guardrail do item 3).

**Decisão:** `qwen2.5-coder:32b` (denso, 32B parâmetros) adotado como
modelo de produção do grafo. Validado com a suíte completa de testes
(16/16 `pytest`) após a troca. Trade-off aceito: tempo de inferência
maior (~2min49s vs ~1min20s nos 16 testes) em troca de comportamento
mais confiável sob incerteza.

### 5. Observabilidade real com Langfuse

**Contexto:** o Langfuse estava configurado desde o início do
projeto, mas sem nenhum código realmente enviando dados para lá —
configuração presente, tracing ausente.

**Implementado:** cada node do grafo (`connector`, `retrieve`,
`diagnose`, `report`) é instrumentado com `@observe`, e a chamada ao
LLM usa o `CallbackHandler` do LangChain — capturando tempo de
execução, tokens e o payload completo de entrada/saída de cada etapa,
visível em `http://localhost:3000`.

**Bug encontrado e corrigido no processo:** em execuções via `pytest`
(diferente do CLI), o SDK não fazia `flush()` automático antes do
processo terminar — de 16 execuções de teste, só 8 traces chegavam ao
Langfuse. Corrigido com uma fixture `autouse` no `conftest.py` que
força o flush ao final da sessão de testes.

### 6. Configuração centralizada (eliminando hardcoded)

**Problema encontrado:** apesar de existir um `.env` desde o início
do projeto, o código nunca o lia — URLs do Qdrant, modelo do LLM e
outras configurações estavam fixas como constantes Python, espalhadas
em múltiplos arquivos. Trocar de modelo exigia editar código-fonte
(`sed` direto no arquivo), não mudar uma variável de ambiente.

**Solução:** `app/config.py`, uma classe `Settings` (via
`pydantic-settings`) como única fonte de verdade, lida do `.env`. Um
comando (`uv run python -m app.config`) imprime a configuração
efetiva a qualquer momento, com segredos mascarados — permite
verificar o que está realmente configurado sem depender de leitura de
código-fonte.

### 7. Segurança e CI antes da publicação

Antes de tornar o repositório público:

- **`gitleaks`**: varredura de **todo o histórico do git** (não só o
  estado atual) em busca de segredos vazados — confirmado limpo antes
  do primeiro push
- **`pre-commit`**: hooks automáticos (lint/format via `ruff`,
  detecção de segredo, bloqueio de arquivo grande >5MB) rodando em
  todo commit local, dali em diante
- **GitHub Actions**: workflow de CI rodando lint + testes unitários
  a cada push/PR — o badge de status no topo deste README reflete o
  resultado real da última execução, não uma alegação

### 8. Segunda comparação de modelo: qwen3.6:35b-a3b avaliado e rejeitado

**Contexto:** meses após a decisão pelo `qwen2.5-coder:32b` (seção 4),
a Alibaba lançou o `qwen3.6:35b-a3b` (MoE, 36B total/3B ativos,
sucessor da série que havia sido descartada na primeira comparação).
Repetiu-se o mesmo processo formal via `promptfoo`, contra o mesmo
pipeline real e os mesmos 10 casos de teste — incluindo o caso crítico
(`IDoc travado` / conector RFC) repetido 3 vezes para medir
estabilidade.

**Resultado:** em 7 dos 10 casos, desempenho equivalente ou
ligeiramente superior ao modelo atual (respostas mais detalhadas,
confiança bem calibrada no caso de segurança do identificador
desconhecido). Porém, no caso crítico repetido 3 vezes, o
`qwen3.6:35b-a3b` **falhou nas 3 execuções de forma idêntica**: o
modelo não devolveu um JSON estruturado válido
(`"Nao foi possivel estruturar a resposta do modelo"`,
`confidence: 0.0`), enquanto o `qwen2.5-coder:32b` acertou as 3 vezes
com 90% de confiança.

**Decisão:** manter `qwen2.5-coder:32b` em produção. Uma falha
determinística e reproduzível (3/3) no cenário mais crítico do
pipeline desqualifica o candidato, independente do desempenho médio
nos demais casos — confiabilidade sob o caso mais exigente pesa mais
que desempenho médio.

**Valor do processo, não só do resultado:** esta comparação também
prova que a decisão de modelo não é estática — é revisitada com
critério formal sempre que surge um candidato relevante, com a mesma
metodologia e o mesmo pipeline real usados desde a primeira vez,
gerando decisões comparáveis ao longo do tempo.

### 9. Achados de code review: estado global, parsing frágil, limites ausentes

Uma revisão de código externa identificou 10 pontos; a triagem separou
o que era real do que era falso alarme ou já havia sido corrigido:

- **Falso alarme:** alegação de que `report_node`/`run_diagnosis`
  estariam ausentes do arquivo — não procede, ambos existem e
  funcionam (o revisor provavelmente viu um trecho cortado, não o
  arquivo completo)
- **Já corrigido antes da revisão:** singleton no retriever e
  `ensure_collection` fora do loop de batch (ver seções anteriores)
- **Confirmados e corrigidos nesta rodada:**
  - `LLM_MODEL` como global mutável de módulo → injetado via `state`/
    parâmetro em `run_diagnosis(..., llm_model=...)`, eliminando risco
    de corrida entre execuções concorrentes
  - Parsing de JSON manual e frágil → `llm.with_structured_output(DiagnosisModel, include_raw=True)`, com o parsing manual antigo mantido como *fallback*, não mais como único caminho
  - `confidence` sem validação de range → `Field(ge=0.0, le=1.0)` no
    schema Pydantic **+** clamp defensivo no código (a mesma filosofia
    de guardrail em camadas já usada para o fallback do conector,
    agora estendida)
  - `logs`/`payload` sem limite de tamanho → `max_length` no Pydantic
    (rejeita entrada absurda na API) e truncamento mais apertado na
    montagem do prompt (protege o contexto/custo do LLM)
  - Zero teste da camada HTTP → `tests/test_api.py` com `TestClient`
  - `Dockerfile` não copiava `data/`, então o fallback de documentos
    de exemplo quebraria em produção → corrigido, com nota explícita
    de que a biblioteca de 36GB nunca deve entrar na imagem e que
    `.env` deve ser injetado em runtime, não commitado na imagem
  - `@app.on_event` (deprecated, ainda funcional mas legado) →
    migrado para o padrão `lifespan` do FastAPI
- **Achado adicional durante a correção do item acima:** a primeira
  tentativa de restaurar a orientação sobre `matched_source` usou
  `Field(description=...)` no schema Pydantic, assumindo que o
  LangChain injetaria essa descrição como contexto textual pro LLM.
  **Isso não teve efeito nenhum** — confirmado porque as respostas do
  modelo saíram byte-a-byte idênticas antes e depois da mudança
  (esperado com `temperature=0`/`seed` fixo apenas se o prompt
  realmente enviado não mudou). Causa real: `with_structured_output`
  no Ollama usa o schema JSON para restringir **tipo/formato** da
  geração (decodificação restrita por gramática), não para injetar
  descrições como instrução legível pelo modelo. A correção que
  funcionou de fato foi devolver a instrução como **texto explícito
  no prompt**, confirmada visualmente via `--debug` antes de rodar a
  suíte completa de novo. Lição: ao adotar saída estruturada via
  schema, texto explícito no prompt continua necessário para lógica
  de preenchimento — o schema garante a forma, não o conteúdo.

### 10. LLM Gateway plugável (não hardcoded em Ollama)

**Contexto:** o projeto nasceu 100% Ollama/local por decisão
deliberada (custo zero de API para prototipar). O posicionamento do
produto evoluiu para viabilizar IA em clientes que não conseguem
adotar o SAP AI Core — o que não significa que todo cliente rodará
100% local: alguns já têm OpenAI/Azure OpenAI contratado, ou querem
mais capacidade do que o hardware local aguenta para um caso
específico. `diagnose_node` instanciava `ChatOllama` diretamente,
então trocar de provedor exigiria editar o grafo.

**Decisão:** extrair a escolha do provedor para `app/llm/factory.py`
(`get_chat_model()`), selecionado via `Settings.llm_provider`
(ollama/openai/azure_openai). Deliberadamente **não** foi criada uma
interface própria (tipo um `LLMProvider.generate()` do zero) — o
factory devolve direto um `BaseChatModel` do LangChain, já que todo o
resto do grafo (`with_structured_output`, callbacks do Langfuse) já
depende do contrato do LangChain. Reaproveitar o polimorfismo que a
lib já oferece é menos código e menos superfície de bug do que
reimplementar o mesmo contrato — uma escolha de "reuso vs.
reinvenção", não só "adicionar abstração".

**Validação:** falha alto e claro (`ConfigurationError`), nunca
silenciosa, quando o provedor escolhido não tem a configuração
necessária (ex: `openai` sem `OPENAI_API_KEY`) — mesma filosofia dos
guardrails determinísticos das seções 1 e 3.

### 11. Conector real para sistema não-SAP (ServiceNow) e caminho RFC honesto

**Contexto:** até aqui, os conectores (`ODataConnector`,
`RFCConnector`) eram mocks assumidos como tal — corretos para
prototipagem, mas insuficientes para provar a promessa de "integração
SAP + não-SAP" que o posicionamento atual do produto assume.

**Decisão:** `ServiceNowConnector` faz chamada HTTP real contra a
Table API do ServiceNow (`GET /api/now/table/incident`) quando
`SERVICENOW_INSTANCE_URL` está configurado, caindo em modo demo/mock
apenas na ausência dessa configuração — mesmo princípio dos conectores
SAP mock (funcionar sem depender de credencial de cliente real), não
uma limitação técnica. Testado via `httpx.MockTransport`, exercitando
o código HTTP de verdade (parâmetros de query, autenticação, parsing
de resposta, tratamento de erro de rede) sem precisar de uma instância
ServiceNow real.

Em paralelo, `RFCConnector` ganhou um modo `use_real=True` com
detecção de feature do `pyrfc` (SAP NetWeaver RFC SDK — binário da
SAP, fora do PyPI): sem o SDK instalado, pedir `use_real=True` falha
com `ConfigurationError` explicando exatamente o que falta, em vez de
cair silenciosamente no mock. RFC (não só OData) é o caminho mais
relevante para o público-alvo do projeto: clientes ainda em ECC
on-premise tipicamente só têm RFC/BAPI como via de automação.

**Por que isso importa para o posicionamento:** prova com código —
não só com docstring de intenção — que o "e outras plataformas" da
proposta de valor do projeto é real: existe pelo menos um sistema
não-SAP com integração de fato funcional, ao lado de um caminho SAP
(RFC) claramente desenhado para o cliente mais restrito (ECC
on-premise), que é justamente quem não consegue pagar SAP AI Core.

### 12. Fechando os conectores multi-vendor (Salesforce, Workday, SAP Ariba) e o caminho real do OData

**Contexto:** a seção anterior fechou 1 dos 4 cenários de referência
multi-vendor do posicionamento do produto (ServiceNow), escolhido
primeiro por ter a API pública mais simples de implementar de verdade
— não por prioridade de negócio. Isso deixava uma dívida técnica
explícita: Salesforce, Workday e SAP Ariba continuavam mock puro, e o
`ODataConnector` não tinha nem o esqueleto `use_real` que o `RFCConnector`
já tinha ganhado.

**Decisão:** os três conectores restantes (`SalesforceConnector`,
`WorkdayConnector`, `AribaConnector`) foram implementados seguindo
**exatamente** o mesmo critério do `ServiceNowConnector` — OAuth2 (client
credentials em todos os três casos) contra o token endpoint documentado
de cada fornecedor, seguido da chamada REST real; ausência de
configuração cai em mock, presença ativa o caminho real, sem mudar
nenhum outro arquivo do projeto. `ODataConnector` ganhou o mesmo padrão
`use_real`/`ConfigurationError` que o `RFCConnector` já tinha, fechando
a assimetria entre os dois conectores SAP mock.

**Validação:** cada conector tem teste via `httpx.MockTransport`
simulando as duas chamadas (token OAuth2 + recurso), provando que o
código de produção (montagem do request, header `Authorization: Bearer`,
parsing da resposta, tratamento de erro HTTP/rede) funciona de verdade
— sem, para nenhum dos três, uma conta/sandbox real disponível para
validar contra produção (mesma ressalva já feita para
`RFCConnector._fetch_real` desde a Fase 8, agora consistente em todo o
projeto, não uma exceção isolada).

**O que isso NÃO é:** uma alegação de que os 4 cenários de referência
(SuccessFactors↔Workday, Salesforce↔SAP, SAP Ariba↔S/4HANA,
ServiceNow↔SAP) estão "prontos para produção" — estão prontos para
**demonstração técnica com credenciais reais em 10 minutos** (trocar
`.env`, sem tocar código), o que é uma barra bem mais alta que "mock
bonito", mas ainda abaixo de "testado contra um cliente real".

### 13. GraphRAG (Neo4j) deixa de ser só campo de configuração

**Contexto:** desde a Fase 4, `Settings` tinha campos para Neo4j e a
documentação dizia explicitamente "reservado para uso futuro, nenhum
código usa isso hoje" — um campo de configuração sem nenhuma
implementação por trás, o tipo exato de coisa que este projeto
criticou no `genai-engineering-template` (documentação descrevendo
funcionalidade que o código não entrega).

**Decisão:** implementar o código real (`app/rag/graph_store.py`) —
grava cada diagnóstico no Neo4j como grafo relacional
(Incident/Interface/System/Document) e consulta esse grafo por
histórico de incidentes na mesma interface antes de gerar um novo
diagnóstico — mas manter **desligado por default**
(`GRAPH_RAG_ENABLED=false`). A decisão de negócio de não priorizar
GraphRAG não mudou (Qdrant resolve o caso de uso principal; grafo só
compensa com meses de histórico real acumulado); o que mudou é que
agora existe uma estrutura real e testada para ligar quando fizer
sentido, em vez de só um parágrafo de intenção.

**Validação:** `tests/test_graph_store.py` usa uma sessão Neo4j FAKE
(implementa só `.run()`, mesmo espírito do `httpx.MockTransport`) para
provar que as queries Cypher corretas são disparadas e os dados voltam
mapeados certo. Também validado que `build_graph()` produz o MESMO
grafo LangGraph de antes desta fase quando a flag está desligada
(nenhum node novo é adicionado) — mudança de comportamento zero no
caminho default.

**Honestidade mantida:** não testado contra um Neo4j real (sem Docker
daemon disponível no ambiente onde isso foi construído) — mesma
ressalva já aplicada ao `RFCConnector._fetch_real`.

### 14. Camada A2A (Agent2Agent) implementada, com a ressalva de GA preservada

**Contexto:** a proposta em
[docs/proposals/a2a-interoperability-layer.md](docs/proposals/a2a-interoperability-layer.md)
estava arquivada desde antes da Fase 8, com dois pré-requisitos
explícitos para sair do papel: conectores SAP fechados e suíte de
testes automatizada madura. As Fases 7/8 (e a seção 12 acima)
satisfazem os dois.

**Decisão:** implementar o subconjunto do protocolo A2A necessário
para o critério de aceite original — Agent Card (`GET
/.well-known/agent-card.json`), task manager e servidor JSON-RPC 2.0
(`POST /a2a`, métodos `message/send` e `tasks/get`) — em `app/a2a/`,
sem depender de nenhum SDK externo de A2A (a proposta original já
citava a imaturidade dessas SDKs como risco a validar antes de
começar). O task manager chama a MESMA função (`run_diagnosis`) que o
`/diagnose` REST — zero lógica de diagnóstico duplicada entre os dois
protocolos.

**Simplificação deliberada:** dos 8 estados de task do protocolo A2A,
só os 4 alcançáveis por um agente síncrono e autocontido como este
foram implementados (`submitted -> working -> completed|failed`).
Autenticação é uma chave estática opcional via header, não OAuth2/JWT
— documentado como gap de produção, não escondido.

**Validação:** `tests/test_a2a.py` prova o critério de aceite original
mecanicamente — o endpoint A2A produz o mesmo relatório que o
`/diagnose` para a mesma entrada (via injeção de dependência do
`diagnosis_fn` no `TaskManager`, sem precisar de um LLM real no ar para
o teste), e uma falha na orquestração vira task `failed` (erro de
negócio), não um HTTP 500 (erro de transporte) — a diferença que
importa para um agente externo saber se deve tentar de novo ou não.

**Ressalva que NÃO muda com esta implementação:** o suporte A2A do
Joule continua unidirecional (outbound) hoje — o Agent Gateway que
habilitaria o Joule a chamar este Copilot como par (inbound) está
pré-GA, previsto para Q4/2026. Este endpoint é compatível com o
protocolo aberto A2A (padrão vendor-neutral, Linux Foundation), não uma
integração já consumível pelo Joule.
