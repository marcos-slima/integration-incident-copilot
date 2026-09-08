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
Corrigir a anotacao `@mandatory`/`@assert.range` no modelo CDS para
ficar menos restritiva, ou publicar uma nova revisao do endpoint
(ex: `PurchaseOrderApprovalsV2`) no deploy do CAP, para nao forcar
clientes ja integrados a se adaptarem de imediato as validacoes mais
rigidas do modelo de dados.
