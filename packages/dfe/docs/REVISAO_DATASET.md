# Revisão fiscal do dataset v1 (P7)

> Gerado por `scripts/review_sheet.py`. Não edite aqui: a decisão vai em
> `data/eval/revisao_v1.yaml` (`aprovado` | `corrigir` + nota).

- Casos: **104** · sintéticos, CNPJ fictício 11.222.333/0001-81
- Split: dev 56 · validation 23 · blind 25
- Revisão: aprovado 104 · corrigir 0 · pendente 0
- Revisor: Marcos Lima (aprovou a pre-revisao de 2026-11-12; revisao por especialista fiscal pendente) · data: 2026-11-12

**O que conferir em cada linha:** a justificativa descreve corretamente a
regra fiscal/técnica, e os achados esperados são os que um N2/N3 esperaria
das ferramentas (fatos `F-*`, lacunas `G-*`, hipóteses `H*`). Casos `blind`
não devem ser usados para ajustar regra nem prompt.

## UC01 — cStat 204 e reenvio após timeout

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| UC01-H1-01 | dev | `F-cStat`, `F-204`, `H1` | 204 com envio, timeout e reenvio do mesmo documento: H1 (reenvio apos timeout) como hipotese. | aprovado |
| UC01-H1-02 | dev | `F-cStat`, `F-204`, `H1` | 204 com envio, timeout e reenvio do mesmo documento: H1 (reenvio apos timeout) como hipotese. | aprovado |
| UC01-H1-03 | validation | `F-cStat`, `F-204`, `H1` | 204 com envio, timeout e reenvio do mesmo documento: H1 (reenvio apos timeout) como hipotese. | aprovado |
| UC01-H1-04 | validation | `F-cStat`, `F-204`, `H1` | 204 com envio, timeout e reenvio do mesmo documento: H1 (reenvio apos timeout) como hipotese. | aprovado |
| UC01-H1-05 | dev | `F-cStat`, `F-204`, `H1` | 204 com envio, timeout e reenvio do mesmo documento: H1 (reenvio apos timeout) como hipotese. | aprovado |
| UC01-SEMTO-01 | validation | `F-cStat`, `F-204`, `G-204` | 204 com dois envios mas sem timeout entre eles: sem base para H1, lacuna G-204. | aprovado |
| UC01-TARDE-01 | validation | `F-cStat`, `F-204`, `G-204` | Timeout depois do ultimo envio nao explica o reenvio: G-204. | aprovado |
| UC01-DOCS-01 | dev | `F-cStat`, `F-204`, `G-204` | Segundo envio e' de outra chave: nao ha reenvio do MESMO documento, G-204. | aprovado |
| UC01-SEMTO-02 | dev | `F-cStat`, `F-204`, `G-204` | 204 com dois envios mas sem timeout entre eles: sem base para H1, lacuna G-204. | aprovado |
| UC01-TARDE-02 | dev | `F-cStat`, `F-204`, `G-204` | Timeout depois do ultimo envio nao explica o reenvio: G-204. | aprovado |
| UC01-DOCS-02 | dev | `F-cStat`, `F-204`, `G-204` | Segundo envio e' de outra chave: nao ha reenvio do MESMO documento, G-204. | aprovado |
| UC01-SEMTO-03 | blind | `F-cStat`, `F-204`, `G-204` | 204 com dois envios mas sem timeout entre eles: sem base para H1, lacuna G-204. | aprovado |
| UC01-TARDE-03 | dev | `F-cStat`, `F-204`, `G-204` | Timeout depois do ultimo envio nao explica o reenvio: G-204. | aprovado |
| UC01-DOCS-03 | validation | `F-cStat`, `F-204`, `G-204` | Segundo envio e' de outra chave: nao ha reenvio do MESMO documento, G-204. | aprovado |
| UC01-H1MULT-01 | dev | `F-cStat`, `F-204`, `H1` | Tres envios com dois timeouts intercalados: H1. | aprovado |
| UC01-H1MULT-02 | dev | `F-cStat`, `F-204`, `H1` | Tres envios com dois timeouts intercalados: H1. | aprovado |
| UC01-H1MULT-03 | dev | `F-cStat`, `F-204`, `H1` | Tres envios com dois timeouts intercalados: H1. | aprovado |
| UC01-H1TZ-01 | validation | `F-cStat`, `F-204`, `H1` | Horarios em -03:00 e Z normalizados para UTC; ordem real envio-timeout-reenvio: H1. | aprovado |
| UC01-H1TZ-02 | dev | `F-cStat`, `F-204`, `H1` | Horarios em -03:00 e Z normalizados para UTC; ordem real envio-timeout-reenvio: H1. | aprovado |
| UC01-H1TZ-03 | validation | `F-cStat`, `F-204`, `H1` | Horarios em -03:00 e Z normalizados para UTC; ordem real envio-timeout-reenvio: H1. | aprovado |
| UC01-RET-01 | blind | `F-cStat`, `F-204`, `H1` | Protocolo 204 dentro de retEnviNFe: cStat lido do protocolo (nao o 104 do lote); H1. | aprovado |
| UC01-RET-02 | blind | `F-cStat`, `F-204`, `H1` | Protocolo 204 dentro de retEnviNFe: cStat lido do protocolo (nao o 104 do lote); H1. | aprovado |
| UC01-RET-03 | validation | `F-cStat`, `F-204`, `H1` | Protocolo 204 dentro de retEnviNFe: cStat lido do protocolo (nao o 104 do lote); H1. | aprovado |
| UC01-PROD-01 | dev | `F-cStat`, `F-204`, `H1` | Mesmo caso H1 com tpAmb=1 em contexto de producao: sem lacuna de ambiente. | aprovado |
| UC01-PROD-02 | dev | `F-cStat`, `F-204`, `H1` | Mesmo caso H1 com tpAmb=1 em contexto de producao: sem lacuna de ambiente. | aprovado |
| UC01-PROD-03 | validation | `F-cStat`, `F-204`, `H1` | Mesmo caso H1 com tpAmb=1 em contexto de producao: sem lacuna de ambiente. | aprovado |
| UC01-BORDA-01 | blind | `F-cStat`, `F-204`, `H1` | Timeout no mesmo instante do reenvio conta (log com precisao de segundo; revisao P7): H1. | aprovado |
| UC01-BORDA-02 | blind | `F-cStat`, `F-204`, `G-204` | Um so envio com timeout: nao houve reenvio, G-204. | aprovado |
| UC01-XSD-01 | validation | `F-cStat`, `F-xsd`, `F-204`, `G-204` | Protocolo sem dhRecbto (obrigatorio no TProtNFe): F-xsd; sem eventos, G-204. | aprovado |
| UC01-XSD-02 | dev | `F-cStat`, `F-xsd`, `F-204`, `G-204` | Protocolo sem dhRecbto (obrigatorio no TProtNFe): F-xsd; sem eventos, G-204. | aprovado |

