# Proposta: Camada de Interoperabilidade A2A para o SAP Integration Copilot

> **Status: Implementada.** Ver `app/a2a/` (`agent_card.py`,
> `task_manager.py`, `server.py`) e a seção "A2A (Agent2Agent)" em
> [docs/ARCHITECTURE.md](../ARCHITECTURE.md). Os pré-requisitos que
> mantinham esta proposta arquivada (conectores SAP fechados + suíte de
> testes automatizada) foram satisfeitos nas Fases 7/8. A ressalva
> abaixo sobre a GA inbound do Joule (Q4/2026) continua válida e não
> muda com esta implementação: o endpoint A2A aqui é compatível com o
> protocolo aberto, não uma integração endossada/GA com o Joule.

## Contexto

O Copilot já está funcional (RAG + LangGraph, 4/4 casos de teste
validados). A arquitetura da SAP oficializa dois protocolos
complementares para agentes de IA:

- **MCP** — acesso a ferramentas/dados (uso interno do Joule a
  capacidades SAP)
- **A2A (Agent2Agent)** — interoperabilidade externa entre agentes,
  caminho preferido da SAP para conectar agentes pro-code externos ao
  ecossistema Joule

## Correção importante em relação à proposta original

O suporte A2A do Joule hoje é **unidirecional (outbound)**: o Joule já
consegue chamar um agente externo via Agent Card. A direção
**inbound** — um orquestrador externo (ou um agente pro-code, como
este Copilot) sendo chamado pelo Joule como par — ainda **não é GA**;
o Agent Gateway que habilita isso está pré-GA, com disponibilidade
geral prevista para Q4/2026.

Ou seja: implementar um servidor A2A no Copilot hoje é **compatível
com o protocolo aberto** (A2A é um padrão vendor-neutral, mantido pela
Linux Foundation, independente do cronograma de GA da SAP) e deixa o
projeto **pronto para a GA inbound do Joule**, mas não deve ser
apresentado como "já consumível pelo Joule hoje" — essa parte
específica (Joule descobrindo/chamando agentes externos como
sub-agentes) ainda depende da GA de Q4/2026.

## Objetivo

Adicionar uma camada A2A **em paralelo** ao endpoint FastAPI
existente, sem alterar o núcleo (grafo LangGraph, RAG, conectores).

## O que NÃO muda

- Grafo LangGraph (retrieve → diagnose → report)
- Pipeline RAG (Qdrant + embeddings Ollama)
- Conectores SAP (OData/RFC)
- Lógica de diagnóstico e geração do relatório Markdown

## Componentes novos (módulo `app/a2a/`)

1. **Agent Card** (`agent_card.py`) — JSON descrevendo a capacidade
   "diagnosticar incidente de integração SAP": nome, descrição,
   skills, requisitos de auth, endpoint
2. **Task manager assíncrono** (`task_manager.py`) — traduz o ciclo de
   vida de task A2A (`submitted` → `working` → `completed`/`failed`)
   em chamadas internas ao grafo LangGraph existente; devolve o
   relatório como *artifact* da task
3. **Servidor A2A** (`server.py`) — expõe o endpoint A2A
   (HTTP(S)/JSON-RPC 2.0) que coexiste com o `/diagnose` do FastAPI,
   ambos chamando a mesma orquestração por trás
4. **Autenticação** — OAuth2/JWT básico entre agentes (simplificado
   para fins de portfólio/demo, documentado como gap de produção)

## Padrão arquitetural

Adapter pattern: a camada A2A isola o protocolo (ainda em evolução
até o GA) da lógica de negócio, evitando acoplamento prematuro.

## Critério de aceite

- Agent Card publicado e válido
- Task A2A de diagnóstico executa o grafo LangGraph e retorna o mesmo
  relatório Markdown já validado nos 4 casos de teste, agora como
  artifact A2A
- FastAPI `/diagnose` continua funcionando sem alteração (regressão
  zero) — **pré-requisito real**: existir suíte de testes automatizada
  (pytest) que verifique isso mecanicamente, não só manualmente
- Riscos a validar antes de iniciar: maturidade das SDKs Python para
  A2A (protocolo tem pouco mais de um ano de existência)

## Onde entra no roteiro

Extensão das Fases 4/6 do plano de aprendizado (LangChain/LangGraph/
MCP → integração final), sem impacto nas fases anteriores já
concluídas. Recomendado entrar **depois** dos conectores SAP e da
suíte de testes automatizada.

## Notas de implementação (retrospectiva)

- **Sem SDK externo de A2A**: o risco de maturidade de SDK citado
  acima nas "Correções importantes" foi contornado implementando o
  subconjunto necessário do protocolo (Agent Card + JSON-RPC 2.0 +
  ciclo de vida de task) diretamente com FastAPI/Pydantic, em vez de
  depender de uma biblioteca de terceiros ainda instável. Mais código
  próprio, porém sem risco de dependência quebrando por trás.
- **Subconjunto de estados de task**: implementados só
  `submitted -> working -> completed|failed`, os únicos alcançáveis
  por um agente síncrono e autocontido como este (sem
  `input_required`/`auth_required`/`canceled`/`rejected`) — ver
  `app/a2a/task_manager.py`.
- **Autenticação**: chave estática opcional via header
  (`A2A_API_KEY`), não OAuth2/JWT — mantido como gap de produção
  documentado, não escondido (ver seção "Componentes novos" acima,
  item 4).
- **Regressão zero validada por teste, não manualmente**: os testes em
  `tests/test_a2a.py` chamam a MESMA função (`run_diagnosis`) que
  `/diagnose`, via injeção de dependência no `TaskManager` — não há
  como o endpoint A2A divergir do comportamento do REST sem quebrar um
  teste.
