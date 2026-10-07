# OData - Mudanca de contrato e metadados

## Sintoma
Um servico OData que funcionava passa a devolver erro depois de uma
atualizacao no SAP (patch, upgrade, novo servico publicado).
Relatos tipicos: "servico OData comecou a dar erro 404/500",
"$metadata mudou", "propriedade removida do servico", "campo nao
existe mais no retorno", "cliente quebra apos release". O sintoma
costuma ser de schema, nao de dados: o servico responde, mas com uma
estrutura diferente da que o consumidor esperava.

## Causas comuns
- Mudanca breaking no contrato: propriedade ou entidade removida/
  renomeada, tipo alterado, ou navegacao que deixou de existir
  apos um patch/upgrade do SAP
- Metadados ($metadata / EDMX) republicados com namespace ou versao
  diferentes, quebrando o consumidor que valida o schema
- O consumidor ainda usa a versao antiga do proxy/servico enquanto o
  backend ja foi atualizado (dessincronia de release)
- Campo novo obrigatorio no backend, rejeitado pelo consumidor antigo

## Diagnostico
1. Ler o $metadata ($metadata ou .xml) do servico e comparar com a
   versao que o consumidor espera
2. Classificar a mudanca: breaking (propriedade removida ou
   RENOMEADA, tipo alterado, MaxLength novo ou menor, propriedade que
   passou a ser obrigatoria) exige adaptar o consumidor - para quem le
   o servico, renomear tem o mesmo efeito de remover. Additive
   (propriedade nova opcional) e cosmetic (anotacao, namespace,
   versao) costumam ser inofensivas
3. Verificar a proveniencia (versao do servico, data de republicacao)
   para separar mudanca real de ruido de namespace/versao
4. Se o erro for 404 em propriedade, o path do recurso mudou; se for
   500, pode ser validacao do backend sobre dado obrigatorio

## Resolucao tipica
Para mudanca breaking, adaptar o consumidor (ou o mapper do
middleware) e subir em conjunto, em vez de corrigir caso a caso. Para
mudanca aditiva/cosmetic, em geral nenhuma acao e necessaria - mas
confirme antes que o consumidor ignora propriedades desconhecidas
(alguns proxies gerados validam o schema estritamente). Quando a
mudanca for esperada e planejada, agendar a atualizacao do consumidor junto do patch do SAP
para evitar janela de incompatibilidade.
