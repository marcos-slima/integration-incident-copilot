# Servico CAP Customizado - Falha ao Processar Aprovacao de Pedido de Compra

## Sintoma
Um servico CAP customizado (extensao de aprovacao de pedido de
compra, side-by-side no BTP) rejeita requisicoes de um consumidor
externo com HTTP 422, ao inves de processar a aprovacao.

## Causas comuns
- Payload do consumidor externo nao inclui um campo obrigatorio do
  modelo CDS (ex: `approverId`), geralmente por desalinhamento entre
  o contrato OData v4 exposto pelo CAP e o que o consumidor espera
- Anotacao `@mandatory`/`@assert.range` no CDS mais restritiva do que
  o consumidor externo foi construido para respeitar
- Versao do modelo de dados evoluiu (novo campo obrigatorio) sem o
  consumidor externo ser atualizado junto

## Diagnostico
1. Verificar o corpo do erro OData v4 retornado (`error.code`,
   `error.message`) - CAP costuma expor a anotacao CDS que falhou
2. Comparar o payload enviado pelo consumidor com o metadata OData v4
   atual do servico (`$metadata`)
3. Checar se houve deploy recente do servico CAP que adicionou
   validacao nova

## Resolucao tipica
Tratar a validacao como regra de negocio, nao como obstaculo: se o
campo e de fato obrigatorio (ex: `approverId` numa aprovacao), o
consumidor externo e que precisa ser corrigido para envia-lo - afrouxar
`@mandatory`/`@assert.range` no CDS para "o erro sumir" deixa passar
aprovacoes sem aprovador e move o problema para auditoria.
Se a validacao nova quebrou consumidores que ainda nao podem ser
atualizados, publicar uma nova revisao do endpoint (ex:
`PurchaseOrderApprovalsV2`) com a regra nova e manter a anterior por um
periodo de transicao combinado, com data de desligamento - em vez de
remover a regra.
