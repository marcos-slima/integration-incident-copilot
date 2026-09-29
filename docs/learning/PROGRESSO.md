# Progresso da trilha de estudo — Integration Incident Copilot

Registro do que foi feito nas sessões de estudo, do que ficou pendente e
dos erros e lições. Reflete o estado real do código na data indicada.

## Trilha (8 módulos)

| # | Módulo | Arquivos-âncora | Status |
|---|---|---|---|
| 1 | Python moderno | `app/models.py`, `app/config.py` | Em andamento |
| 2 | FastAPI | `app/main.py` | Não iniciado |
| 3 | Pydantic | `app/models.py`, `app/agent/state.py` | Não iniciado |
| 4 | Qdrant + RAG | `app/rag/retriever.py` | Não iniciado |
| 5 | LangChain | `app/llm/factory.py` | Não iniciado |
| 6 | LangGraph | `app/agent/graph.py` | Não iniciado |
| 7 | Nodes | `app/agent/nodes.py` | Não iniciado |
| 8 | Langfuse | dashboard `localhost:3000` | Não iniciado |

## Sessão 2026-09-18

**Feito**
- Leitura dos docs (`ARCHITECTURE`, `PROCESSO_DESENVOLVIMENTO`, `GUIA_DE_ESTUDOS`, `TUTORIAL_ARQUITETURA_DEBUG`, `README`) e dos 7 arquivos de código da trilha.
- Módulo 1, conceito 1 (type hints) explicado sobre `IncidentRequest`, com comparação TypeScript/ABAP Cloud.
- Exercício A/B (`__annotations__`, instância válida, `interface_type='sap'` rejeitado) validado no ambiente: Python 3.12.13, `ValidationError` com `literal_error`.

**Pendente**
- Aprendiz rodar os exercícios A e B e registrar a previsão antes do B.
- Módulo 1, conceito 2: `str | None = None` (tipo vs. valor default).
- Módulo 1: `Field(...)`, `BaseModel`, `pydantic-settings` em `config.py`.
- Checagem de entendimento (2–3 perguntas) do conceito 1.

**Erros e discrepâncias encontrados (docs vs. código)**
- `GUIA_DE_ESTUDOS.md` §3 mostra `DiagnosisModel` em `graph.py`; hoje está em `app/agent/state.py`. [resolvido em 2026-09-29: bloco corrigido para `app/agent/state.py`]
- `TUTORIAL_ARQUITETURA_DEBUG.md` cita breakpoints em `graph.py` para funções que hoje estão em `app/agent/nodes.py`, e descreve um grafo de 4 nodes; o código atual tem `web_search` e, opcionalmente, `graph_enrich`/`graph_write`. [RESOLVIDO em 2026-09-29. Duas coisas: (a) as contagens foram corrigidas para 9 conectores e 9 nodes; (b) a parte de `web_search` estava errada **aqui** — `web_search` não é um node do grafo, é uma tool que o LLM chama dentro do node de diagnóstico (`app/agent/graph.py`: "nao e um node separado no grafo"). Os breakpoints estavam piores do que o registro dizia: apontavam para `app/agent/graph.py` **e** para símbolos inexistentes (`structured_llm`, `result["raw"]`, `diagnosis["confidence"]`), então seguir o tutorial ao pé da letra não levava a breakpoint nenhum. Corrigidos para `nodes.py` com linha e símbolo reais]
- `ARCHITECTURE.md` (diagrama) não mostra o node `web_search`, que existe em `graph.py`. [INVALIDADO em 2026-09-29: `web_search` não é um node — mesma premissa errada do item acima]
- `diagnose_node` usa `create_react_agent` com parsing manual de JSON; o tutorial e o README descrevem `with_structured_output`. O código é a fonte de verdade. [RESOLVIDO em 2026-09-29. Este registro é que estava velho, não a prosa: o structured output foi adotado depois ("avaliacao externa, curto prazo, item 5"). O caminho primário é `create_react_agent(..., response_format=DiagnosisModel)`, que dispara uma chamada adicional com `with_structured_output` de verdade; o parsing por regex é o **último** fallback, só usado quando `structured_response` não vem. README e tutorial estavam certos no efeito, mas nomeavam `include_raw=True` e `structured_llm`, que não existem no código]
- `README.md` (Decisões 4 e 8) cita `qwen2.5-coder:32b` como modelo de produção; `app/config.py` define `qwen3-coder-next:latest`. [resolvido: docs alinhados ao código; as Decisões 4 e 8 foram mantidas como registro histórico, marcadas como superadas pela Fase 12]
- `ailab.sh` não existe em `~/ai-stack` nem no PATH.
- Working tree com alterações não commitadas de outra sessão: `app/models.py` (adiciona `cap`/`apim` ao `Literal`), `app/rag/ingest.py` (lock de extração de PDF), `frontend/src/App.tsx`.

**Lições**
- Type hints em Python não são impostos pelo interpretador; quem os transforma em validação é o Pydantic/FastAPI (papel que o zod cumpre em TypeScript, mas lendo as próprias anotações).
- Ao ensinar sobre o código, conferir `git status` e `git log`: sessões paralelas alteram o projeto.
