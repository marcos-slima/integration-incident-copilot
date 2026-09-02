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
| 7 | Domínio Real (conectores SAP de verdade) | 🔄 Parcial — ver Fase 8 |
| 8 | Acessibilidade & Multi-Vendor (LLM Gateway, ServiceNow real) | ✅ Concluída |

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

## Fase 7 — Domínio Real (não iniciada)

**Objetivo:** substituir os conectores mock por integração real com
um sistema SAP (mesmo que sandbox/trial), fechando a lacuna entre
"demo bem construída" e "ferramenta que toca produção".

Fica registrada como próxima fase natural do roteiro, não como parte
deste ciclo já concluído.

## Nota de atualização — Fase 6 (continuação)

Uma rodada de **code review externo** (10 pontos levantados, 6
confirmados e corrigidos, 2 já resolvidos em rodada anterior, 1 falso
alarme, 1 imprecisão factual corrigida) foi tratada como parte
contínua da Fase 6 — reforça a prática de auditar criticamente
qualquer sugestão (própria ou externa) antes de aplicar, em vez de
aceitar ou rejeitar por autoridade da fonte. Ver seção 9 das
"Decisões de Arquitetura" no README para o detalhamento completo.
