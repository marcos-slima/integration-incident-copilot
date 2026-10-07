# Benchmark de rerankers (DA-29)

Último item do backlog priorizado pela segunda revisão arquitetural
externa (P2), depois do modelo `VERIFIED_AS` do GraphRAG (DA-28, ver
`docs/ARCHITECTURE.md`). A revisão apontou que o reranker de produção
(`cross-encoder/ms-marco-MiniLM-L-6-v2`, ver
`app/rag/retriever.py::RERANKER_MODEL`) nunca foi comparado
formalmente contra alternativas — inclusive alternativas
**multilíngues**, relevante aqui porque as queries reais são
majoritariamente em português, enquanto `ms-marco-MiniLM-L-6-v2` foi
treinado só em inglês (dataset MS MARCO).

> **Re-execução de 2026-10-07 (validação, M-20).** O resultado original
> (abaixo) usou 13 consultas e apresentou "+7pp Hit@1" para uma diferença de
> **uma consulta**. Com o dataset atual (18 consultas in-scope) o quadro é:
>
> | Modelo | Hit@1 | MRR@5 | Latência média | vs. L-6 (McNemar exato) | IC 95% da diferença de Hit@1 |
> |---|---|---|---|---|---|
> | ms-marco-L6 (baseline) | 16/18 (0,89) | 0,94 | 1 725 ms | — | — |
> | ms-marco-L12 | 16/18 (0,89) | 0,94 | 3 362 ms | p = 1,0 (0 × 0) | [0,000, 0,000] |
> | **mmarco-mMiniLMv2** (produção) | 17/18 (0,94) | 0,97 | 2 497 ms | p = 1,0 (1 × 0) | [+0,000, +0,167] |
> | bge-reranker-base | 17/18 (0,94) | 0,96 | 8 925 ms | p = 1,0 (2 × 1) | [−0,111, +0,222] |
>
> Leitura honesta: nenhum candidato é estatisticamente melhor que o
> baseline neste dataset; o mmarco segue em produção por ser **multilíngue**
> (consultas em PT-BR) e mais rápido que os outros multilíngues/maiores, não
> por "acertar mais". Latência medida em CPU deste ambiente (sem GPU); os
> números absolutos não se comparam com os da execução original. Os dados
> por consulta e as estatísticas (`vs_baseline`) estão em
> `data/eval/reranker_benchmark_results.json`; o gate `reranker_invariant`
> passa a avisar quando a vantagem não é significativa.

Este documento registra a metodologia, os resultados e a recomendação
resultante. O script que produziu os números está em
`scripts/benchmark_rerankers.py`; os dados brutos por query estão em
`data/eval/reranker_benchmark_results.json`.

## Metodologia

- **Corpus**: os mesmos `data/sample_docs/*.md` usados em produção
  (fallback da collection `incidents`), **chunked com os mesmos
  parâmetros de produção** (`MarkdownTextSplitter`, `chunk_size=500`,
  `chunk_overlap=50` — ver `app/rag/ingest.py::TARGETS["incidents"]`)
  — na execução original, 10 documentos → 40 chunks (na re-execução de
  2026-10-07, 15 documentos).
- **Queries + relevância**: os casos *in-scope* de
  `data/eval/rag_eval_dataset.json` — 13 na execução original (2 casos
  `out_of_scope` excluídos); na re-execução de 2026-10-07, 18 *in-scope*
  (10 `out_of_scope` excluídos). Casos `out_of_scope` não têm documento
  relevante, então não fazem sentido para comparar rerankers.
- **Protocolo**: cada modelo reranqueia o **corpus inteiro** (não um
  pool pré-filtrado por um primeiro estágio) para cada query — isola a
  qualidade do reranker em si, sem a variável do retriever híbrido.
  Múltiplos chunks do mesmo documento são deduplicados mantendo o de
  maior score, produzindo um ranking por DOCUMENTO (não por chunk),
  que é o nível em que a relevância do dataset de avaliação está
  anotada.
- **Métricas**: Hit@1, Recall@5, MRR@5, nDCG@5 (relevância binária —
  lógica pura e testada em `app/rag/eval_metrics.py` /
  `tests/test_eval_metrics.py`, sem depender de nenhum modelo
  carregado) + latência média/p95 por query (ms) + delta de RSS do
  processo após carregar o modelo (proxy de RAM, via `psutil`) +
  contagem de parâmetros (proxy de custo computacional mais estável
  entre ambientes que uma medida de %CPU de uma chamada síncrona
  single-thread).

### Nota de recursos (honestidade sobre o ambiente)

Os números abaixo foram gerados numa máquina real (AMD Ryzen AI Max+
395), mas dentro de um sandbox com **2 vCPUs, ~3.8GB RAM e ~3.7GB de
disco livre** no momento da execução — sem GPU (inferência 100% CPU,
mesmo caso de uso de produção deste projeto, que também roda sem GPU
dedicada por padrão). Os 4 modelos candidatos foram escolhidos para
caber nesse orçamento (nenhum acima de ~1.2GB em disco); o cache do
Hugging Face Hub é limpo após cada modelo avaliado (`_clear_hf_cache_for`
em `scripts/benchmark_rerankers.py`) para não estourar os ~3.7GB
disponíveis rodando os 4 em sequência.

Um modelo maior e mais forte em multilíngue (ex:
`BAAI/bge-reranker-v2-m3`, ~2.2GB) fica registrado como próximo passo
natural — não testado aqui por risco de OOM/disco cheio nesta máquina
específica, não por decisão de que não valeria a pena.

