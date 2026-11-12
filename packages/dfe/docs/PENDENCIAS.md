# DF-e Copilot — Pendências

Itens que dependem de ação humana, infraestrutura ou decisão de negócio.
Registrados durante a execução autônoma de 2026-11-12; nenhum bloqueou as
demais atividades.

| ID | Pendência | Por quê | Quem / como resolver |
|---|---|---|---|
| P1 | Mover o core para `packages/core` | `app/` acumula core e IIC; mover quebraria imports, Dockerfile, gates e ~2.000 testes sem revisão humana | Decisão de arquitetura + PR dedicado |
| P2 | Imagem Docker e CI próprios do DF-e Copilot | Hoje o `Dockerfile` só empacota o IIC; o CI roda os dois produtos juntos | Definir pipeline de release do produto |
| P3 | Coleção RAG fiscal (MOC 7.0, NT 2018.005, tabela de rejeições) | Exige Qdrant e Ollama vivos e curadoria dos trechos; o ambiente de execução autônoma não tem esses serviços | Marcos: rodar a ingestão na máquina local e revisar trechos |
| P4 | Conferir páginas da NT 2018.005 v1.51 (regra 2B08-20) | Referência citada sem leitura da página exata | Curadoria fiscal (Marcos) |
| P5 | Schema de `protNFe` | O PL 010g não traz; protocolos ficam com `xsd_validation=not_executed` | Localizar pacote de retorno vigente no Portal NF-e |
| P6 | Tela da UI para `findings` e revisão de causa | Tipos e rótulos já existem no frontend do core; a tela do produto exige decisão de UX e marca próprias | Definir se o DF-e terá frontend próprio ou tema no frontend atual |
| P7 | Dataset de 100 casos com gabarito revisado | Gerador sintético mínimo criado; o gabarito precisa de revisão fiscal humana | Curadoria fiscal (Marcos) |
| P8 | Prompt/persona do especialista fiscal | Mudar prompt exige promptfoo com Ollama (invariante 20) | Rodar promptfoo localmente e regravar baseline |
| P9 | Verificação de assinatura XMLDSig | Fora do MVP; hoje sempre `not_executed` | Decidir biblioteca e escopo (read-only) |
| P10 | Testes dependentes de Ollama, BM25 e SSL não rodaram no ambiente autônomo | Ambiente sem esses recursos; 5 testes do core falharam só por isso e passam na máquina local | Rodar `uv run pytest tests/ -m "not integration"` localmente antes do merge |
