# DF-e Copilot — Pendências

Itens que dependem de ação humana, infraestrutura ou decisão de negócio.
Registrados durante a execução autônoma de 2026-11-12; nenhum bloqueou as
demais atividades.

| ID | Pendência | Por quê | Quem / como resolver |
|---|---|---|---|
| P10 | Testes dependentes de Ollama, BM25 e SSL não rodaram no ambiente autônomo | Ambiente sem esses recursos; 5 testes do core falharam só por isso e passam na máquina local | Rodar `uv run pytest tests/ -m "not integration"` localmente antes do merge |
| P11 | Revisão por especialista fiscal | A aprovação de 2026-11-12 não é de especialista; H539, `<=` do H1, `F-amb` e `G-ns` são decisões de produto | Especialista fiscal revisar `docs/REVISAO_DATASET.md`; mudanças entram como `corrigir` em `data/eval/revisao_v1.yaml` |

## Resolvidas

| ID | Pendência | Resolução |
|---|---|---|
| P2 | Imagem e CI próprios | Alvo Docker `dfe`, Compose isolado e workflow específico, com wheels de entrega e smoke autenticado; validação local registrada em `ACEITE_P2.md`. Execução remota aguarda push/PR |
| P6 | Interface e fronteiras do produto | Shell próprio validado; core-ui distribuível; contratos 1.0 e testes isolados por artefato (ver `ACEITE_P6.md`) |
| P1 | Extração do core | Backend em `packages/core`, pacote `copilot-core`, dependências explícitas e import namespace preservado; Docker/CI ajustados |
| P7 | Revisão do dataset v1 | 104 casos aprovados por Marcos em 2026-11-12 a partir da pré-revisão (decisões 1–5 na D6) |
| P3 | Ingestão do corpus fiscal no Qdrant local | Executada por Marcos em 2026-11-13 com o MOC 7.0 Anexo I; a medição de validação teve `F-kb` em 13 casos (D10) |
| P9 | Verificação de assinatura XMLDSig | Perfil fechado NF-e com `lxml` + `cryptography`, read-only, sem cadeia ICP-Brasil/LCR (`certificate_trust=not_verified`) (D4) |
| P8 | Prompt fiscal do especialista | `dfe-1.2.0` medido em 2026-11-13: split validation 23/23 com a base fiscal ligada; medição cega de `dfe-1.1.0` registrada na D8 (validation na D10) |
| P5 | Schema de `protNFe` | Entrypoints locais sobre os tipos oficiais do `leiauteNFe` (D3); `retConsSitNFe` segue sem tipo no pacote |
| P4 | Páginas da NT 2018.005 v1.51 (regra 2B08-20) | Conferido em 2026-11-12: seção 4.5, páginas 22–23, cStat 204 "Rejeição: Duplicidade de NF-e"; também no MOC 7.0 Anexo I |
