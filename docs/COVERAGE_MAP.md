# Mapa de cobertura: produto x mecanismo (DA-58)

<!-- Arquivo GERADO por scripts/coverage_map.py --write. Nao edite a mao:
     o gate `connector_coverage` reprova se este texto divergir do calculado. -->

> **A coluna de capacidade e' afirmacao, nao medicao.** A matriz de produto foi
> transcrita de uma fonte de referencia **sem citacao publicada e sem release SAP**
> (ver o cabecalho de `data/sap_products.yaml`). A coluna de cobertura e' fato sobre
> este repositorio e e' verificada por gate. Nao cite este mapa como fato de produto
> SAP, e nao confunda as duas colunas.

## Como ler

Dois eixos independentes, e nao uma tabela de ticks unica:

- **Capacidade** (`Tabela A`) e' afirmacao sobre o PRODUTO SAP. Vem de
  `data/sap_products.yaml`, transcrita de uma fonte de referencia **sem
  citacao publicada e sem release SAP**. Nao e' medicao nossa.
- **Cobertura** (`Tabela B`) e' fato sobre o REPOSITORIO: existe codigo
  que fala com este par. E' verificavel, e por isso a unica coluna em que
  este projeto pode afirmar sem ressalva.

Legenda de cobertura: `D` dedicado (conector feito para o produto) |
`G` generico (cliente de mecanismo, **nao validado** contra este produto) |
`·` sem cobertura.

`G` nao e' sinonimo de 'funciona'. O `ODataConnector` e' `G` para 15
produtos e nao foi validado contra nenhum deles — `docs/ARCHITECTURE.md` e'
quem diz o que foi validado contra sistema real.

## Tabela A — capacidade por produto (afirmacao da fonte)

| Produto | APIs | Events | IDoc | RFC | SOAP | OData | Files | Data Integration | Integration Suite |
|---|---|---|---|---|---|---|---|---|---|
| S/4HANA | ✓✓ | ✓✓ | ✓✓ | ✓✓ | ✓✓ | ✓✓ | ✓✓ | ✓✓ | ✓✓ |
| ECC | ✓ | lim | ✓✓ | ✓✓ | ✓✓ | ✓ | ✓✓ | ✓✓ | ✓✓ |
| SuccessFactors | ✓ | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Ariba | ✓ | ✓ | — | — | — | — | ✓ | — | ✓ |
| Business Network | ✓ | ✓ | — | — | — | — | ✓ | — | ✓ |
| Concur | ✓ | ✓/cen | — | — | ✓/cen | — | ✓ | — | ✓ |
| Fieldglass | ✓ | ✓/cen | — | — | ✓/cen | — | ✓ | — | ✓ |
| IBP | ✓ | ✓/cen | — | — | — | ✓ | ✓ | ✓ | ✓ |
| EWM | ✓ | ✓ | ✓✓ | ✓✓ | ✓ | ✓ | ✓ | — | ✓ |
| TM | ✓ | ✓ | ✓✓ | ✓✓ | ✓ | ✓ | ✓ | — | ✓ |
| GTS | ✓ | ✓/cen | ✓✓ | ✓✓ | ✓ | ✓ | ✓ | — | ✓ |
| Commerce | ✓ | ✓ | — | — | ✓/cen | ✓ | ✓ | — | ✓ |
| Sales/Service Cloud | ✓ | ✓ | — | — | ✓ | ✓ | ✓ | — | ✓ |
| Datasphere | ✓ | — | — | — | — | ✓ | ✓ | ✓✓ | ✓ |
| BW/4HANA | ✓ | — | — | — | — | ✓ | ✓ | ✓✓ | ✓ |
| SAC | ✓ | — | — | — | — | ✓ | ✓ | ✓ | ✓ |
| CAP | ✓ | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| RAP | ✓ | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kyma | ✓ | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Build Process Automation | ✓ | ✓ | — | — | — | — | ✓ | — | ✓ |
| Joule / Agents | ✓ | ✓ | — | — | — | — | — | — | ✓ |
| AI Core / GenAI Hub | ✓ | — | — | — | — | — | — | ✓ | ✓ |
| Process Orchestration / PI | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| API Management | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| ServiceNow | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| Salesforce | ? | ? | ? | ? | ? | ? | ? | ? | ? |
| Workday | ? | ? | ? | ? | ? | ? | ? | ? | ? |

