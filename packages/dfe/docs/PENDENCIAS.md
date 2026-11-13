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
| P10 | Testes dependentes de Ollama, BM25 e SSL não rodaram no ambiente autônomo | Ambiente sem esses recursos; 5 testes do core falharam só por isso e passam na máquina local | Rodar `uv run pytest tests/ -m "not integration"` localmente antes do merge |
| P11 | Revisão por especialista fiscal | A aprovação de 2026-11-12 não é de especialista; H539, `<=` do H1, `F-amb` e `G-ns` são decisões de produto | Especialista fiscal revisar `docs/REVISAO_DATASET.md`; mudanças entram como `corrigir` em `data/eval/revisao_v1.yaml` |

## Resolvidas

| ID | Pendência | Resolução |
|---|---|---|
| P7 | Revisão do dataset v1 | 104 casos aprovados por Marcos em 2026-11-12 a partir da pré-revisão (decisões 1–5 na D6) |
| P9 | Verificação de assinatura XMLDSig | Perfil fechado NF-e com `lxml` + `cryptography`, read-only, sem cadeia ICP-Brasil/LCR (`certificate_trust=not_verified`) (D4) |
| P8 | Prompt fiscal do especialista | `dfe-1.0.0` medido em 2026-11-12: promptfoo 5/5 com qwen3.6:35b-a3b (PR #15) |
| P5 | Schema de `protNFe` | Entrypoints locais sobre os tipos oficiais do `leiauteNFe` (D3); `retConsSitNFe` segue sem tipo no pacote |
| P4 | Páginas da NT 2018.005 v1.51 (regra 2B08-20) | Conferido em 2026-11-12: seção 4.5, páginas 22–23, cStat 204 "Rejeição: Duplicidade de NF-e"; também no MOC 7.0 Anexo I |
