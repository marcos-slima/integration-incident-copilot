# DF-e Copilot — Decisões

Decisões do domínio fiscal. As decisões do core continuam em
`docs/DECISOES_DE_ARQUITETURA.md` da raiz (DA-93 fase 4 e DA-94).

## D0. Produto independente sobre o core (DA-94)

O DF-e Copilot é um produto separado do Integration Incident Copilot, com
pacote, ponto de entrada, testes e documentação próprios, e reutiliza o core
por um plugin de domínio. A flag `FISCAL_DOMAIN_ENABLED` e o contrato fiscal
no core (fase 1 original) foram removidos.

### D1. Domínio fiscal DF-e, fase 1: flag e contrato de contexto (DA-93)

**O problema.** O estudo IIC → SAP AMS/DF-e (`ESTUDO_IIC_AMS_DFE_CONSOLIDADO_v3`)
propõe especializar o copiloto em incidentes de documentos fiscais
eletrônicos. Fazer isso num branch de longa duração quebraria os gates
(`prompt_digest_measured`, `connector_reachable`, `da_registered`) a cada
mudança do núcleo. E o domínio fiscal não é um mecanismo de transporte:
reaproveitar `interface_type` para "nfe" misturaria dois eixos.

**A solução.** Domínio plugável na `main`, atrás de flag:
- (Substituído pela DA-94: o produto é ligado por `DOMAIN_PLUGINS=dfe_copilot.plugin`.)
- Contrato `FiscalContext` (hoje em `packages/dfe/src/dfe_copilot/context.py`), versionado
  (`schema_version="1"`) e restrito ao recorte do MVP: `family="nfe"`,
  `model="55"`, `layout_version="4.00"`, `environment`
  (`homologation`/`production`), `jurisdiction` (UF validada) e
  `event_time` opcional para filtro de vigência.
- (Original) `IncidentRequest.fiscal_context` opcional; `run_diagnosis()` só copiava o
  contexto para o `CopilotState` com a flag ligada. Desligada, o campo é
  aceito e descartado — grafo, prompt (digest inalterado) e roteamento
  ficam idênticos.

**Validação.** `packages/dfe/tests/test_plugin.py`: default da flag, recorte
aceito e rejeitado (UF inválida, modelo 65, leiaute 3.10), propagação com a
flag ligada e descarte com ela desligada.

**Limitações (registradas de propósito).**
- Fase 1 não tem nó fiscal: com a flag ligada o contexto só chega ao estado.
  Ferramentas determinísticas (XSD, cStat, dígito da chave) e o especialista
  fiscal são as fases seguintes do estudo (PRs 2–6).
- Recorte definido para o MVP: SP, homologação. O contrato aceita outras UFs
  e produção porque o recorte é de dados de teste, não de schema.
- `IncidentEventData` (webhook/AMQP) ainda não carrega `fiscal_context`.

**Fase 2 (2026-11-11): ferramentas fiscais determinísticas.**
- `packages/dfe/src/dfe_copilot/fiscal/xml_tools.py`: parse seguro (sem DTD, entidades, rede ou
  schema remoto; limite de 1 MB), decomposição da chave de acesso com dígito
  verificador módulo 11 e cUF IBGE, leitura de `cStat`/`xMotivo`/`tpAmb` só em
  `protNFe/infProt` (nunca status de lote), hash SHA-256 dos bytes originais e
  `findings` do tipo `fact`.
- Pacote oficial versionado em `packages/dfe/data/schemas/nfe/PL_010g_v1.03/`, com
  manifesto (`PL_010g_v1.03.manifest.json`): URL do Portal Nacional NF-e,
  publicação 26/10/2026, obtido em 11/11/2026, SHA-256 do ZIP e de cada XSD.
  `SchemaPackage.load()` recusa o pacote se algum hash divergir.
- Validação XSD compilada sem rede contra o entrypoint `NFe/nfe_v4.00.xsd`.
  Sem pacote, sem entrypoint ou com versão desconhecida, o resultado é
  `not_executed`, nunca `valid`.
- Testes: `packages/dfe/tests/test_fiscal_xml.py`.

**Limitações da fase 2.**
- O PL 010g não traz schema de `protNFe`: protocolos de retorno ficam com
  `xsd_validation=not_executed`.
