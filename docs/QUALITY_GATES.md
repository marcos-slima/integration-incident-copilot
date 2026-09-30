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
| `implemented_das_documented` | toda DA registrada no `CLAUDE.md` tem prosa localizável (seção `### N. ... (DA-N)` no `README.md`, ou `docs/ARCHITECTURE.md` como local alternativo declarado) — e, no sentido inverso, nenhuma seção `(DA-N)` órfã | instantâneo | todo push/PR |
| `das_index_current` | o índice de DAs do `README.md` é único (sem tabela colada duas vezes), lista exatamente o mesmo conjunto do registro do `CLAUDE.md`, cada linha aponta para seção que existe, e o número da coluna Seção é o do heading real (não o da seção vizinha) | instantâneo | todo push/PR |
| `da_registered` | toda DA citada no código de produto (`app/`, `scripts/`, `alembic/`) tem linha na tabela de DAs do `CLAUDE.md`. Nove DAs estavam fora do livro-razão com a prosa só na docstring: três delas (soberania de dados, AMQP 1.0, circuit breaker Redis) são das mais arquiteturais do projeto e invisíveis para quem navega pelas DAs | instantâneo | todo push/PR |
| `preflight_delegates` | o preflight de RAM do harness é `app/evaluation/ram_preflight.py`, não python inline no `scripts/promptfoo_remote.sh` | instantâneo | todo push/PR |
| `prompt_digest_measured` | o prompt em produção (`app/agent/prompts.py`) tem o mesmo digest do prompt **medido** no `data/eval/prompt_baseline.json` | instantâneo | todo push/PR |
| `docs_markup_integrity` | fences de código balanceados e links relativos `.md` resolvendo, em `docs/`, `README.md` e `CLAUDE.md` | instantâneo | todo push/PR |
| `docs_code_references` | referências `app/x.py::símbolo` e `app/x.py:N` citadas na documentação existem no código e na linha, e toda citação de arquivo .md em backticks existe em lugar real (raiz, `docs/`, `data/sample_docs/` ou ao lado do doc) | instantâneo | todo push/PR |
| `connector_reachable` | todo conector registrado em `app/connectors/__init__.py` é aceito pelo Literal de `interface_type`, pelas choices do `--interface` do CLI, pelo dropdown da UI web (`SYSTEMS` em `frontend/src/components/DiagnoseView.tsx`), é documentado em `app/models.py`/`app/admin/models.py`, e é coberto por `_SAP_INTERFACE_TYPES` ou `_SAAS_INTERFACE_TYPES` (exceto `apim`, cross-vendor por decisão) | instantâneo | todo push/PR |
| `connector_validation_matrix` | todo conector registrado em `app/connectors/__init__.py` tem linha na matriz de validação de `docs/ARCHITECTURE.md` — a única fonte de verdade sobre o que foi testado contra instância real — e nenhuma linha órfã sobrou para conector removido. Apodreceu em silêncio uma vez: `successfactors` ficou meses sem linha, com todos os gates verdes | instantâneo | todo push/PR |
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
- **`prompt_digest_measured` não prova que a medição ainda é válida.** O gate
  compara o digest de produção com o digest gravado. Ele prova que o texto
  não mudou *desde a última vez que alguém regravou o arquivo* — mas nada
  impede que alguém rode `--write-prompt-baseline` sem ter executado o
  promptfoo. O arquivo é uma declaração, não uma prova; o valor está em ele
  ser versionado e revisável no diff. Uma evolução natural é ligar o digest ao
  resultado do promptfoo em `data/eval/promptfoo_baseline.json`, para que
  gravar um digest exija um resultado de medição junto.
- **O digest não cobre o conteúdo variável do prompt.** Logs, payload, chunk
  RAG e dados do conector entram a cada incidente. Dois incidentes com o
  mesmo digest usaram o mesmo *template*, não necessariamente o mesmo
  contexto.
