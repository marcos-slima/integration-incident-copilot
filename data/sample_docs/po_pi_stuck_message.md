# SAP PO/PI - Mensagem travada no Monitor (RABAX/EOIO)

## Sintoma
Mensagem de integracao fica presa no SAP Process Orchestration /
Process Integration sem processar, e o usuario relata "mensagem
travada", "mensagem presa no PO", "EOIO stuck", "RABAX_STATE",
"mensagem nao processada no monitor". No Message Monitor a mensagem
aparece com erro de processamento ou fica em estado "in process" sem
avancar, e frequentemente o time suspects timeout do backend SAP.
A mensagem NAO aparece como concluida com sucesso.

## Causas comuns
- Timeout do canal (IDOC/RFC/SOAP) contra o backend SAP: o PO/PI
  considera o destinatario lento e trava a mensagem
- Regra de roteamento/canal apontando para sistema ou porta errados
  apos refresh de ambiente
- Credencial (usuario tecnico OAuth2 ou basica) expirada ou sem
  permissao no sistema de destino, gerando 401/403 no canal
- Mensagem em estado RABAX_TRANSFER/EOIO aguardando acknowledge do
  receptor, ou componente de orquestracao com internal error

## Diagnostico
1. Abrir o Message Monitor da interface (URL /pimon ou
   /mdt/api/1.0/facade para o Message Monitor REST) e filtrar por
   status da mensagem e pelo canal de destino
2. Conferir o log do canal no PO/PI (rastreamento de mensagem) para o
   erro real do destino (timeout, 401/403, 404 de rota)
3. Se o erro for de timeout, validar disponibilidade do sistema SAP de
   destino e a porta/host configurados no canal
4. Se for 401/403, renovar/atualizar a credencial e conferir escopos

## Resolucao tipica
Corrigir a causa subjacente e reprocessar a mensagem pelo Message
Monitor (botao de reprocessamento/Restart), em vez de criar mensagem
nova. Ajustar o timeout do canal quando o backend SAP e lento, ou
corrigir host/porta/credencial do canal. Reprocessar mantem o ID da
mensagem original, evitando criar incidente duplicado.
