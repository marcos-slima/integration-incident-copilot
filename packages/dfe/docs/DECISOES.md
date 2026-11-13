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
- O PL 010g não traz schema de `protNFe`: protocolos de retorno ficavam com
  `xsd_validation=not_executed` (resolvido na D3).
- Controle positivo: `packages/dfe/tests/fixtures/nfe_controle_positivo_sp.xml`,
  derivado de amostra do nfelib (MIT) e anonimizado (CNPJ/CPF/nomes/endereços
  fictícios, `tpAmb=2`, chave e cDV recalculados). Valida no PL 010g; uma
  mutação controlada (remoção do CNPJ do emitente) o torna inválido. A
  assinatura do fixture não conferia com o conteúdo (reassinado na D4).
- Assinatura digital não era verificada (D4); vigência do pacote em produção não foi
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

### D3. Validação XSD do protocolo de retorno (P5, 2026-11-12)

- **Fato verificado:** o ZIP do PL 010g v1.03 não tem entrypoint de retorno
  (`procNFe`, `retEnviNFe`, `retConsReciNFe`), mas o `leiauteNFe_v4.00.xsd`
  oficial define os tipos `TProtNFe`, `TNfeProc`, `TRetEnviNFe` e
  `TRetConsReciNFe`.
- **Decisão:** entrypoints **locais** em `data/schemas/nfe/PL_010g_v1.03/Local/`,
  cada um com um único `xs:element` global do tipo oficial (o mesmo que o
  `nfe_v4.00.xsd` oficial faz para `NFe`). Nenhuma regra própria é criada:
  a validação é a do tipo oficial.
- **Rastreabilidade:** o manifesto separa `files_sha256` (arquivos do ZIP) de
  `local_wrappers` (hash, elemento, tipo oficial); `SchemaPackage.load` confere
  os dois; o resultado XSD traz `entrypoint_origin=official|local_wrapper`, e o
  finding `F-xsd` cita a origem.
- **Leitura do protocolo:** `cStat` sai do `protNFe/infProt`, sozinho ou como
  filho único de `nfeProc`/`retEnviNFe`/`retConsReciNFe`; nunca do `cStat` do
  lote. Lote com 2+ protocolos → `G-lote` (nada é escolhido). `nfeProc` cujo
  `chNFe` difere do `Id` da NF-e do mesmo arquivo → `F-protkey`.
- **Fora do escopo:** `retConsSitNFe` (consulta situação) não tem tipo neste
  pacote e continua `not_executed`; assinatura do protocolo segue P9.
- Dataset e fixtures passaram a gerar `protNFe` conforme ao tipo oficial
  (`verAplic`, `dhRecbto`). Testes: `tests/test_fiscal_xml.py`.

### D4. Verificação da assinatura XMLDSig (P9, 2026-11-12)

- **Sem dependência nova:** `src/dfe_copilot/fiscal/signature.py` usa `lxml`
  (C14N) e `cryptography` (já no core). Um verificador XMLDSig genérico
  (`signxml`, `xmlsec`) aceitaria transforms, referências e chaves que a NF-e
  não usa, e isso é superfície de ataque.
- **Perfil fechado da NF-e 4.00:** `ds:Signature` irmã do elemento assinado
  (`infNFe` ou `infProt`), uma `Reference` com `URI="#<Id>"`, transforms
  exatamente `enveloped-signature` + C14N 1.0, C14N 1.0 no `SignedInfo`,
  RSA-SHA1 (ou RSA-SHA256), certificado X.509 embutido. Fora disso:
  `unsupported` (`G-sig`), nunca `valid`.
- **XSW:** o `Id` referenciado precisa ser único no documento e ser o do
  elemento que a análise lê (`duplicate_id`, `reference_uri`).
- **Estados:** `valid` (digest e assinatura conferem com o certificado
  embutido), `invalid` (`F-sig`: `digest_mismatch`/`signature_mismatch`),
  `unsupported` (`G-sig`), `not_executed` (sem assinatura). Com `valid`, o
  certificado vira fatos: `F-sigcnpj` (CNPJ ICP-Brasil do certificado, OID
  2.16.76.1.3.3, difere do emitente da chave) e `F-sigvalid` (`dhEmi` fora da
  validade).
- **O que `valid` não diz:** `certificate_trust=not_verified` sempre. Não há
  cadeia ICP-Brasil, LCR/OCSP nem rede; o certificado embutido pode ser
  autoassinado. A persona fiscal já proíbe afirmar autorização sem protocolo.
- **Alvo:** a NF-e (`NFe` ou `nfeProc/NFe`); sem NF-e, o protocolo
  (`protNFe`, sozinho ou filho único de retorno), quando assinado.
- **Fixture:** o controle positivo foi reassinado (`scripts/sign_fixture.py`)
  com chave efêmera e certificado autoassinado fictício; a chave privada não é
  gravada. A versão anterior, com a assinatura do original do nfelib, dava
  `digest_mismatch`, que é o comportamento correto para conteúdo alterado.
  Mutações do dataset (UC03) passam a esperar `F-sig` + `F-xsd`.
