# Tutorial: SAP Integration Copilot — Da Requisição ao Relatório

> Público-alvo: quem já domina arquitetura SAP e conceitos de
> integração, mas está consolidando Python/FastAPI/LangGraph. Use as
> analogias com ABAP como ponte, não como substituto de entender o
> código Python real.
>
> Pré-requisito: stack local no ar (`~/ai-stack` via `docker compose
> up -d`, Ollama ativo) e o projeto aberto no VS Code com o
> `.vscode/launch.json` já configurado (ver Fase 4 do
> `docs/PROCESSO_DESENVOLVIMENTO.md`).

> **Nota de atualização:** este tutorial foi escrito quando o projeto
> tinha só 2 conectores (OData/RFC, ambos mock) e 4 nodes no grafo. Hoje
> sao 8 conectores (a maioria real quando configurado) e o grafo pode
> ter ate 6 nodes com GraphRAG habilitado
> (`connector → retrieve → [graph_enrich] → diagnose → [graph_write] → report`).
> O roteiro de debug abaixo continua correto para o caso RFC guiado na
> Seção 5, mas nao cobre os nodes/conectores novos — ver
> `docs/ARCHITECTURE.md` para o estado completo e atual.

---

## 1. Mapa Geral da Solução

### Fluxo ponta a ponta

```
Cliente HTTP (curl / HTTPie / Bruno)
        │  POST /diagnose { description, interface_type?, identifier? }
        ▼
┌─────────────────────────────────────────────────────────┐
│ app/main.py                                              │
│   FastAPI valida o corpo da requisição contra             │
│   IncidentRequest (Pydantic) ANTES de qualquer código      │
│   seu rodar — se faltar campo obrigatório, nem chega       │
│   na sua função.                                          │
└─────────────────────┬───────────────────────────────────┘
                       │ run_diagnosis(request)
                       ▼
┌─────────────────────────────────────────────────────────┐
│ app/agent/graph.py                                        │
│   run_diagnosis() monta o estado inicial (CopilotState)    │
│   e chama o grafo compilado LangGraph.                     │
└─────────────────────┬───────────────────────────────────┘
                       ▼
        ┌──────────────────────────────┐
        │  StateGraph (LangGraph)       │   ← máquina de estados,
        │                                │      não um loop comum
        │  connector → retrieve →        │
        │  diagnose → report             │
        └──────────────────────────────┘
                       │
   ┌───────────────────┼────────────────────┬─────────────────────┐
   ▼                   ▼                    ▼                     ▼
connector_node   retrieve_node        diagnose_node          report_node
app/connectors/  app/rag/retriever.py  app/agent/graph.py     app/agent/graph.py
   │                   │                    │
   ▼                   ▼                    ▼
SAPConnector      Qdrant (via          ChatOllama
(mock hoje,       qdrant-client)       (langchain_ollama)
OData/RFC)        + embeddings              │
                  (nomic-embed-text          ▼
                  via Ollama)          Ollama runtime local
                                       (qwen2.5-coder:32b)
                       │                    │
                       └────────┬───────────┘
                                ▼
                     DiagnosisResponse (Pydantic)
                                │
                                ▼
                  Cliente recebe JSON + report_markdown
```

### Onde cada etapa vive (arquivo real)

| Etapa | Arquivo | O que faz |
|---|---|---|
| Entrada HTTP + validação | `app/main.py` | Define `POST /diagnose`, delega pro grafo |
| Contratos de dados | `app/models.py` | `IncidentRequest` (entrada), `DiagnosisResponse` (saída) |
| Configuração central | `app/config.py` | Única fonte de verdade — URLs, modelo, credenciais, lida do `.env` |
| Orquestração (o "workflow") | `app/agent/graph.py` | Define os 4 nodes e as arestas entre eles |
| Busca de dados no sistema SAP + multi-vendor | `app/connectors/` | `base.py` (contrato comum), 8 conectores (OData, RFC, ServiceNow, Salesforce, Workday, Ariba, CAP, APIManagement) - maioria real quando configurado |
| Busca de conhecimento (RAG) | `app/rag/ingest.py`, `app/rag/retriever.py` | Indexação e consulta no Qdrant |
| Testes | `tests/` | Regressão automatizada de tudo acima |

