# SAP Ariba - Pedido de Compra Bloqueado por Divergencia de Fornecedor

## Sintoma
Um pedido de compra (PO) enviado do S/4HANA para a Ariba Network fica
com status de falha/bloqueado na rede, sem chegar ao fornecedor - o
comprador ve o PO como "enviado" no S/4HANA, mas o fornecedor nunca o
recebe na Ariba Network.

## Causas comuns
- O ANID (Ariba Network ID) do fornecedor cadastrado no S/4HANA
  (LFA1/mestre de fornecedor) esta desatualizado ou incorreto em
  relacao ao ANID real do fornecedor na rede
- Replicacao de dados mestre de fornecedor entre o S/4HANA e a Ariba
  Network esta atrasada ou falhou silenciosamente (job de sincronizacao
  de mestre de fornecedor parado ha varios dias)
- Fornecedor trocou de conta/ANID na Ariba Network (fusao, migracao de
  conta) sem que o time de compras atualizasse o cadastro no S/4HANA

## Diagnostico
1. Confirmar no monitor de integracao da Ariba Network o codigo de
   erro exato retornado para o PO (normalmente indica divergencia de
   ANID/roteamento de fornecedor)
2. Comparar o ANID cadastrado no mestre de fornecedor do S/4HANA com o
   ANID ativo do fornecedor na rede Ariba
3. Verificar a data da ultima sincronizacao bem-sucedida de dados
   mestre de fornecedor entre os dois sistemas - um gap de varios dias
   e forte indicio de job de sincronizacao parado, nao um erro pontual

## Resolucao tipica
Corrigir o ANID no mestre de fornecedor do S/4HANA (ou reativar o job
de sincronizacao de dados mestre) e reenviar o PO - antes de reenviar,
confirmar com o fornecedor qual conta/ANID esta realmente ativa para
evitar repetir o mesmo erro com um ANID diferente porem igualmente
desatualizado.
