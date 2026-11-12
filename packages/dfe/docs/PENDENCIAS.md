# DF-e Copilot — Pendências

Itens que dependem de ação humana, infraestrutura ou decisão de negócio.
Registrados durante a execução autônoma de 2026-11-12; nenhum bloqueou as
demais atividades.

| ID | Pendência | Por quê | Quem / como resolver |
|---|---|---|---|
| P1 | Mover o core para `packages/core` | `app/` acumula core e IIC; mover quebraria imports, Dockerfile, gates e ~2.000 testes sem revisão humana | Decisão de arquitetura + PR dedicado |
| P2 | Imagem Docker e CI próprios do DF-e Copilot | Hoje o `Dockerfile` só empacota o IIC; o CI roda os dois produtos juntos | Definir pipeline de release do produto |
| P3 | Rodar a ingestão do corpus fiscal no Qdrant local | Código pronto (`dfe_copilot.kb`: coleção híbrida própria `dfe_kb`, metadados por trecho, filtro de aplicabilidade antes da busca, `revogado` excluído, abstenção sem fonte), testado com Qdrant em memória; a ingestão real precisa de Qdrant e Ollama vivos | Marcos: comandos do `README.md` (ambiente da raiz: `--project ../..`) |
| P6 | Tela da UI para `findings` e revisão de causa | Tipos e rótulos já existem no frontend do core; a tela do produto exige decisão de UX e marca próprias | Definir se o DF-e terá frontend próprio ou tema no frontend atual |
| P7 | Dataset de 100 casos com gabarito revisado | Dataset v0 com 29 casos (`data/eval/cases_v0.jsonl`, gerado por `scripts/gen_dataset.py`), gabarito derivado das regras e `reviewed=false`; faltam casos de LLM, divisão dev/validação/teste cego e revisão fiscal | Curadoria fiscal (Marcos) |
| P9 | Verificação de assinatura XMLDSig | Fora do MVP; hoje sempre `not_executed` | Decidir biblioteca e escopo (read-only) |
| P10 | Testes dependentes de Ollama, BM25 e SSL não rodaram no ambiente autônomo | Ambiente sem esses recursos; 5 testes do core falharam só por isso e passam na máquina local | Rodar `uv run pytest tests/ -m "not integration"` localmente antes do merge |

## Resolvidas

| ID | Pendência | Resolução |
|---|---|---|
| P8 | Prompt fiscal do especialista | `dfe-1.0.0` medido em 2026-11-12: promptfoo 5/5 com qwen3.6:35b-a3b (PR #15) |
| P5 | Schema de `protNFe` | Entrypoints locais sobre os tipos oficiais do `leiauteNFe` (D3); `retConsSitNFe` segue sem tipo no pacote |
| P4 | Páginas da NT 2018.005 v1.51 (regra 2B08-20) | Conferido em 2026-11-12: seção 4.5, páginas 22–23, cStat 204 "Rejeição: Duplicidade de NF-e"; também no MOC 7.0 Anexo I |