## Tabela B — cobertura do repositorio por par (fato do codigo)

| Produto | APIs | Events | IDoc | RFC | SOAP | OData | Files | Data Integration | Integration Suite |
|---|---|---|---|---|---|---|---|---|---|
| S/4HANA | G | · | G | G | G | G | · | · | · |
| ECC | G | · | G | G | G | G | · | · | · |
| SuccessFactors | D | · | · | · | G | D | · | · | · |
| Ariba | D | · | · | · | · | · | D | · | · |
| Business Network | D | · | · | · | · | · | D | · | · |
| Concur | · | · | · | · | · | · | · | · | · |
| Fieldglass | · | · | · | · | · | · | · | · | · |
| IBP | G | · | · | · | G | G | · | · | · |
| EWM | G | · | G | G | G | G | · | · | · |
| TM | G | · | G | G | G | G | · | · | · |
| GTS | G | · | G | G | G | G | · | · | · |
| Commerce | G | · | · | · | G | G | · | · | · |
| Sales/Service Cloud | G | · | · | · | G | G | · | · | · |
| Datasphere | G | · | · | · | G | G | · | · | · |
| BW/4HANA | G | · | · | · | G | G | · | · | · |
| SAC | G | · | · | · | G | G | · | · | · |
| CAP | D | · | · | · | G | D | · | · | · |
| RAP | G | · | · | · | G | G | · | · | · |
| Kyma | G | · | · | · | G | G | · | · | · |
| Build Process Automation | · | · | · | · | · | · | · | · | · |
| Joule / Agents | · | · | · | · | · | · | · | · | · |
| AI Core / GenAI Hub | · | · | · | · | · | · | · | · | · |
| Process Orchestration / PI | D | · | · | · | · | · | · | · | · |
| API Management | D | · | · | · | · | · | · | · | · |
| ServiceNow | D | · | · | · | · | · | · | · | · |
| Salesforce | D | · | · | · | · | · | · | · | · |
| Workday | D | · | · | · | · | · | · | · | · |

## Resumo por produto