- Controle positivo: `packages/dfe/tests/fixtures/nfe_controle_positivo_sp.xml`,
  derivado de amostra do nfelib (MIT) e anonimizado (CNPJ/CPF/nomes/endereços
  fictícios, `tpAmb=2`, chave e cDV recalculados). Valida no PL 010g; uma
  mutação controlada (remoção do CNPJ do emitente) o torna inválido. A
  assinatura do fixture não confere com o conteúdo, por construção.
- Assinatura digital não é verificada; vigência do pacote em produção não foi
  confirmada (`applicability=candidate_homologation_not_production_certified`).
- As ferramentas ainda não são chamadas pelo grafo (nó fiscal: fase seguinte).

**Fase 3 (2026-11-12): linha do tempo e regras fiscais.**
- `packages/dfe/src/dfe_copilot/fiscal/correlation.py`: eventos `sent`/`timeout`/`response`/`error`
  normalizados para UTC (horário sem fuso é recusado) e correlacionados só
  por identificador explícito (`document_ref` = chave de acesso).
- `diagnose_duplicity()` (UC01, cStat 204): fato `F-204` com a regra
  `NT2018.005_v1.51_sec4.5_2B08-20`; a hipótese `H1` (reenvio após timeout)
  exige dois ou mais envios do mesmo documento **e** timeout entre o primeiro
  e o último. Sem isso, fato `G-204` e causa `inconclusive`.
- `diagnose_timeout()` (UC02): timeout sem resposta posterior gera
  `processing_outcome=unknown` e hipótese de resposta perdida; nunca afirma
  indisponibilidade do autorizador.
- Nenhuma regra gera ação automática; a próxima ação é sempre consulta
  autorizada antes de reenviar.
- Testes: `packages/dfe/tests/test_fiscal_correlation.py`.

**Fase 4 (2026-11-12): fatos × hipóteses na resposta.**
- `DiagnosisResponse.findings` (`Finding`: `fact`/`hypothesis`, fonte,
  evidências, teste de confirmação, alternativas) e `cause_status`
  (`hypothesis`/`inconclusive`; `confirmed` só por revisão humana).
- `app/agent/findings.py::build_findings` monta a lista por código: fatos das
  ferramentas (`CopilotState.fiscal_findings`) e a causa proposta como
  `hypothesis`; um `fact` sem fonte determinística é rebaixado.
- O `DiagnosisModel` (saída estruturada do LLM) **não** mudou: o LLM continua
  sem declarar fatos, e o digest do prompt fica igual (invariante 20).
- Novo `trust_level` **`rule_reference`**: a evidência do rule engine deixa de
  ser `system_observed` (regra publicada não prova que o incidente a violou).
  No relatório ela continua entre as evidências primárias; UI e catálogos
  pt-BR/en ganharam o rótulo.
- Testes: `tests/test_da93_findings.py`.

### D2. Prompt fiscal do especialista (P8, 2026-11-12)

- `src/dfe_copilot/prompt.py`: persona fiscal (N2/N3 AMS, NF-e 55/4.00, GRC
  NFE/DRC, PI/PO, CPI) com cinco regras: achados `fact` prevalecem; nunca
  afirmar autorização/cancelamento/denegação sem protocolo; nunca recomendar
  reenvio, cancelamento, inutilização ou contingência como ação automática
  (204 → consultar a chave primeiro); citar regra/NT só se estiver nos
  achados ou no contexto; lacunas `G-*` viram passo de coleta.
- O core injeta a persona e os findings do plugin no prompt via
  `DfePlugin.prompt` (hook da DA-94); o template do core não muda e o digest
  do core segue `62b9b620…`.
- Proveniência própria: `prompt_version=dfe-1.0.0`, digest em
  `data/eval/prompt_baseline.json`. `tests/test_prompt.py` reprova se a persona
  ou o template do bloco mudarem sem regravar o baseline.
- Medição: `promptfooconfig.yaml` + `scripts/promptfoo_provider.py` (pipeline
  real, rule engine desligado para medir o LLM). **Ainda não medido**: o
  baseline registra `NAO MEDIDO` até a execução local com Ollama.

---