**Gap honesto:** `app/services/` existe na estrutura do repositório (criada no bootstrap inicial) mas está **vazia até hoje** — nenhuma lógica de negócio foi colocada lá. Não finja que existe algo funcionando ali.

### Analogia ABAP

Pense no `StateGraph` como um **workflow** (tipo BRF+ ou uma cadeia de BAdIs em sequência): cada `node` é um step que recebe uma "área de trabalho" (o `CopilotState`, um `TypedDict`), pode ler e escrever nela, e passa adiante. Não tem `PERFORM`/`CALL FUNCTION` direto de um node pro outro — o LangGraph decide a ordem baseado nas arestas (`add_edge`) que você declarou, parecido com a definição de fluxo de um workflow, não com chamada de sub-rotina imperativa.

---

## 2. Catálogo de Frameworks e Bibliotecas

Só o que o projeto **realmente usa** — não a API inteira de cada lib.

| Biblioteca | O que é | Por que foi escolhida aqui | O que o código chama de fato |
|---|---|---|---|
| **FastAPI** | Framework web assíncrono para APIs | Validação automática via Pydantic, geração de OpenAPI/Swagger de graça, é o padrão de mercado para APIs Python hoje | `FastAPI()`, decorators `@app.get`/`@app.post`, `response_model=` |
| **Pydantic** | Validação de dados via type hints | Já vem embutido no FastAPI; garante que `IncidentRequest` malformado nunca chega no seu código | `BaseModel`, campos com `str \| None`, `Literal["odata", "rfc"]`, `Field(ge=0, le=1)` (validação de confidence), `Field(max_length=...)` (limite de entrada), `with_structured_output` (saída do LLM validada) |
| **pydantic-settings** | Extensão do Pydantic para configuração via `.env`/env vars | Elimina configuração hardcoded (gap real que encontramos e corrigimos) | `BaseSettings`, `SettingsConfigDict(env_file=".env")` |
| **LangGraph** | Orquestração de agentes como máquina de estados (grafo) | Modela o fluxo (`connector→retrieve→diagnose→report`) de forma explícita e visualizável, em vez de um script sequencial disfarçado de "agente" | `StateGraph`, `add_node`, `add_edge`, `set_entry_point`, `compile()`, `.invoke()` |
| **langchain-ollama** | Integração LangChain ↔ Ollama | Dá interface padronizada (`ChatOllama`, `OllamaEmbeddings`) em vez de chamar a API REST do Ollama na mão | `ChatOllama(...).with_structured_output(DiagnosisModel, include_raw=True).invoke(prompt)`, `OllamaEmbeddings(model=...).embed_documents()/.embed_query()` |
| **langchain-text-splitters** | Divisão de texto em chunks | `MarkdownTextSplitter` respeita a estrutura Markdown dos documentos de incidente ao invés de cortar no meio de uma frase | `MarkdownTextSplitter(chunk_size=..., chunk_overlap=...).split_text()` |
| **langchain-community** (`PyPDFLoader`) | Extração de texto de PDF | Usado só na ingestão da biblioteca de referência (livros), não no fluxo de diagnóstico | `PyPDFLoader(path).load()` |
| **qdrant-client** | Cliente Python do Qdrant (vector DB) | Busca por similaridade vetorial — é o "motor de busca" do RAG | `QdrantClient(url=...)`, `.create_collection()`, `.upsert()`, `.query_points()` |
| **Ollama** (runtime) | Servidor de inferência local de LLMs | Roda modelo local (`qwen2.5-coder:32b`) sem depender de API paga/nuvem — decisão alinhada ao seu hardware (APU com ROCm) | Não é chamado diretamente pelo código do Copilot — o `langchain-ollama` fala com ele via HTTP em `settings.ollama_host` |
| **LLM Gateway** (`app/llm/factory.py`) | Abstracao interna, nao uma lib externa | Permite trocar Ollama por OpenAI/Azure OpenAI via `Settings.llm_provider`, sem tocar no grafo | `get_chat_model()` retorna um `BaseChatModel` do LangChain, seja qual for o provedor escolhido |
| **Langfuse** | Observabilidade de agentes/LLM | Visibilidade de tempo/tokens/payload de cada etapa, sem isso o sistema era uma caixa-preta | `@observe` (decorator), `CallbackHandler` (LangChain), `get_client().flush()` |
| **pytest** | Framework de testes | Padrão de mercado Python; `conftest.py` implementa skip automático de testes de integração se a stack estiver fora do ar | `@pytest.mark.parametrize`, `@pytest.mark.integration`, fixtures |
| **promptfoo** | Comparação/regressão de prompt e modelo | Usado *fora* do código de produção — ferramenta de decisão, não dependência do Copilot em si | `providers` (exec customizado chamando `run_diagnosis` de verdade), `tests`/`assert` |
| **pyRFC** | Binding Python pro SAP NetWeaver RFC SDK | O `RFCConnector` ja tem `use_real=True` implementado e pronto, mas **`pyrfc` foi arquivado pela propria SAP** (mai/2026) - bloqueio persiste, agora por falta de binding mantido, alem do SDK licenciado (que e gratuito pra cliente real com S-user, so nao pra este portfolio) | Ver `app/connectors/rfc_connector.py`, docstring atualizada |