- **Se o número da DA no código é a DA certa.** `da_registered` exige que `DA-N` apareça na
  tabela do `CLAUDE.md`; ele não confere que a linha descreva aquilo que o código faz. Uma DA
  registrada com a prosa trocada passa. É o mesmo limite de toda documentação versionada:
  o gate garante a amarração, não a verdade.
- **A prosa em si.** `implemented_das_documented` verifica que a seção
  *existe* e que a DA está amarrada a ela. Ele não avalia se o texto explica o
  problema, a solução e as limitações — um gate de estilo documental reprovar
  por redação seria o primeiro a ser desligado, e desligar um gate é
  exatamente o que ele existe para evitar.
- **Seções marcadas como `(nota informal — sem DA)`.** São decisões reais que
  nunca receberam numeração e por isso escapam da conferência. O marcador é
  explícito para que a omissão seja visível no diff, mas nada cobra que
  permaneçam informais.
- **A legitimidade do local alternativo.** `implemented_das_documented` aceita
  `docs/ARCHITECTURE.md` para DA-32/33/34/35 sem perguntar se o texto de lá
  está atualizado. Ele confere presença, não atualidade.
- **A prosa fora de `app/x.py::símbolo`.** `docs_code_references` só verifica a
  forma precisa de citar código, que é a que a documentação de debug usa para
  mandar abrir um breakpoint. Identificadores em prosa solta
  (`ANTHROPIC_API_KEY`, `RFC_SYSTEM_INFO`, `${QDRANT_HOST_PORT:-6333}`) são
  deliberadamente ignorados: o primeiro não existe, o segundo é Function
  Module ABAP, o terceiro é variável de shell. Um gate que acuse esses três é
  um gate que alguém desliga.
- **Se a documentação está *certa*, apenas está desatualizada.**
  `docs_code_references` prova que `report_node` é definido em
  `app/agent/nodes.py`; não prova que o tutorial manda abrir o arquivo
  certo, nem que o texto em volta do link está correto. Ele pega referência
  quebrada, não equivalente errada.
- **Fences balanceados não significam markdown válido.** `docs_markup_integrity`
  conta abre/fecha de bloco de código. Tabelas malformadas, listas aninhadas
  erradas e âncoras `#link` que não existem passam.
