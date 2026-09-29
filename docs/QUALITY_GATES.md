# Quality Gates (DA-51)

A suite de testes responde "o código faz o que promete?". Os gates respondem
outra pergunta: **"o que o projeto afirma sobre a própria qualidade continua
verdade?"** Antes da DA-51, as afirmações mais fortes do README — "10/10 no
promptfoo", "hit@1 0.923 do mmarco", "45 queries de dashboard" — eram
verificadas à mão, uma vez, e nunca mais.

## Mapa dos gates

| Gate | O que protege | Custo | Onde roda |
|---|---|---|---|
| `rag_dataset_schema` | `data/eval/rag_eval_dataset.json`: campos obrigatórios, `interface_type` dentro do Literal do pipeline, piso de 15 casos | instantâneo | todo push/PR |
| `corpus_coverage` | todo `expected_sources` existe em `data/sample_docs/` | instantâneo | todo push/PR |
| `dataset_difficulty_mix` | existe ao menos um caso `hard` e um `out_of_scope` | instantâneo | todo push/PR |
| `reranker_invariant` | o modelo em produção é o vencedor medido, com hit@1 e margem sobre o baseline | instantâneo | todo push/PR |
| `promptfoo_configs` | configs do promptfoo são YAML válido, têm `prompts`/`tests`/`providers`, e os scripts `exec:` referenciados existem | instantâneo | todo push/PR |
| `llm_baseline` | existe baseline versionado do promptfoo (comparação de regressão de LLM) | instantâneo | todo push/PR |
| `candidate_das_fresh` | nenhuma DA marcada como "candidata" no `CLAUDE.md` já entregue em `docs/ARCHITECTURE.md` | instantâneo | todo push/PR |
| `migrations_and_dashboards` (job) | `alembic upgrade head` em banco limpo + as 45 queries dos 4 dashboards | ~1 min | todo push/PR |
| `llm_eval` (job) | promptfoo contra o baseline; falha em regressão de caso | depende do provider | agendado 03:17 UTC + manual |

## Rodando localmente

```bash
# todos os gates determinísticos
uv run python scripts/quality_gate.py

# um gate só
uv run python scripts/quality_gate.py --only reranker_invariant

# relatório JSON (mesmo artefato que o CI publica)
uv run python scripts/quality_gate.py --json /tmp/quality-gate.json

# promote aviso a falha
uv run python scripts/quality_gate.py --strict
```

Exit code `1` = falha. O relatório vai para `$GITHUB_STEP_SUMMARY` no CI.

```bash
# job de migrações + dashboards, local, contra um banco descartável
docker exec <container-postgres> createdb -U iic iic_ci_check
DATABASE_URL="postgresql://iic:<senha>@localhost:5432/iic_ci_check" uv run alembic upgrade head
uv run python scripts/validate_dashboards.py --dsn "postgresql://iic:<senha>@localhost:5432/iic_ci_check"
```

## O gate de LLM, com honestidade

O promptfoo deste repositório roda **modelo local** via `exec:` provider
(`scripts/promptfoo_provider.py`). Um runner hospedado do GitHub não tem Ollama
nem o modelo de 51 GB, então o job `llm_eval` só roda no agendado ou manual, e
mesmo assim exige um provider alternativo configurado (`OPENAI_API_KEY`).

Sem provider, o job **não fica verde**: ele escreve "NÃO EXECUTADO" no summary
explicando que o gate de LLM segue sem verificação automática. Um gate que
reporta sucesso sem ter rodado é pior que não existir.

Para fechar o ciclo quando rodar localmente:

```bash
npx promptfoo eval -c promptfooconfig.yaml -o resultados.json
uv run python scripts/quality_gate.py --write-promptfoo-baseline resultados.json  # grava data/eval/promptfoo_baseline.json
git add data/eval/promptfoo_baseline.json
```

Depois disso, `--compare-promptfoo resultados.json` falha quando um caso que
passava no baseline falha agora, avisa quando um caso some do resultado e
ignora casos novos (que ainda não estão no baseline). O payload malformado é
recusado com exit 1 — nunca interpretado como "zero regressões".

## O que estes gates **não** cobrem

- **Qualidade do RAG de verdade.** `reranker_invariant` confere que o modelo em
  produção é o vencedor *medido antes*. Reexecutar o benchmark exige Qdrant com
  o corpus ingerido e o cross-encoder; isso vive em `scripts/benchmark_rerankers.py`,
  rodado a mão. Um gate que dependesse disso só rodaria no CI com GPU.
- **O promptfoo em todo PR.** Por custo e pelo modelo local, é agendado.
- **Resultado visual dos painéis.** `validate_dashboards.py` prova que a query
  roda; não prova que o painel mostra o que alguém espera.
- **Erros que só aparecem com um filtro específico selecionado.** O validador
  substitui as variáveis de template por "All".

## Lições do processo

1. **O primeiro gate erra a premissa.** A primeira versão rejeitava
   `expected_sources: []` — mas os dois casos `out_of_scope` têm essa lista
   vazia de propósito ("fora do escopo, não deve recuperar nada"). O gate
   estava certo sobre a forma e errado sobre o significado, e teria sido
   desligado na primeira semana. Hoje `out_of_scope` *exige* lista vazia, e
   lista preenchida nesse caso é falha: o caso se contradiz.
2. **Um gate que aceita "nenhum resultado" é um falso verde.**
   `normalize_promptfoo_results` recusa payload vazio ou desconhecido.
3. **O primeiro `candidate_das_fresh` acusou a DA-32** — listada como
   "candidata, aguardam Kyma" no `CLAUDE.md` embora entregue em `67b78e8`. Era
   um bug de documentação real que nenhuma suite pegava.