### Analogia ABAP

- `BaseModel` do Pydantic ≈ uma estrutura DDIC com checagem de domínio automática na entrada — só que validado em runtime pelo framework, não numa `CALL FUNCTION` de validação manual.
- `QdrantClient` ≈ um sistema de busca por similaridade — não existe equivalente direto em ABAP clássico; o mais próximo conceitualmente é uma busca fuzzy (`FUZZY SEARCH` no HANA), mas aqui a busca é por **significado semântico** via vetor, não por texto.

---

## 3. Roteiro de Debug no VS Code

Use a configuração **"Debug: graph.py (caso IDoc travado)"** do `.vscode/launch.json` — ela já roda com `--debug`, então você vê o prompt exato e a resposta bruta do LLM no console, além dos breakpoints.

Pra ir direto num símbolo, use `Ctrl+Shift+O` (Windows/Linux) com o arquivo aberto — digite o nome da função e pula direto pra ela, sem precisar rolar.

### BP1 — Entrada da requisição (validação Pydantic)

**Arquivo:** `app/main.py`, dentro da função `diagnose(request: IncidentRequest)`

**Coloque o breakpoint na primeira linha do corpo da função** (o `return run_diagnosis(request)`).

**O que observar:** no painel de variáveis, expanda `request` — já é um objeto `IncidentRequest` totalmente validado. Se você chamar a API com `interface_type: "sap"` (valor inválido, já que só aceita `"odata"`/`"rfc"`), **o breakpoint nunca vai disparar** — o FastAPI já teria rejeitado com HTTP 422 antes de chegar aqui.

**Pergunta que este ponto responde:** "onde exatamente a validação de contrato acontece, e o que already está garantido quando meu código de negócio começa a rodar?"

> Nota: para debugar via HTTP de verdade (não só CLI), use a config **"Debug: FastAPI (uvicorn)"** do launch.json, suba o servidor com F5, e dispare a requisição de outro terminal com `curl`/HTTPie.

### BP2 — Montagem do estado inicial

**Arquivo:** `app/agent/graph.py`, função `run_diagnosis`, logo após a criação de `initial_state`

**O que observar:** o dicionário `initial_state` — repare que `retrieved_context`, `diagnosis` e `report_markdown` **ainda não existem** nele. O `CopilotState` é um `TypedDict` com `total=False`, ou seja, cada node preenche só o que é responsabilidade dele.

**Pergunta:** "o que exatamente entra no grafo antes de qualquer processamento, e o que fica pra cada node produzir?"

### BP3 — Node do conector

**Arquivo:** `app/agent/graph.py`, função `connector_node`

Coloque o breakpoint na linha `result = connector.fetch(...)`.

**Passos:**
1. Antes da chamada: inspecione `state.get("interface_type")` e `state.get("identifier")` — vieram da requisição original
2. **Step Into** (F11) dentro de `connector.fetch()` — você cai em `app/connectors/rfc_connector.py` (ou `odata_connector.py`), dentro de `RFCConnector.fetch()`
3. Observe o dicionário `_MOCK_SCENARIOS` no escopo do módulo — é aqui que o "sistema SAP simulado" realmente vive
4. Depois do `return`, volte pro `connector_node` (F5 ou Step Out) e veja `result` — um `ConnectorResult` com `source_system`, `status`, `error_code`, `message`, `raw`, `is_fallback`