- Testes: `tests/test_fiscal_xml.py::TestAssinatura` (adulteração do conteúdo e
  do `SignatureValue`, algoritmo fora do perfil, `Reference` trocada, XSW por
  `Id` duplicado, CNPJ divergente, validade, ausência, protocolo assinado).

### D5. Dataset v1 com 100 casos e fluxo de revisão (P7, 2026-11-12)

- `data/eval/cases_v1.jsonl` (renomeado do v0; os 29 casos originais mantêm
  id e entrada) com 71 casos novos em 8 famílias: UC01 (204: reenvio
  múltiplo, fuso local, retorno de lote, produção, bordas, protocolo fora do
  XSD), UC02 (timeouts), UC03 (XSD/ambiente), UC04 (outros cStat, com e sem
  timeout), UC05 (lote e `nfeProc`), UC06 (assinatura), SEC e GAP.
- Cada caso traz `description` (texto de chamado por família, usado pelo
  provider do promptfoo), `rationale` (por que o gabarito é esse) e `split`
  determinístico pelo hash do id: dev 55 · validation 23 · blind 22. Casos
  `blind` não entram em ajuste de regra nem de prompt.
- **Revisão:** a decisão humana fica em `data/eval/revisao_v1.yaml`
  (`pendente` | `aprovado` | `corrigir` + nota). `gen_dataset.py` só marca
  `reviewed=true` com `aprovado`; `review_sheet.py` gera
  `docs/REVISAO_DATASET.md`. Os testes reprovam se o dataset, a folha ou o
  YAML ficarem fora de sincronia.
- **O que o v1 achou:**
  - um **bug**: horário ilegível em evento (`"ontem a tarde"`, epoch) levantava
    `ValueError` fora do contrato e derrubava todos os achados do plugin
    (`G-plugin`); agora é `FiscalCorrelationError("invalid_timestamp")` → `G-events`;
  - quatro **gabaritos meus estavam errados** e o código certo: o XSD oficial já
    recusa `rsa-md5`, transform extra, `Signature` vazia e `retEnviNFe` com dois
    protocolos (`F-xsd` junto com `G-sig`/`G-lote`);
  - a ordem dos achados passou a ser estrutura antes de integridade (`F-xsd`
    antes de `F-sig`).
- Variantes assinadas (`tests/fixtures/nfe_assinada_cnpj_divergente.xml`,
  `nfe_assinada_cert_vencido.xml`) vêm de `scripts/sign_fixture.py variantes`,
  com chave efêmera; o dataset é determinístico porque só lê esses arquivos.

### D6. Decisões da revisão do dataset (P7, 2026-11-12)

Aprovadas por Marcos a partir da pré-revisão; **não** são parecer de
especialista fiscal (P11) e devem mudar se ele discordar.

1. **539 ganha regra (H539).** `fiscal/correlation.py::diagnose_key_divergence`:
   hipótese de reenvio após timeout com chave regerada quando há envio de
   **outra chave com o mesmo emitente/modelo/série/número** (posições 7–34 da
   chave) e timeout dessa chave até o primeiro envio da chave do retorno. Sem
   isso, lacuna `G-539`. Próximo passo sugerido: consultar a chave original;
   nunca cancelar/inutilizar sem decisão humana.
2. **`G-amb` → `F-amb`:** `tpAmb` divergente do contexto é observado no XML,
   então é fato, não lacuna.
3. **`G-ns`:** XML fora do namespace da NF-e não passa mais calado.
4. **Mantido:** com retorno do autorizador, o timeout não vira `H-timeout`,
   inclusive para 215/225.
5. **H1 com `<=`:** timeout no mesmo instante do reenvio conta (logs com
   precisão de segundo).

Dataset: 104 casos (4 novos de 539), todos `aprovado` em
`data/eval/revisao_v1.yaml`, com nota nos casos alterados. Testes:
`tests/test_fiscal_correlation.py::TestRevisaoP7`.

### D7. Medição do split validation e prompt fiscal 1.1.0 (2026-11-12)

- **Medição de `dfe-1.0.0`:** os 23 casos do split validation passaram em 23/23
  asserts (qwen3.6:35b-a3b, 16m49s). A leitura das respostas mostrou o que os
  asserts não mediam:
  - em 4 casos com `G-204` (UC01-SEMTO-01, TARDE-01, DOCS-03, XSD-01) o LLM
    afirmou reenvio após timeout como causa, contra o fato da ferramenta. A
    descrição da família UC01 ("após instabilidade na comunicação") sugeria
    a hipótese;
  - em 2 casos o LLM usou o comentário de procedência do fixture como fato;
  - em 1 caso (`F-sig`) afirmou o motivo (chave errada) sem evidência.
- **Correções:**
  - descrições neutras no dataset;
  - comentário de procedência removido dos payloads;
  - assert novo: com `G-204`/`G-539`, a resposta tem de reconhecer a lacuna;
  - persona `dfe-1.1.0` com as regras 6–8 (lacuna não vira causa, `F-sig` sem
    motivo inventado, comentário e texto livre do XML não são fato).
