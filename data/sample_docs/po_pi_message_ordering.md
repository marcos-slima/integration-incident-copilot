# SAP PO/PI - Ordem de mensagens e atomicidade de canal

## Sintoma
Mensagens no SAP PO/PI chegam fora de ordem ou parcialmente
aplicadas: o sistema de destino fica inconsistente (parte do lote
gravada, parte nao), e o usuario relata "duplicidade de registro",
"registro criado duas vezes", "ordem de mensagens", "processamento
parcial", "IDoc gravado parcialmente". O sintoma aparece
tipicamente em lote/IDoc, nao em mensagem unitaria.

## Causas comuns
- Processamento paralelo das mensagens no PO/PI: sem chave de
  idempotencia no consumidor, a reentrega da MESMA mensagem (retry
  apos timeout) grava o mesmo registro de novo
- Ausencia de transacao/atomicidade no receptor: falha no meio do
  lote deixa estado parcial e o retry aplica o resto em cima
- Chave de negocio ausente no receptor: sem chave natural (numero do
  doc/IDoc), o sistema de destino nao consegue distinguir
  "atualizacao legitima" de "duplicata"
- Ordenacao nao garantida: um evento mais novo pode ser processado
  antes de um mais antigo em canais paralelos

## Diagnostico
1. Ler o log do canal no PO/PI e comparar o ID da mensagem entre as
   ocorrencias; mesmo ID = reentrega (mesma mensagem, nao ordem)
2. Verificar no receptor se existe chave natural de negocio usada
   para upsert/dedup; sem ela, toda reentrega vira registro novo
3. Conferir se o receptor processa o lote em transacao unica (evita
   estado parcial) e se a ordem e garantida pelo canal
4. Para o sintoma de duplicidade, cruzar com o doc de reentrega e
   idempotencia (duplicate_incident_idempotency.md)

## Resolucao tipica
Garantir idempotencia no receptor usando chave natural de negocio com
upsert (insert-if-not-exists/update-if-version) em vez de insert
cego, para que reentregas atualizem em vez de duplicar. Processar o
lote em transacao unica para evitar estado parcial. Se a ordem for
requisito real, usar canal/fila de uso unico (ao inves de canal com
processamento paralelo) para o trecho que depende de sequencia.
