# ServiceNow - Alerta de Monitoramento sobre Falha SAP

## Sintoma
Um incidente e aberto automaticamente no ServiceNow (via integracao de
monitoramento/observabilidade) referenciando um CI do tipo "SAP ECC"
ou "SAP S/4HANA", antes de qualquer chamado ser aberto diretamente no
lado SAP - tipicamente indicando indisponibilidade de destino RFC,
falha de iFlow CPI, ou job batch travado detectado por um agente de
monitoramento externo ao SAP.

## Causas comuns
- Ferramentas de observabilidade (ex: monitoramento de infraestrutura,
  APM) correlacionam sintomas de rede/host com o CMDB e abrem
  incidente automaticamente no ServiceNow, mesmo quando a causa raiz
  esta no lado SAP
- Prioridade do incidente ServiceNow (ex: "1 - Critical") normalmente
  reflete o SLA do processo de negocio afetado (ex: folha de
  pagamento, faturamento), nao a complexidade tecnica do problema

## Diagnostico
1. Usar o `cmdb_ci` e a `short_description` do incidente ServiceNow
   para identificar qual interface/sistema SAP especifico esta
   envolvido
2. Cruzar o horario do incidente ServiceNow com logs/status do lado
   SAP (SM59, SLG1, monitor de IDoc, MPL do CPI) para confirmar a
   causa raiz tecnica
3. Tratar o incidente ServiceNow como o "sinal de negocio" (impacto,
   prioridade, SLA) e os dados do lado SAP como a "causa raiz tecnica"
   - as duas fontes se complementam, nao competem

## Resolucao tipica
Corrigir a causa raiz do lado SAP identificada no diagnostico, e
atualizar/fechar o incidente ServiceNow com a causa raiz encontrada -
mantendo rastreabilidade entre o sinal de negocio (ITSM) e a correcao
tecnica (SAP), util tanto para o encerramento do incidente quanto para
analise de tendencia futura.