- **Identificadores citados dentro de bloco de código.** O gate procura
  `app/…py::símbolo` em qualquer lugar do markdown, inclusive dentro de
  blocos ``` ; um exemplo ilustrativo num fence pode ser acusado.
- **Se o conector funciona contra um tenant real.** `connector_reachable` e
  `connector_validation_matrix` conferem presença e coerência: que todo
  conector registrado tem linha na matriz de
  [docs/ARCHITECTURE.md](ARCHITECTURE.md#conectores---mock-vs-real-hoje)
  e que nenhuma linha órfã sobrou. Nenhum dos dois confere a **verdade** da
  afirmação "validado". `connector_validation_matrix` é o gate que impede a
  apodrecer, não o que atesta: ele exige que a matriz * exista* e cubra todo
  conector registrado, porque `SuccessFactorsConnector` (DA-34) ficou meses
  sem linha nenhuma com todos os gates verdes. Já a matriz é uma afirmação
  versionada, revisável no diff — o mesmo limite de `prompt_digest_measured`
  (ver acima). Atestar a validação exigiria o artefato da execução real
  (log da chamada contra o tenant, data, versão do produto), que hoje não
  existe versionado para nenhum dos dez conectores, nem para os quatro
  validados.

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
4. **O próprio gate se quebrou com a própria documentação.** Depois que a
   linha da DA-51 na tabela de DAs passou a citar "das DAs candidatas" numa
   célula, a busca pela *primeira ocorrência* da frase casava ali, recortava
   um bloco sem nenhum `DA-N` e devolvia lista vazia: o gate virava aviso e a
   lista de candidatas deixava de ser verificada sem ninguém perceber. O
   regex passou a ancorar no título, no início da linha, com teste de
   regressão que reproduz o caso real.
5. **Teste verde não prova que o código roda.** O preflight de RAM era um
   heredoc de 92 linhas dentro de `scripts/promptfoo_remote.sh`, sem
   cobertura: a aritmética que decide se a suite carrega 48 G só podia ser
   conferida com a RAM à mão. E o inverso também vale: **o job que roda não
   prova que a coisa medida está medida.** O `rag-quality` rodava com
   `EMBEDDING_BACKEND=fastembed` (DA-38) porque o GitHub Actions não tem
   Ollama, mas a *ingestão* ignorava essa variável e indexava com
   `OllamaEmbeddings` fixo. O retriever consultava com bge-small (384 dims)
   numa collection criada com nomic-embed-text (768), e a busca morria com
   `Wrong input: Vector dimension error: expected dim: 768, got 384`. O job
   era verde por never ter recuperado nada — a falha era de nao
   mensurável, e só apareceu quando o gate passou a ser rodado de verdade.
   Duas correções: a ingestão passou a usar o mesmo resolvedor de embedder
   da consulta (`retriever._get_embeddings`), e `_FastEmbedWrapper` ganhou
   `embed_documents`, que só a consulta usava — sem ele a ingestão falhava
   com `'_FastEmbedWrapper' object has no attribute 'embed_documents'`.
6. **O limiar precisa discriminar os grupos, não existir.** O
   `oos_rejection_rate` media `hits[0]["score"] < 0.7`, e `score` é cosseno
   denso puro. Medido no corpus de avaliação: in-scope vai de 0.719 a 0.871,
   out-of-scope dá 0.753 e 0.779 — **os grupos se sobrepõem**, e o único
   limiar que separaria (0.780) rejeitaria junto 4 dos 13 in-scope. O gate
   media similaridade de cosseno e chamava isso de rejeição. O sinal que
   discrimina é `rerank_score_calibrated` (DA-42): in-scope ≥ 0.975,
   out-of-scope ≤ 0.050. O gate passou a medir o mesmo número que decide a
   admissão na pipeline, no ponto neutro da sigmoid (σ(0) = 0.50), que fica
   a 0.45 dos dois lados. E quando o hit vem sem esse campo — rerank não
   rodou — o gate **erra** em vez de tratar a ausência como 0.0, que seria
   um falso verde pela regra 2. Verificado por perturbação: com a sigmoid
   forçada a 0.99 o gate reprova, e com o `rerank()` neutralizado ele levanta
   `RuntimeError`. Nota: o corpus tem **2** casos out-of-scope; a taxa é
   0%, 50% ou 100%, então a métrica é fraca por construção e o gate serve
   mais como rede de regressão do que como medida de qualidade. Ampliar o
   corpus é trabalho em aberto, não algo que um limiar ajustado conserte.
   verificada carregando 48 G. Movido para
   `app/evaluation/ram_preflight.py` (núcleo puro, 15 testes) e
   `preflight_delegates` passou a vigiar que o script continua delegando —
   sem ele, a matemática poderia voltar para dentro do shell com a suite
   ainda verde, testando um módulo que ninguém chama.
6. **Gate que só checa um sentido passa pelo buraco.** `candidate_das_fresh`
   pergunta "esta DA marcada como candidata já foi entregue?". Nunca fez a
   pergunta inversa — "a DA entregue tem a prosa localizável?" — e por isso
   15 seções de decisão do `README.md` sobreviveram sem rótulo `(DA-N)`,
   invisíveis para qualquer `grep "DA-15"`, e a DA-30 ficou sem seção
   própria em lugar nenhum. A prosa existia; a amarração não. Um gate
   unidirecional não é metade da verificação, é uma verificação que dá
   sensação de cobertura.
7. **Contar errado é pior que não contar.** Auditando a documentação da
   DA-52, o texto dizia "cinco estados" e o invariante 15 do `CLAUDE.md`
   repetia o número, enquanto o enum `ObservationStatus` tinha quatro e a
   seção de limitações do próprio README afirmava que os dois estados extras
   nem existiam. Três artefatos, três afirmações, uma falsa. Por isso
   `TestEstadosDocumentados` compara a tabela e a contagem do README com o
   enum em vez de fixar um literal — o número é consequência, não constante.
