# Duplicidade de incidente - reentrega de webhook e idempotencia

## Sintoma
O mesmo incidente e diagnosticado/criado mais de uma vez para um unico
evento de negocio. O usuario relata "duplicidade de incidente",
"mesmo erro apareceu duas vezes", "chegou o mesmo webhook de novo",
"criamos dois tickets iguais". A origem quase nunca e um bug no
sistema de origem: e o comportamento padrao de entrega
"at-least-once" de webhooks e event meshes.

## Causas comuns
- O provedor de eventos entrega "at-least-once": se nao recebe 2xx,
  reentrega o MESMO evento (mesmo id), as vezes por ~30 dias
- Endpoint lento: se o handler nao responde dentro da janela do
  provedor (tipicamente ~10s), ele reentrega. Endpoint lento gera o
  proprio retry e a propria duplicidade
- Chave de idempotencia ausente ou mal escolhida: deduplicar apenas
  por um campo de negocio (ex.: ConfirmationId) descarta metade dos
  eventos legitimos quando o provedor divide um pedido em varios
- Marcar o id comoprocessado ANTES de processar: se o processamento
  falhar depois, o retry e tratado como duplicado e o evento se perde
- Deduplicacao so em memoria: reinicio do processo zera o controle e
  o retry vira incidente novo

## Diagnostico
1. Comparar o ID do evento entre as duplicatas: mesmo id = reentrega
   legitima do provedor, NAO erro de regra de negocio
2. Medir o tempo de resposta do endpoint; acima da janela do provedor
   (~10s) o timeout esta auto-gerando retries
3. Verificar se a deduplicacao usa armazenamento duravel (banco/
   Redis com constraint unica), e nao um set em memoria
4. Conferir a chave de idempotencia: deve incluir o id do evento +
   tipo de evento (+ subtipo/sequencia quando o provedor divide)

## Resolucao tipica
Implementar idempotencia no consumidor: gravar a chave (event id +
tipo) em armazenamento duravel, devolver 200 ao descartar duplicata
(senao o provedor reentrega de novo) e registrar o id so APOS o
processamento bem-sucedido. Para repeticoes que carregam dados mais
novos, reconciliar (aplicar o payload mais recente) em vez de apenas
descartar. Reduzir o tempo de resposta do handler (ack rapido e
processamento em fila) para nao manufactureir retries.