## UC02 — timeout sem retorno

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| UC02-UNK-01 | dev | `G-noxml`, `F-timeout`, `H-timeout` | Timeout sem resposta posterior e sem XML: desfecho desconhecido (H-timeout). | aprovado |
| UC02-RESP-01 | dev | `G-noxml`, `F-timeout` | Houve resposta depois do timeout: F-timeout sem H-timeout. | aprovado |
| UC02-UNK-02 | dev | `G-noxml`, `F-timeout`, `H-timeout` | Timeout sem resposta posterior e sem XML: desfecho desconhecido (H-timeout). | aprovado |
| UC02-RESP-02 | dev | `G-noxml`, `F-timeout` | Houve resposta depois do timeout: F-timeout sem H-timeout. | aprovado |
| UC02-UNK-03 | dev | `G-noxml`, `F-timeout`, `H-timeout` | Timeout sem resposta posterior e sem XML: desfecho desconhecido (H-timeout). | aprovado |
| UC02-RESP-03 | dev | `G-noxml`, `F-timeout` | Houve resposta depois do timeout: F-timeout sem H-timeout. | aprovado |
| UC02-DUPLO-01 | dev | `G-noxml`, `F-timeout`, `H-timeout` | Dois timeouts e nenhuma resposta apos o ultimo: H-timeout. | aprovado |
| UC02-DUPLO-02 | dev | `G-noxml`, `F-timeout`, `H-timeout` | Dois timeouts e nenhuma resposta apos o ultimo: H-timeout. | aprovado |
| UC02-DUPLO-03 | blind | `G-noxml`, `F-timeout`, `H-timeout` | Dois timeouts e nenhuma resposta apos o ultimo: H-timeout. | aprovado |
| UC02-RESPANTES-01 | dev | `G-noxml`, `F-timeout`, `H-timeout` | A unica resposta e' anterior ao ultimo timeout: desfecho do ultimo envio desconhecido, H-timeout. | aprovado |
| UC02-RESPANTES-02 | validation | `G-noxml`, `F-timeout`, `H-timeout` | A unica resposta e' anterior ao ultimo timeout: desfecho do ultimo envio desconhecido, H-timeout. | aprovado |
| UC02-RESPANTES-03 | blind | `G-noxml`, `F-timeout`, `H-timeout` | A unica resposta e' anterior ao ultimo timeout: desfecho do ultimo envio desconhecido, H-timeout. | aprovado |
| UC02-SEMTO-01 | dev | `G-noxml` | Sem timeout registrado: nenhuma regra de timeout se aplica; so a lacuna de XML. | aprovado |

