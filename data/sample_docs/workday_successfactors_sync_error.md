# Workday - Falha na Replicacao de Dados vindos do SuccessFactors

## Sintoma
Um evento de integracao falha no Workday ao tentar replicar uma
mudanca de dados de funcionario (contratacao, mudanca de cargo,
desligamento) originada no SAP SuccessFactors Employee Central,
tipicamente em cenarios onde o SuccessFactors e o sistema mestre de RH
central mas o Workday e usado por uma unidade de negocio especifica
(ex: pos-aquisicao, joint venture, ou modelo hibrido de HRIS).

## Causas comuns
- `Person_ID_External` (ou identificador equivalente) enviado pelo
  SuccessFactors nao corresponde a nenhum Worker existente no Workday
  - tipico em contratacoes novas quando a ordem de criacao entre os
  dois sistemas nao esta sincronizada
- Job Profile / Position enviado pelo SuccessFactors nao tem
  equivalente mapeado no Workday (taxonomias de cargo divergentes
  entre os dois sistemas)
- Evento de integracao dependia de um evento anterior que ainda nao
  foi processado (ordem de eventos fora de sequencia, comum quando o
  middleware nao garante ordenacao estrita por funcionario)

## Diagnostico
1. Verificar no log do evento de integracao Workday qual campo/
   referencia falhou exatamente (mensagem de erro costuma citar o
   External ID ou o codigo de mapeamento ausente)
2. Confirmar no SuccessFactors se o registro de origem existe e esta
   completo (Employee Central Assignment/Job Information)
3. Verificar se ha eventos de integracao anteriores para o mesmo
   funcionario ainda pendentes/com erro - processar fora de ordem e
   uma causa frequente de falha em cascata

## Resolucao tipica
Corrigir o dado de origem no SuccessFactors (ou o mapeamento de
taxonomia no middleware) e reprocessar o evento de integracao
especifico no Workday - reprocessar em lote sem identificar a causa
raiz especifica tende a repetir a mesma falha para todos os
funcionarios com o mesmo problema de mapeamento.
