# RFC Gateway - Pool de Processos de Dialogo Esgotado

## Sintoma
Chamada RFC sincrona (via destino RFC configurado em SM59) falha com
`SYSTEM_FAILURE` apos um timeout, tipicamente na faixa de 60 segundos,
sem mensagem de erro especifica de aplicacao.

## Causas comuns
- `gwy/max_conn` ou `rdisp/wp_no_dia` do sistema de destino esgotado
  por pico de carga batch concorrente com a chamada RFC sincrona
- Muito comum em paisagens ECC on-premise sem escalonamento
  automatico de processos de trabalho, onde jobs batch noturnos ou de
  fim de mes competem pelo mesmo pool de processos de dialogo usado
  por integracoes sincronas
- Diferente de "Connection refused" (destino totalmente inacessivel):
  aqui o destino esta no ar, mas sem processo de dialogo livre para
  atender a chamada dentro do timeout

## Diagnostico
1. No sistema de destino, verificar SM50/SM66 no horario do incidente
   em busca de processos de dialogo ocupados por jobs de longa duracao
2. Conferir se o horario do incidente coincide com uma janela de
   batch/fechamento conhecida
3. Verificar `gwy/max_conn` e o numero de processos de dialogo
   configurados (RZ10/instance profile) contra o pico real de uso

## Resolucao tipica
Redistribuir jobs batch para fora da janela de uso sincrono critico,
aumentar o numero de processos de dialogo reservados para integracao,
ou migrar a integracao sincrona para um padrao assincrono (fila) que
tolere picos de carga sem falhar a chamada.