## UC03 — XSD e ambiente

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| UC03-VALIDO-01 | validation | — | NF-e completa, conforme ao XSD e com assinatura que confere: nenhum fato de erro. | aprovado |
| UC03-MUT-01 | dev | `F-xsd`, `F-sig` | Alteracao no infNFe: XSD reprova (F-xsd) e o digest da assinatura deixa de conferir (F-sig). | aprovado |
| UC03-MUT-02 | validation | `F-xsd`, `F-sig` | Alteracao no infNFe: XSD reprova (F-xsd) e o digest da assinatura deixa de conferir (F-sig). | aprovado |
| UC03-MUT-03 | validation | `F-xsd`, `F-sig` | Alteracao no infNFe: XSD reprova (F-xsd) e o digest da assinatura deixa de conferir (F-sig). | aprovado |
| UC03-AMB-01 | dev | `F-amb` | NF-e com tpAmb=2 num contexto declarado de producao: fato F-amb (observado; revisao P7). | aprovado |
| UC03-AMBPROT-01 | validation | `F-cStat`, `F-prot`, `F-amb` | Protocolo de homologacao (tpAmb=2) num contexto de producao: F-prot (nProt) e F-amb. | aprovado |
| UC03-PRODOK-01 | blind | `F-cStat`, `F-prot` | Protocolo de producao em contexto de producao: F-cStat e F-prot, sem F-amb. | aprovado |
| UC03-AMBPROT-02 | dev | `F-cStat`, `F-prot`, `F-amb` | Protocolo de homologacao (tpAmb=2) num contexto de producao: F-prot (nProt) e F-amb. | aprovado |
| UC03-PRODOK-02 | dev | `F-cStat`, `F-prot` | Protocolo de producao em contexto de producao: F-cStat e F-prot, sem F-amb. | aprovado |

