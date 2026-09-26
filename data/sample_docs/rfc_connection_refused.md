# RFC Destination - Connection Refused

## Sintoma
Chamada RFC (via SM59, ou de um sistema externo) falha com erro de
"Connection refused" ou "Partner not reached". Relato tipico do
usuario: "SM59 nao conecta", "SM59 dando erro de conexao recusada",
"conexao recusada no destino RFC", "ECONNREFUSED". A falha e
IMEDIATA (a conexao nem e estabelecida) - diferente do esgotamento do
pool de processos do gateway, em que a chamada conecta e falha so apos
um timeout (ver rfc_gateway_pool_timeout.md).

## Causas comuns
- Servico RFC/gateway do sistema de destino nao esta ativo
  (dispatcher parado, instancia em manutencao)
- Porta do gateway SAP bloqueada por firewall entre origem e destino
- Destino RFC configurado com host/instancia incorretos apos um
  refresh de ambiente

## Diagnostico
1. Testar a conexao diretamente em SM59 (Connection Test)
2. Verificar se o dispatcher do sistema de destino esta ativo
3. Confirmar regras de firewall/rede entre os hosts envolvidos

## Resolucao tipica
Corrigir o host/porta no destino RFC apos um refresh de ambiente,
ou acionar o time de infraestrutura para liberar a porta do gateway.