**Pergunta:** "como o conector decide o que retornar, e o que acontece quando o identificador não é reconhecido?" (tente rodar com um `--id` que não existe em `_MOCK_SCENARIOS` pra ver o fallback sendo escolhido no `.get(identifier, _DEFAULT)`)

### BP4 — Node de retrieval (Qdrant)

**Arquivo:** `app/rag/retriever.py`, função `retrieve`

Breakpoint em `results = client.query_points(...)`.

**Passos:**
1. Antes: inspecione `query_vector` — uma lista de ~768 floats (a dimensão do `nomic-embed-text`). É a "tradução" da sua pergunta em texto pra um ponto no espaço vetorial
2. Step Over (F10) na chamada — é aqui que a rede vai até o Qdrant (`http://127.0.0.1:6333`)
3. Depois: expanda `results` — cada item tem `.payload["source"]`, `.payload["text"]`, `.score`. **O `score` é a peça mais importante aqui** — foi ele que decidiu, na comparação `0.904` vs `0.559`, qual documento vence

**Pergunta:** "o retriever está de fato retornando o documento certo, com margem de confiança suficiente, ou está empatado com outro candidato?" — isso foi exatamente o que caçamos manualmente quando descobrimos o bug de mistura de contexto (ver `docs/PROCESSO_DESENVOLVIMENTO.md`, Fase 3).

### BP5 — Node de diagnóstico (chamada ao LLM)

**Arquivo:** `app/agent/graph.py`, função `diagnose_node`

> **Atualizado após code review:** o código não usa mais `llm.invoke(prompt)`
> direto — usa `llm.with_structured_output(DiagnosisModel, include_raw=True)`,
> que valida a saída contra um schema Pydantic e retorna um **dicionário**,
> não um `AIMessage` puro.

Dois breakpoints:

**5a.** Na linha `result = structured_llm.invoke(prompt, ...)` — **antes** de executar.
- Inspecione `prompt` (string completa)
- Inspecione `llm` — confirme `model`, `temperature=0.0`, `seed=42`

**5b.** Logo depois, na linha `raw_message = result["raw"]`.
- `result` é um `dict` com duas chaves: `"raw"` (o `AIMessage` original, com `.content` em texto puro) e `"parsed"` (uma instância de `DiagnosisModel` já validada, ou `None` se a validação estruturada falhar)
- Se `parsed` vier `None`, o código cai no bloco de fallback logo abaixo (parsing manual tolerante do `raw_message.content`) — é a mesma rede de segurança de sempre, agora como *segunda* camada, não a única

**Pergunta:** "o modelo recebeu exatamente o contexto que eu esperava, e a validação estruturada (`parsed`) teve sucesso, ou caiu no fallback manual?"

### BP6 — Guardrails determinísticos

**Arquivo:** `app/agent/graph.py`, função `_apply_confidence_guardrails` (extraída de `diagnose_node` após o code review — antes ficava inline)

Três verificações em sequência, todas de código, nenhuma delas depende do LLM se autoavaliar corretamente:

1. **Clamp de range:** `diagnosis["confidence"] = max(0.0, min(1.0, ...))` — defesa em profundidade mesmo com `Field(ge=0.0, le=1.0)` já validando na origem via Pydantic
2. **Fallback do conector:** mesmo guardrail de sempre — identificador não reconhecido → teto de confiança 0.4
3. **Contexto vazio:** guardrail mais novo — se não veio nenhum documento do retriever **e** não tem dado de conector, teto de confiança 0.3, `matched_source` forçado pra `None`

**O que observar:** rode uma vez com um caso conhecido (nenhum guardrail deveria disparar), uma vez com identificador desconhecido (guardrail 2), e uma vez com uma descrição totalmente fora do domínio sem `--interface` (guardrail 3).

**Pergunta:** "quantas camadas independentes de proteção existem entre uma resposta ruim do LLM e o que chega no usuário final — e cada uma delas dispara quando deveria?"


### BP7 — Node de relatório