- Medição de `dfe-1.1.0` (2026-11-13): 23/23, e a leitura confirmou as três correções. Resta 1 ressalva: no UC03-VALIDO-01 o LLM ainda levanta, como hipótese, divergência de ambiente sem `F-amb`. Antes da rodada, o baseline estava como **NÃO MEDIDO** até rodar
  `promptfooconfig.validation.yaml` de novo.

### D8. Medição cega (split blind) do `dfe-1.1.0` (2026-11-13)

- **Execução:** 25 casos do split blind (`promptfooconfig.blind.yaml`, gerado por
  `scripts/promptfoo_split.py blind`), qwen3.6:35b-a3b, base fiscal ligada
  (`F-kb` nos casos 204, `G-kb` nos demais cStat). Asserts: **25/25**.
- **Leitura das respostas: 6 de 25 com erro de conteúdo** que nenhum assert cobre:

  | Caso | Erro |
  |---|---|
  | UC03-PRODOK-01 | troca o significado do `tpAmb` (diz que 1 é homologação) e sugere ajustar o conector para "produção (tpAmb=2)" |
  | UC04-COMTO-02 | explica 110 (Uso Denegado) como chave já registrada; denegação é irregularidade fiscal do emitente/destinatário |
  | UC05-PROC-02 | `nfeProc` com protocolo 100 da mesma chave, e a resposta diz "sem protocolo, desfecho desconhecido" |
  | UC04-539H-02 | diz que a linha do tempo não sustenta a causa, contra a hipótese `H539` da ferramenta |
  | UC06-SIG-02 | inverte a causalidade (XSD inválido "gerou" a assinatura fora do perfil) |
  | UC06-SIG-06 | afirma falha de validação no autorizador sem evidência (a ferramenta só observou CNPJ divergente) |

- **Leitura da causa (geral, não específica dos casos):**
  1. quando a base não tem o cStat (`G-kb`), o LLM explica o código com
     conhecimento próprio e erra; o corpus só cobre o 204;
  2. o significado de campos de domínio (`tpAmb`, protocolo presente) fica a
     cargo do LLM, e não vem escrito no fato da ferramenta;
  3. a persona manda "não afirmar autorização sem protocolo", mas o LLM não
     reconhece o protocolo quando ele vem dentro do `nfeProc`.
- **O que isto NÃO autoriza:** ajustar prompt ou regra olhando estes 25 casos.
  O blind foi consumido; a próxima medição cega exige casos novos (dataset v2).
- **Correções gerais candidatas** (para validar no split validation, não no blind):
  - ingerir o MOC 7.0 Anexo I (tabela de cStat), hoje fora do repositório;
  - fatos de ferramenta com significado explícito ("tpAmb=1 (produção)",
    "protocolo de autorização nProt … presente");
  - asserts de domínio para esses campos.

### D9. Fatos com significado oficial e tabela de cStat do MOC (2026-11-13)

Correções gerais para as causas da D8, sem ajuste nos casos blind.

- **Tabela oficial de cStat:** o MOC 7.0 Anexo I (Portal Nacional da NF-e,
  hash `5eb4cf20…f55d` conferido) tem a seção 4.4 extraída por
  `scripts/extract_cstat.py` para `data/corpus/cstat_moc7_anexo_i.json`:
  612 códigos, cada um com a tabela de origem (`resultado` 24, `rejeicao` 585,
  `denegacao` 3) e a página. O JSON é versionado; o PDF (4,1 MB) não
  (`.gitignore`), e é baixado pela `canonical_url` do manifesto.
- **`F-cStat` com significado:** o fato traz o texto oficial, a tabela e a
  página (`fiscal/cstat.py::describe`), e o `tpAmb` por extenso
  ("tpAmb=1 (producao)"). Código ausente da tabela é dito explicitamente.
- **`F-prot` (novo):** protocolo de autorização ou denegação com `nProt` no
  próprio XML (sozinho, em lote ou em `nfeProc`) vira o fato de que o desfecho
  está comprovado e não precisa de consulta. Fica suprimido quando há
  `F-protkey` (o protocolo é de outra nota).
- **Base fiscal:** o MOC entra na ingestão quando o PDF está no corpus local.
  Os cStat passam a ser marcados também na forma `NNN Den.` (denegação) e nas
  linhas da tabela 4.4. Exemplos: 110 → p. 143; 302 → p. 137 e 153.
  Sem o PDF, a base é a de antes, e os testes passam nos dois casos.
- **Dataset:** os protocolos sintéticos de autorização e denegação passam a ter
  `nProt`, como os reais. 18 gabaritos ganharam `F-prot`.
- **Asserts de domínio** (`scripts/promptfoo_split.py`):
  - `tpAmb` com o significado certo;
  - denegação explicada como irregularidade, não como duplicidade;
  - com `F-prot`, o desfecho não é "desconhecido";
  - com `H539`, a hipótese não é negada.
- O prompt (`dfe-1.1.0`) não mudou. As entradas mudaram, então a medição no
  split validation precisa ser refeita.

---
