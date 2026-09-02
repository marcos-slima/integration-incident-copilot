# Salesforce - Case sobre Falha Silenciosa na Sincronizacao com SAP

## Sintoma
Um Case e aberto no Salesforce Service Cloud (tipicamente pelo time de
vendas ou pelo proprio cliente via portal) relatando que um pedido/
oportunidade criado no Salesforce nao aparece no SAP (SD/S4HANA) apos
o prazo esperado, sem nenhum erro visivel para o usuario de negocio -
a integracao falhou de forma "silenciosa" do ponto de vista do
Salesforce.

## Causas comuns
- Middleware de integracao (CPI, MuleSoft, ou similar) processou a
  mensagem mas ela caiu em uma fila de erro (Dead Letter) sem
  notificar o lado Salesforce
- Mapeamento de campo obrigatorio no SAP (ex: centro, organizacao de
  vendas) nao veio preenchido corretamente pelo objeto Salesforce de
  origem
- Erro de autenticacao/token expirado no conector do middleware para o
  SAP, que nao gera erro visivel no Salesforce (a chamada do
  Salesforce para o middleware teve sucesso; o middleware que falhou
  no proximo salto)

## Diagnostico
1. Confirmar no middleware de integracao (nao no Salesforce) se a
   mensagem correspondente ao Case chegou e qual foi o status
   real (sucesso/erro/fila de retry)
2. Se a mensagem nunca chegou ao middleware, o problema esta no
   trigger/fluxo de automacao dentro do proprio Salesforce (Flow,
   Process Builder, Apex trigger) que deveria ter disparado o envio
3. Se a mensagem chegou ao middleware mas falhou no salto para o SAP,
   tratar como um incidente de integracao SAP normal (ver conectores
   OData/RFC) - o Case Salesforce e o "sinal de negocio" (SLA de
   atendimento ao cliente), a causa raiz tecnica esta no SAP ou no
   middleware

## Resolucao tipica
Reprocessar a mensagem presa (retry manual ou automatico no
middleware) apos corrigir a causa raiz tecnica, e atualizar o Case no
Salesforce com o motivo real da demora - o time de vendas normalmente
so precisa saber "quando vai ser resolvido" e "por que aconteceu", nao
o detalhe tecnico da integracao.
