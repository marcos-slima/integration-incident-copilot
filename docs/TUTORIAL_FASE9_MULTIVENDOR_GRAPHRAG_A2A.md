# Tutorial — Fase 9: Multi-Vendor Completo, GraphRAG e A2A

> Complementar ao [TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md](TUTORIAL_ACESSIBILIDADE_MULTIVENDOR.md)
> (Fase 8) — aqui não se repete o que já está explicado lá (padrão de
> conector real vs. mock, LLM Gateway, estrutura de testes com
> `httpx.MockTransport`). Este documento cobre só o que é novo na
> Fase 9: fechamento dos conectores multi-vendor restantes, GraphRAG
> real (desligado por default) e a camada A2A.

## 1. Motivo desta fase

Ao final da Fase 8, uma pergunta direta ("alguma dívida técnica em
relação ao genai-engineering-template?") revelou uma lista concreta de
itens documentados como "reservado para uso futuro" ou "arquivado" em
várias partes do projeto (GraphRAG em `ARCHITECTURE.md`, a proposta A2A
em `docs/proposals/`, 3 dos 4 conectores multi-vendor de referência
ainda mock). Em vez de deixar isso como débito permanente, esta fase
fecha cada item com código real e testado — ou, onde o "real de
verdade" dependia de infraestrutura externa indisponível neste
ambiente (um Neo4j de produção, uma conta Salesforce/Workday/Ariba),
com uma estrutura real, testada com dublês de infraestrutura (driver
fake, `httpx.MockTransport`), pronta para ativar trocando só
configuração — nunca com "preencher a documentação e deixar o código
por fazer".

## 2. Artefatos gerados nesta fase

| Arquivo | O que é |
|---|---|
| `app/connectors/salesforce_connector.py` | Conector real (OAuth2 Client Credentials + SOQL) |
| `app/connectors/workday_connector.py` | Conector real (OAuth2 + REST) |
| `app/connectors/ariba_connector.py` | Conector real (OAuth2 + REST) |
| `app/connectors/odata_connector.py` | Reescrito: ganhou `use_real` (mesmo padrão do RFC) |
| `app/rag/graph_store.py` | GraphRAG real sobre Neo4j, desligado por default |
| `app/agent/graph.py` | Nodes `graph_enrich`/`graph_write` (só entram no grafo se `GRAPH_RAG_ENABLED=true`) |
| `app/a2a/agent_card.py` | Agent Card do protocolo A2A |
| `app/a2a/task_manager.py` | Ciclo de vida de task A2A → `run_diagnosis()` |
| `app/a2a/server.py` | Servidor JSON-RPC 2.0 (`POST /a2a`) |
| `app/main.py` | Monta `/a2a` e `GET /.well-known/agent-card.json` |
| `tests/test_connectors.py` | +14 testes (Salesforce, Workday, Ariba, OData real) |
| `tests/test_graph_store.py` | 9 testes do GraphRAG (sessão Neo4j fake) |
| `tests/test_a2a.py` | 9 testes da camada A2A (`TestClient` + `diagnosis_fn` stub) |
| `docker-compose.yml` | Serviço `neo4j` sob profile opt-in `graphrag` |
| `.env.example` | Variáveis dos 3 conectores novos + GraphRAG + A2A |
| `.vscode/launch.json` | +3 debug configs de conector, +2 de pytest focado |
| `docs/proposals/a2a-interoperability-layer.md` | Status atualizado: arquivada → implementada |

## 3. Testando os conectores novos

Mesmo padrão da Fase 8 — sem nada configurado no `.env`, todos caem em
modo demo (mock):

```bash
uv run python -m app.agent.graph "Case sem sync" --interface salesforce --id SF-CASE-00847-DEMO --debug
uv run python -m app.agent.graph "Falha SuccessFactors->Workday" --interface workday --id WD-SYNC-FAIL-DEMO --debug
uv run python -m app.agent.graph "PO bloqueado" --interface ariba --id ARIBA-PO-BLOCKED-DEMO --debug
```

Ou via VS Code: "Debug: graph.py (Salesforce - Case sem sync SAP)",
"(Workday - falha de sync SuccessFactors)", "(Ariba - PO bloqueado por
fornecedor)".