## UC04 — outros cStat

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| UC04-CSTAT-01 | validation | `F-cStat`, `F-prot` | Autorizado: F-cStat com o texto oficial e F-prot (nProt no XML comprova o desfecho). | aprovado |
| UC04-CSTAT-02 | dev | `F-cStat`, `F-prot` | Resultado com nProt: F-cStat com o texto oficial e F-prot (o XML comprova o desfecho). | aprovado |
| UC04-CSTAT-03 | dev | `F-cStat`, `F-prot` | Resultado com nProt: F-cStat com o texto oficial e F-prot (o XML comprova o desfecho). | aprovado |
| UC04-CSTAT-04 | blind | `F-cStat` | cStat sem regra local: F-cStat com o texto oficial do MOC 7.0 Anexo I. | aprovado |
| UC04-CSTAT-05 | blind | `F-cStat` | cStat sem regra local: F-cStat com o texto oficial do MOC 7.0 Anexo I. | aprovado |
| UC04-CSTAT-06 | validation | `F-cStat` | cStat sem regra local: F-cStat com o texto oficial do MOC 7.0 Anexo I. | aprovado |
| UC04-CSTAT-07 | dev | `F-cStat`, `F-prot` | Resultado com nProt: F-cStat com o texto oficial e F-prot (o XML comprova o desfecho). | aprovado |
| UC04-CSTAT-08 | blind | `F-cStat`, `F-prot` | Resultado com nProt: F-cStat com o texto oficial e F-prot (o XML comprova o desfecho). | aprovado |
| UC04-CSTAT-09 | blind | `F-cStat`, `G-539` | 539 sem linha do tempo: nao ha como sustentar H539, lacuna G-539. | aprovado |
| UC04-CSTAT-10 | blind | `F-cStat` | cStat sem regra local: F-cStat com o texto oficial do MOC 7.0 Anexo I. | aprovado |
| UC04-COMTO-01 | dev | `F-cStat`, `F-prot` | Ha retorno com nProt: F-prot; o timeout nao vira H-timeout (o XML e' a resposta). | aprovado |
| UC04-COMTO-02 | blind | `F-cStat`, `F-prot` | Ha retorno com nProt (denegacao): F-prot; o timeout nao vira H-timeout. | aprovado |
| UC04-COMTO-03 | dev | `F-cStat`, `F-prot` | Ha retorno com nProt: F-prot; o timeout nao vira H-timeout. | aprovado |
| UC04-COMTO-04 | dev | `F-cStat` | Ha retorno do autorizador: o timeout nao vira H-timeout. | aprovado |
| UC04-COMTO-05 | dev | `F-cStat` | Ha retorno do autorizador: o timeout nao vira H-timeout. | aprovado |
| UC04-RECI-01 | dev | `F-cStat`, `G-539` | Protocolo unico dentro de retConsReciNFe: cStat 539 lido do protocolo; sem eventos, G-539. | aprovado |
| UC04-539H-01 | blind | `F-cStat`, `H539` | 539 com envio de outra chave do MESMO numero, timeout e reenvio: H539 (chave regerada). | aprovado |
| UC04-539H-02 | blind | `F-cStat`, `H539` | 539 com envio de outra chave do MESMO numero, timeout e reenvio: H539 (chave regerada). | aprovado |
| UC04-539H-03 | blind | `F-cStat`, `H539` | 539 com envio de outra chave do MESMO numero, timeout e reenvio: H539 (chave regerada). | aprovado |
| UC04-539G-01 | dev | `F-cStat`, `G-539` | Envio anterior e' de outro numero: nao e' a mesma nota, G-539. | aprovado |

## UC05 — lote e nfeProc

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| UC05-LOTE-01 | dev | `G-lote` | retConsReciNFe com 2 protocolos: nenhum cStat e' escolhido (G-lote). | aprovado |
| UC05-LOTE-02 | validation | `G-lote` | retConsReciNFe com 2 protocolos: nenhum cStat e' escolhido (G-lote). | aprovado |
| UC05-LOTE-03 | dev | `G-lote`, `F-timeout`, `H-timeout` | Lote ambiguo (G-lote) e timeout sem resposta da chave: H-timeout. | aprovado |
| UC05-PROC-01 | dev | `F-cStat`, `F-prot` | nfeProc com NF-e assinada e protocolo 100 da mesma chave: F-cStat e F-prot. | aprovado |
| UC05-PROCKEY-01 | dev | `F-protkey`, `F-cStat` | nfeProc cujo protocolo e' de outra chave: F-protkey, sem F-prot (o protocolo nao prova esta nota). | aprovado |
| UC05-PROC-02 | blind | `F-cStat`, `F-prot` | nfeProc com NF-e assinada e protocolo 100 da mesma chave: F-cStat e F-prot. | aprovado |
| UC05-PROCKEY-02 | dev | `F-protkey`, `F-cStat` | nfeProc cujo protocolo e' de outra chave: F-protkey, sem F-prot (o protocolo nao prova esta nota). | aprovado |

## UC06 — assinatura XMLDSig

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| UC06-SIG-01 | validation | `F-sig` | SignatureValue adulterado, digest intacto: F-sig (signature_mismatch). | aprovado |
| UC06-SIG-02 | blind | `F-xsd`, `G-sig` | rsa-md5 nao e' aceito pelo XSD nem pelo perfil: F-xsd + G-sig. | aprovado |
| UC06-SIG-03 | blind | `G-sig` | Reference aponta para outro Id: G-sig (reference_uri); o XSD nao restringe o URI. | aprovado |
| UC06-SIG-04 | dev | `F-xsd`, `G-sig` | Transform XPath extra: XSD reprova a quantidade e o perfil recusa: F-xsd + G-sig. | aprovado |
| UC06-SIG-05 | validation | — | Comentario fora do infNFe nao altera o conteudo assinado: nenhum fato de erro. | aprovado |
| UC06-SIG-06 | blind | `F-sigcnpj` | Assinatura confere, mas o CNPJ ICP-Brasil do certificado difere do emitente: F-sigcnpj. | aprovado |
| UC06-SIG-07 | dev | `F-sigvalid` | Assinatura confere, mas dhEmi esta fora da validade do certificado: F-sigvalid. | aprovado |
| UC06-SIG-08 | dev | `F-cStat`, `F-prot`, `F-xsd`, `G-sig` | Signature vazia num protocolo sem Id: F-xsd e G-sig (signed_element_without_id); o nProt ainda da F-prot. | aprovado |
| UC06-SIG-09 | blind | `F-xsd` | NF-e sem Signature (obrigatoria no TNFe): F-xsd; assinatura not_executed. | aprovado |

## SEC — segurança da entrada

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| SEC-XXE-01 | dev | `G-xml` | DTD com entidade externa: XML recusado antes da analise (G-xml). | aprovado |
| SEC-MAL-01 | dev | `G-xml` | XML malformado: G-xml. | aprovado |
| SEC-BOMB-01 | validation | `G-xml` | Expansao de entidades (DTD interna): G-xml. | aprovado |
| SEC-DTDEXT-01 | dev | `G-xml` | DOCTYPE com DTD remota: G-xml, sem acesso a rede. | aprovado |
| SEC-XSI-01 | dev | `F-cStat`, `F-prot` | xsi:schemaLocation remoto e' ignorado; validacao so com o pacote local: F-cStat e F-prot. | aprovado |
| SEC-NS-01 | dev | `G-ns` | protNFe sem o namespace da NF-e nao e' tratado como fiscal: lacuna G-ns. | aprovado |
| SEC-XINC-01 | dev | `G-ns` | XInclude nao e' processado; XML nao fiscal: lacuna G-ns. | aprovado |

## GAP — lacunas de entrada

| Caso | Split | Esperado | Justificativa | Status |
|---|---|---|---|---|
| GAP-DV-01 | blind | `F-cStat`, `F-dv` | Digito verificador errado: F-dv; a regra 204 exige chave valida (sem F-204). | aprovado |
| GAP-TZ-01 | blind | `G-noxml`, `G-events` | Horario sem fuso e' ambiguo: linha do tempo recusada (G-events). | aprovado |
| GAP-CUF-01 | dev | `F-cStat`, `F-dv` | cUF 99 nao existe (DV recalculado): F-dv. | aprovado |
| GAP-REF-01 | dev | `G-noxml`, `G-ref` | Sem XML e eventos de duas chaves: nao ha documento de referencia (G-ref). | aprovado |
| GAP-EVT-01 | blind | `G-noxml`, `G-events` | Tipo de evento fora do contrato: G-events. | aprovado |
| GAP-EVT-02 | dev | `G-noxml`, `G-events` | Evento sem document_ref: G-events. | aprovado |
| GAP-EVT-03 | validation | `G-noxml`, `G-events` | Horario ilegivel: G-events (antes do v1 virava excecao e derrubava o plugin). | aprovado |
| GAP-EVT-04 | validation | `G-noxml`, `G-events` | Horario numerico (epoch) nao e' ISO 8601: G-events. | aprovado |
| GAP-VAZIO-01 | dev | `G-noxml` | Payload vazio equivale a sem XML: G-noxml. | aprovado |
