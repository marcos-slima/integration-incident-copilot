# SAP Integration Copilot

![tests](https://github.com/marcos-slima/sap-integration-copilot/actions/workflows/tests.yml/badge.svg)

> 📋 Veja o [processo de desenvolvimento](docs/PROCESSO_DESENVOLVIMENTO.md) seguido neste projeto, fase por fase.

Assistente de IA para diagnóstico de incidentes de integração SAP.
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
já cobre um sistema não-SAP (ServiceNow) de verdade, não só mock.

## Arquitetura

```
Frontend/API client
      │
      ▼
   FastAPI
      │
      ▼
Orquestração via LangGraph
      │
   ┌──┴────────────────────────────┐
   ▼                                ▼
RAG Retriever              Conectores SAP + não-SAP
(PDF/MD/CSV)             (OData/RFC mock, ServiceNow real)
   │                                │
   └──────────────┬─────────────────┘
                   ▼
         LLM Gateway (Ollama / OpenAI / Azure OpenAI)
                   │
                   ▼
      Resposta + Relatório Markdown
```

Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para o detalhamento
por camada (API / orquestração / LLM Gateway / RAG / conectores).

## Stack

- **API**: FastAPI + Pydantic
- **Orquestração**: LangGraph
- **LLM Gateway**: plugável — Ollama (default, local-first), OpenAI ou
  Azure OpenAI (`app/llm/factory.py`), sem trocar código do grafo
- **RAG**: LangChain + Qdrant (vector store)
- **Observabilidade**: Langfuse (opcional; tracing de todo o fluxo do
  agente quando configurado)
- **Conectores**: OData / RFC (SAP, mock) + ServiceNow (chamada HTTP
  real via Table API, cai em mock só sem instância configurada)

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
