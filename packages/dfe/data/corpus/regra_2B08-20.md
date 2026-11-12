---
source_id: NT_2018.005_v1.51
document_type: regra_validacao
rule_id: 2B08-20
cstat: "204"
model: ["55", "65"]
layout_version: "4.00"
environment: [homologation, production]
jurisdiction: nacional
authority_class: oficial_gov
status: vigente_nao_confirmada_em_producao
reviewed_by: null
---

# Regra 2B08-20 — cStat 204 (Rejeição: Duplicidade de NF-e)

**Fonte:** NT 2018.005 v1.51 (março/2025), seção 4.5 "Protocolo de
Autorização na Rejeição por Duplicidade", páginas 22–23; a mesma regra consta
no MOC 7.0 Anexo I (regras de validação).

**O que a regra verifica (paráfrase):** acesso à base de NF-e pela chave
composta de modelo, UF, CNPJ/CPF do emitente, série e número. Se já existir
documento com essa chave, o autorizador rejeita com cStat 204. A NT 2018.005
alterou a regra para que a rejeição por duplicidade informe dados do
protocolo do documento já existente.

**Leitura para diagnóstico:** o 204 indica que **um documento com a mesma
chave já foi recebido**. Não diz, sozinho, se a primeira tentativa foi
autorizada nem por que houve nova tentativa. Antes de qualquer reenvio,
consultar a situação do documento por leitura autorizada.

**Causas típicas a investigar (hipóteses, não fatos):** reenvio após timeout
sem resposta; retransmissão automática da mensageria; envio manual
concorrente; divergência de estado entre mensageria e backend.