Para ativar o caminho real de qualquer um deles, preencher as
variáveis correspondentes no `.env` (ver `.env.example` — todas
seguem `<SISTEMA>_<CAMPO>`, ex. `SALESFORCE_INSTANCE_URL`,
`WORKDAY_TENANT`, `ARIBA_BASE_URL`). Nenhuma exige tocar em código
Python.

Rodar só os testes de conector: `uv run pytest tests/test_connectors.py -v`
(22 testes, todos sem infraestrutura externa).

## 4. Ativando o GraphRAG de verdade

Por default, `GRAPH_RAG_ENABLED=false` — o grafo LangGraph roda
exatamente igual a antes desta fase (dois nodes a menos). Para ativar:

```bash
# 1. Sobe o Neo4j real (NAO sobe com "docker compose up" default)
docker compose --profile graphrag up -d neo4j

# 2. No .env:
echo "GRAPH_RAG_ENABLED=true" >> .env
echo "NEO4J_PASSWORD=changeme123" >> .env   # mesmo valor do docker-compose.yml, ou o que voce definiu

# 3. Cria as constraints (uma vez)
uv run python -m app.rag.graph_store --init

# 4. Roda um incidente com interface/identificador (so grava/consulta
#    o grafo quando ha interface+identificador - texto livre sem
#    conector nao tem o que relacionar)
uv run python -m app.agent.graph "RFC travado de novo" --interface rfc --id RFC-GWY-POOL-TIMEOUT-DEMO --debug
```

Rode o mesmo comando uma segunda vez: o prompt impresso (`--debug`)
deve mostrar um bloco "Historico conhecido desta interface (1
incidente(s) anterior(es)...)" — prova visual de que a escrita da
primeira execução alimentou a consulta da segunda.

Sem um Neo4j real disponível neste ambiente de desenvolvimento, a
validação aqui foi feita com uma sessão fake
(`tests/test_graph_store.py`, `uv run pytest tests/test_graph_store.py -v`)
— o Cypher e o mapeamento de dados estão testados; a integração ponta
a ponta contra um Neo4j real não foi.

## 5. Testando a camada A2A manualmente

Com a API no ar (`uv run uvicorn app.main:app --reload`):

```bash
# Agent Card
curl -s http://127.0.0.1:8000/.well-known/agent-card.json | python3 -m json.tool

# Enviar uma mensagem (executa o diagnostico e retorna a task ja completed)
curl -s -X POST http://127.0.0.1:8000/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "iFlow falhando com erro 401"}]
      }
    }
  }' | python3 -m json.tool

# Consultar a task pelo id retornado acima (result.id)
curl -s -X POST http://127.0.0.1:8000/a2a \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": "<TASK_ID_AQUI>"}}' \
  | python3 -m json.tool
```

Isso exige Ollama no ar (mesma dependência do `/diagnose` REST) porque
o task manager chama a mesma orquestração completa. Os testes
automatizados (`uv run pytest tests/test_a2a.py -v`) não exigem isso —
usam um `diagnosis_fn` stub injetado no `TaskManager`.

Para ativar autenticação por chave: `A2A_API_KEY=algum-valor` no
`.env`, e enviar o header `X-A2A-Api-Key: algum-valor` em toda
chamada a `/a2a` (sem o header ou com valor errado: HTTP 401).

## 6. O que NÃO foi feito nesta fase (gap honesto)

- Nenhum dos conectores novos (Salesforce/Workday/Ariba) nem o caminho
  real do OData foi validado contra uma conta/tenant real — só via
  `httpx.MockTransport` simulando a API documentada de cada fornecedor.
  Mesma ressalva de sempre, agora consistente em todos os conectores.
- GraphRAG não foi validado contra um Neo4j real (sem Docker daemon
  disponível no ambiente onde isso foi construído) — só com sessão
  fake.
- A camada A2A não foi testada contra um cliente A2A externo de
  verdade (ex: um orquestrador real usando uma SDK A2A) — só via
  `curl`/`TestClient` simulando o formato de mensagem documentado.
- `app/services/` continua vazio, deliberadamente — ver a nota em
  `docs/ARCHITECTURE.md` sobre por que isso não é dívida técnica neste
  momento.
- A lista de ferramentas de sustentação de infraestrutura pessoal
  (`docs/ferramentas-sustentacao-ecossistema.md`) não faz parte desta
  fase — é backlog de tooling de operação, não de arquitetura da
  solução, e tratá-la junto seria dispersão de escopo.