**Arquivo:** `app/agent/graph.py`, função `report_node`, no `return {"report_markdown": report}`

**O que observar:** a f-string `report` montada — compare com o `DiagnosisResponse.report_markdown` que sai na resposta HTTP final. Esse é o último ponto onde você vê tudo junto: causa raiz, confiança, fontes, dado do conector.

**Pergunta:** "o relatório final reflete fielmente tudo que os nodes anteriores descobriram, ou perdeu informação no caminho?"

---

## 4. Arquivos de Configuração

### `pyproject.toml`

| Seção | O que configura | Efeito observável no debug |
|---|---|---|
| `[project.dependencies]` | Bibliotecas de produção | Se faltar uma aqui, `uv sync` não instala e o `import` falha antes de qualquer breakpoint disparar |
| `[project.optional-dependencies.dev]` | `pytest`, `ruff`, `httpx` | Só existem no seu ambiente, nunca seriam instaladas numa imagem de produção enxuta |
| `[tool.hatch.build.targets.wheel]` `packages = ["app"]` | Diz ao build backend onde está o código-fonte | Sem isso, `uv sync` falhava (bug real que resolvemos no início do projeto) |
| `[tool.pytest.ini_options]` `markers` | Declara o marker `integration` | É o que permite `pytest -m integration` filtrar só os testes que precisam da stack |
| `[tool.ruff]` `line-length` | Regra de lint/format | Reflete diretamente no que `ruff-format` reescreve no seu código |

### `.env` (na raiz de `~/integration-incident-copilot`)

Cada variável mapeia 1:1 pra um campo de `app/config.py::Settings`:

| Variável no `.env` | Campo em `Settings` | O que muda no comportamento |
|---|---|---|
| (não setado, usa default) | `llm_model` | Qual modelo o `diagnose_node` chama — foi editando isso indiretamente (via `sed` no código, antes do `app/config.py` existir) que trocamos de `qwen3:30b-a3b` pra `qwen2.5-coder:32b` |
| `NEO4J_PASSWORD` | `neo4j_password` | Usado pelo GraphRAG (`app/rag/graph_store.py`) quando `GRAPH_RAG_ENABLED=true` — desligado por default, mas implementado e testado (nao mais so provisionado) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | `langfuse_*` | Sem essas três, o `CallbackHandler()` do Langfuse falha silenciosamente em autenticar — os traces simplesmente não aparecem em `localhost:3000` |

**Para ver a configuração efetiva sem ler código:** `uv run python -m app.config`

### Qdrant

Duas collections, criadas dinamicamente por `app/rag/ingest.py::ensure_collection()`:

- `sap_incident_docs` — a que o `diagnose_node` de fato consulta (via `retrieve_node`)
- `sap_reference_library` — biblioteca de estudo pessoal, **nunca consultada pelo grafo**

O tamanho do vetor (`vector_size`) não é hardcoded — é lido do próprio embedding gerado (`len(vectors[0])`), então se você trocar `EMBEDDING_MODEL` no `.env`, a próxima ingestão cria a collection com a dimensão certa automaticamente (mas atenção: **misturar embeddings de dimensões diferentes na mesma collection quebra a busca** — trocar de modelo de embedding exige reindexar do zero).

### Ollama

Dois modelos com papéis diferentes, nenhum overlap:
- `qwen2.5-coder:32b` — geração de texto/JSON (`diagnose_node`)
- `nomic-embed-text` — embeddings (ingestão e consulta no RAG)

---

## 5. Estudo de Caso Guiado

### Mapeando cenários de negócio SAP nos mocks técnicos existentes

Os conectores mock de hoje são genéricos (não específicos de SuccessFactors/Ariba/Concur), mas os *tipos de falha* que simulam mapeiam de forma honesta nos cenários reais do seu ecossistema:

| Cenário de negócio | Falha técnica equivalente já implementada | Mock a usar |
|---|---|---|
| S/4HANA MM ↔ e-procurement terceiro (timeout no pedido de compra) | Timeout OData sem paginação | `odata_timeout_cpi.md` (via texto, sem conector ainda) |
| SuccessFactors EC ↔ S/4HANA HCM (erro de IDoc em dados mestre) | IDoc status 51, dado mestre ausente | `RFC-IDOC-51-DEMO` |
| SuccessFactors ECP ↔ terceiro de folha/banco (falha de conexão) | RFC connection refused | `RFC-CONN-REFUSED-DEMO` |
| **Ariba ↔ S/4HANA MM/SD (IDoc 51 em pedido/fatura)** | **IDoc status 51, dado mestre ausente** | **`RFC-IDOC-51-DEMO`** ← caso guiado abaixo |
| Concur ↔ S/4HANA FI/HCM (erro 401 em despesas de viagem) | HTTP 401, credencial/token OAuth2 | `CPI-401-DEMO` |

Escolhi o cenário **Ariba ↔ S/4HANA (IDoc 51)** pra debugar do início ao fim porque é o único que já validamos formalmente com dado real (5 execuções idênticas com seed fixo, mais 3 repetições na comparação de modelo) — então cada valor abaixo é o que você **vai ver de verdade**, não uma simulação hipotética.

### Narrativa de negócio

Ariba envia um pedido de compra pro S/4HANA via integração de dados mestre. O IDoc gerado trava com status 51 — o documento de aplicação (o pedido, nesse caso) não é criado porque o **material 4711 não está cadastrado no centro 1000** de destino. Isso é uma falha clássica de dado mestre não sincronizado entre os dois sistemas, não um problema de conectividade.

### Passo a passo com debugger

1. Abra `.vscode/launch.json`, escolha **"Debug: graph.py (caso IDoc travado)"** (já vem com `--interface rfc --id RFC-IDOC-51-DEMO`)
2. Coloque os 7 breakpoints da Seção 3
3. Aperte F5

**No BP3 (connector_node):** `identifier = "RFC-IDOC-51-DEMO"`. Step Into em `RFCConnector.fetch()` — você vê o dicionário retornando:
```
ConnectorResult(
    source_system="RFC", status="error", error_code="51",
    message="IDoc com status 51 - Application Document Not Posted",
    raw="IDOC: 0000000001234567\nSTATUS: 51\nMESSAGE: Erro ao criar
         documento de aplicacao - material 4711 nao cadastrado no
         centro 1000",
    is_mock=True, is_fallback=False
)
```
Isso é o "payload simulando o que Ariba/S4 reportariam" — em produção, seria aqui que entraria a chamada real (BAPI de status de IDoc, ou API OData equivalente).

**No BP4 (retrieve_node):** a query efetiva combina a descrição (`"IDoc travado"`) com `data.message` — o retriever busca por *"IDoc travado\nIDoc com status 51 - Application Document Not Posted"*. Resultado: `idoc_status_51.md` com score `0.904` — folga grande sobre o segundo colocado, sinal de correspondência forte.

**No BP5a (antes do invoke):** o `prompt` inclui o bloco `connector_block` com o `raw` completo do IDoc — o modelo recebe o número do material (4711) e do centro (1000) **verbatim**, não uma paráfrase.

**No BP5b (depois do invoke):** `raw` deve ser um JSON como:
```json
{"matched_source": "idoc_status_51.md", "probable_root_cause":
"O material 4711 não está cadastrado no centro 1000, impedindo a
criação do documento de aplicação.", "confidence": 0.9,
"next_steps": [...]}
```

**No BP6 (guardrail):** `data.is_fallback` é `False` (identificador reconhecido) — o bloco `if` nem executa, `diagnosis["confidence"]` permanece `0.9`.

**No BP7 (report_node):** o `report_markdown` final junta tudo — descrição original, dado do conector (`status=error, codigo=51`), causa raiz, confiança 90%, próximos passos, e a lista de fontes candidatas (`cpi_http_401.md, idoc_status_51.md`, mostrando que outro documento chegou a competir mas perdeu).

### O que esse caso guiado prova sobre a arquitetura

O dado que "resolveu" o diagnóstico não veio do texto livre do usuário (`"IDoc travado"` sozinho é vago demais) — veio do **conector**, que é o equivalente do sistema SAP real reportando o erro estruturado. Isso é a demonstração viva de por que o `connector_node` roda **antes** do `retrieve_node`: dado de sistema estruturado é mais confiável que descrição textual humana, e o pipeline foi desenhado deliberadamente pra refletir essa prioridade — não por acaso.