## Modelos avaliados

| Chave | Modelo (Hugging Face) | Notas |
|---|---|---|
| `ms-marco-L6` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | **Baseline da comparação** (era o modelo de produção antes da DA-29). Inglês apenas (MS MARCO). |
| `ms-marco-L12` | `cross-encoder/ms-marco-MiniLM-L-12-v2` | Mesma família do baseline, mais profundo (12 camadas vs. 6). Inglês apenas. |
| `mmarco-mMiniLMv2` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Treinado no mMARCO (MS MARCO traduzido, incluindo português) — candidato multilíngue leve. |
| `bge-reranker-base` | `BAAI/bge-reranker-base` | Multilíngue (100+ idiomas, incluindo PT), maior que os demais (~1.1GB). |

## Resultados

| Modelo | Hit@1 | Recall@5 | MRR@5 | nDCG@5 | Latência média | Latência p95 | RSS (Δ) | Parâmetros |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `ms-marco-L6` (baseline) | 0.85 | 1.00 | 0.92 | 0.94 | 787ms | 813ms | +45MB | 22.7M |
| `ms-marco-L12` | 0.85 | 1.00 | 0.92 | 0.94 | 1552ms | 1593ms | +49MB | 33.4M |
| **`mmarco-mMiniLMv2`** | **0.92** | 1.00 | **0.96** | **0.97** | **1195ms** | **1255ms** | +430MB | 117.6M |
| `bge-reranker-base` | 0.92 | 1.00 | 0.96 | 0.97 | 4171ms | 4281ms | +379MB | 278.0M |

(Tabela da execução original, 13 casos in-scope. Latência = tempo da
chamada `predict()` reranqueando os 40 chunks do
corpus inteiro por query, não só os poucos candidatos que o pipeline
de produção normalmente passa ao reranker após a fusão RRF — um limite
superior conservador, não o tempo real esperado em produção.)

Recall@5 é 1.00 para todos os 4 modelos — com um corpus de só 10
documentos e a maioria das queries tendo 1 único documento relevante,
o top-5 quase sempre contém a resposta certa; a métrica que de fato
diferencia os modelos aqui é **onde** dentro do top-5 o documento certo
aparece (Hit@1, MRR@5, nDCG@5).

## Recomendação

**Trocar `RERANKER_MODEL` em `app/rag/retriever.py` de
`cross-encoder/ms-marco-MiniLM-L-6-v2` para
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.**

Motivos:

1. **Na execução original (13 casos), melhor em toda métrica de
   qualidade** (ver a nota de re-execução de 2026-10-07 no topo: com 18
   casos a diferença não é significativa) — Hit@1 sobe de 0.85 para
   0.92, MRR@5 de 0.92 para 0.96, nDCG@5 de 0.94 para 0.97. Faz
   sentido: as queries reais são majoritariamente em português
   (jargão técnico às vezes em inglês, ex: "IDoc", "iFlow"), e o
   baseline nunca viu português durante o treino.
2. **Empata em qualidade com `bge-reranker-base`** (mesmos 4 números),
   mas é **3.5x mais rápido que o bge** (1195ms vs. 4171ms) e usa menos da
   metade dos parâmetros (117M vs. 278M) — sem custo de qualidade
   observado neste dataset, a versão menor é estritamente melhor
   escolha para latência de produção. Em relação ao L-6, porém, o
   mmarco é **mais lento**: ~1,5x na execução original (1195ms vs.
   787ms) e ~1,45x na re-execução de 2026-10-07 (2497ms vs. 1725ms).
3. Continua sendo um modelo pequeno o suficiente para rodar em CPU
   sem GPU dedicada, mesma premissa operacional do projeto hoje.

Esta troca **foi aplicada** (DA-29): hoje
`RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"` em
`app/rag/retriever.py`. Foi uma mudança de uma linha, sem nenhuma outra
mudança de código, já que `rerank()` só depende do nome do modelo ser
compatível com a interface `CrossEncoder.predict()`, que todos os 4
candidatos satisfazem. A re-execução de 2026-10-07 (18 casos) mostrou
que a diferença de qualidade em relação ao L-6 **não é
estatisticamente significativa** (McNemar exato, p = 1,0); a escolha
foi mantida pelo suporte multilíngue (consultas em PT-BR), não por
ganho de acerto comprovado.

## Não-objetivos desta fase

- `BAAI/bge-reranker-v2-m3` (multilíngue, mais forte, ~2.2GB) não foi
  testado por risco de recursos nesta máquina específica — candidato
  natural para uma rodada futura com mais RAM/disco disponível.
- O dataset de avaliação (13 casos in-scope na execução original; 18
  hoje) é pequeno — suficiente
  para uma comparação relativa entre modelos, mas não para afirmar
  significância estatística. Crescer `data/eval/rag_eval_dataset.json`
  com mais casos (especialmente casos com múltiplos documentos
  relevantes, onde Recall@5 deixaria de saturar em 1.00) melhoraria a
  confiança deste benchmark.
- Latência medida é do reranker reranqueando o corpus inteiro (40
  chunks na execução original), não do pool reduzido que o pipeline de produção
  normalmente passa após a fusão RRF (`_retrieve_unified`,
  `app/rag/retriever.py`) — um limite superior, não uma medida de
  latência end-to-end de produção.