| Produto | Categoria | Conectores dedicados | Genericos | Disponiveis | Cobertos | Lacunas |
|---|---|---|---|---|---|---|
| S/4HANA | sap_product | — | odata, rfc | 9 | 5 | Events, Files, Data Integration, Integration Suite |
| ECC | sap_product | — | odata, rfc | 9 | 5 | Events, Files, Data Integration, Integration Suite |
| SuccessFactors | sap_product | successfactors | odata | 7 | 3 | Events, Files, Data Integration, Integration Suite |
| Ariba | sap_product | ariba | — | 4 | 2 | Events, Integration Suite |
| Business Network | sap_product | ariba | — | 4 | 2 | Events, Integration Suite |
| Concur | sap_product | — | — | 5 | 0 | APIs, Events, SOAP, Files, Integration Suite |
| Fieldglass | sap_product | — | — | 5 | 0 | APIs, Events, SOAP, Files, Integration Suite |
| IBP | sap_product | — | odata | 6 | 2 | Events, Files, Data Integration, Integration Suite |
| EWM | sap_product | — | odata, rfc | 8 | 5 | Events, Files, Integration Suite |
| TM | sap_product | — | odata, rfc | 8 | 5 | Events, Files, Integration Suite |
| GTS | sap_product | — | odata, rfc | 8 | 5 | Events, Files, Integration Suite |
| Commerce | sap_product | — | odata | 6 | 3 | Events, Files, Integration Suite |
| Sales/Service Cloud | sap_product | — | odata | 6 | 3 | Events, Files, Integration Suite |
| Datasphere | sap_product | — | odata | 5 | 2 | Files, Data Integration, Integration Suite |
| BW/4HANA | sap_product | — | odata | 5 | 2 | Files, Data Integration, Integration Suite |
| SAC | sap_product | — | odata | 5 | 2 | Files, Data Integration, Integration Suite |
| CAP | sap_product | cap | odata | 7 | 3 | Events, Files, Data Integration, Integration Suite |
| RAP | sap_product | — | odata | 7 | 3 | Events, Files, Data Integration, Integration Suite |
| Kyma | sap_product | — | odata | 7 | 3 | Events, Files, Data Integration, Integration Suite |
| Build Process Automation | sap_product | — | — | 4 | 0 | APIs, Events, Files, Integration Suite |
| Joule / Agents | sap_product | — | — | 3 | 0 | APIs, Events, Integration Suite |
| AI Core / GenAI Hub | sap_product | — | — | 3 | 0 | APIs, Data Integration, Integration Suite |
| Process Orchestration / PI | sap_middleware | po | — | 0 | 0 | — |
| API Management | sap_middleware | apim | — | 0 | 0 | — |
| ServiceNow | non_sap | servicenow | — | 0 | 0 | — |
| Salesforce | non_sap | salesforce | — | 0 | 0 | — |
| Workday | non_sap | workday | — | 0 | 0 | — |

## Filas com lacuna

Os mecanismos listados sao os que o produto expoe e que nada no repositorio alcanca. E' a fila de trabalho, e nao um defeito — por isso o gate `connector_coverage` reprova por incoerencia, nunca por falta aqui.

| Produto | Conectores dedicados | Mecanismos sem cobertura |
|---|---|---|
| S/4HANA | — | Events, Files, Data Integration, Integration Suite |
| ECC | — | Events, Files, Data Integration, Integration Suite |
| SuccessFactors | successfactors | Events, Files, Data Integration, Integration Suite |
| Ariba | ariba | Events, Integration Suite |
| Business Network | ariba | Events, Integration Suite |
| Concur | — | APIs, Events, SOAP, Files, Integration Suite |
| Fieldglass | — | APIs, Events, SOAP, Files, Integration Suite |
| IBP | — | Events, Files, Data Integration, Integration Suite |
| EWM | — | Events, Files, Integration Suite |
| TM | — | Events, Files, Integration Suite |
| GTS | — | Events, Files, Integration Suite |
| Commerce | — | Events, Files, Integration Suite |
| Sales/Service Cloud | — | Events, Files, Integration Suite |
| Datasphere | — | Files, Data Integration, Integration Suite |
| BW/4HANA | — | Files, Data Integration, Integration Suite |
| SAC | — | Files, Data Integration, Integration Suite |
| CAP | cap | Events, Files, Data Integration, Integration Suite |
| RAP | — | Events, Files, Data Integration, Integration Suite |
| Kyma | — | Events, Files, Data Integration, Integration Suite |
| Build Process Automation | — | APIs, Events, Files, Integration Suite |
| Joule / Agents | — | APIs, Events, Integration Suite |
| AI Core / GenAI Hub | — | APIs, Data Integration, Integration Suite |

## Linhas sem dado de capacidade

Estas linhas existem porque o repo tem conector, mas a fonte nao as cobre. Todos os mecanismos marcados `?` na Tabela A sao **desconhecidos, nao negativos**: converter `?` em `—` aqui seria inventar uma negativa. Preencher isso e' trabalho de fonte, nao de codigo.

- **Process Orchestration / PI** (sap_middleware)
- **API Management** (sap_middleware)
- **ServiceNow** (non_sap)
- **Salesforce** (non_sap)
- **Workday** (non_sap)
