# SAP AI Core vs. IA local/sob medida — comparação de custo

Este documento existe para uma conversa concreta com um cliente: por
que uma solução como o **SAP Integration Copilot** — rodando local ou
sobre infraestrutura já contratada pelo cliente — é uma alternativa
viável para quem não pode (técnica ou financeiramente) adotar o SAP AI
Core, sem abrir mão de IA real integrada ao SAP.

## O custo estrutural do SAP AI Core

O SAP AI Core cobra por consumo (créditos BTP, 1 crédito = 1 EUR), mas
o ponto central para uma PME ou empresa ainda em ECC on-premise não é
o preço por unidade — é a base fixa obrigatória:

| Item | Custo |
|---|---|
| SAP HANA Cloud (camada de dados **obrigatória** para o AI Core, independente do quanto de IA for usado) | €36.000 – €480.000/ano |
| Inferência GPU (NVIDIA A100) | €2,50 – €3,50/hora |
| Treinamento GPU | €3,50 – €5,00/hora |
| SAP AI Launchpad | €3.600 – €9.600/ano |
| Implantação típica de médio porte (3 modelos, ~500h CPU/mês, egress e API calls inclusos) | ~€98.400/ano |
| Faixa de orçamento realista ano 1 (médio porte, 3-5 modelos) | €130.000 – €270.000 |

Comparado a AWS SageMaker (~€0,81–1,36/h), Google Vertex AI
(~€1,15–1,93/h) e Azure AI Studio (~€0,83–1,47/h), a inferência GPU do
AI Core custa **85% a 220% mais**. Créditos "gratuitos" iniciais
expiram em 12 meses, convertendo para cobrança on-demand com prêmio de
20-30%. Contratos típicos exigem compromisso mínimo anual (ex:
€50.000/ano) com créditos não usados perdidos, sem reembolso.

*Fonte: [SAP AI Core & Launchpad Pricing 2026](https://saplicensingexperts.com/blog/sap-ai-core-launchpad-pricing-and-budget-planning).*

**Por que isso exclui estruturalmente boa parte da base SAP:** a
exigência de HANA Cloud como camada obrigatória pressupõe estar em
S/4HANA sobre BTP. Segundo Gartner/IDC, cerca de **40-45% da base de
clientes SAP ECC** deve permanecer em sistemas legados além de 2027
(entre 13 mil e 17 mil clientes, a depender do horizonte), muitas
vezes por anos — com custo de migração para S/4HANA variando de US$ 2
milhões a US$ 1 bilhão, dependendo da complexidade.

*Fonte: [Nearly half of SAP ECC customers may stick with legacy ERP beyond 2027 (CIO)](https://www.cio.com/article/4000543/nearly-half-of-sap-ecc-customers-may-stick-with-legacy-erp-beyond-2027.html).*

## O custo de uma alternativa local/sob medida

| Item | Custo |
|---|---|
| Hardware para modelos até ~30B parâmetros (classe usada neste projeto) | ~US$ 3.500 (investimento único) |
| Energia (operação contínua) | US$ 50 – 150/mês |
| Ponto de equilíbrio vs. API de nuvem, uso moderado | 6 a 12 meses |
| Limiar de volume acima do qual self-hosting compensa | ~50-100 milhões de tokens/mês |

*Fonte: [Cost of Running Local LLM: Break-Even Guide 2026](https://aisuperior.com/cost-of-running-local-llm/).*

O `SAP Integration Copilot` já roda nessa faixa hoje (Ollama +
Qwen2.5-Coder 32B local), como prova de conceito em produção, não como
projeção teórica.

## Onde este projeto se encaixa

O `app/llm/factory.py` (LLM Gateway) deste projeto não força a escolha
entre "100% local" ou "100% nuvem": o mesmo grafo de diagnóstico roda
sobre Ollama local (default, sem custo de API), ou sobre uma
assinatura OpenAI/Azure OpenAI que o cliente já tenha — sem reescrever
código. Isso permite três conversas comerciais diferentes com o mesmo
produto técnico:

1. **Cliente sem orçamento/infra para SAP AI Core** (ECC on-premise,
   PME, ou apenas resistência a compromisso mínimo anual de dezenas de
   milhares de euros): solução roda 100% local, custo de
   infraestrutura na casa de milhares (não dezenas de milhares) de
   dólares, uma vez.
2. **Cliente que já tem OpenAI/Azure OpenAI contratado**: mesmo
   produto, apontado para o provedor existente, sem custo adicional de
   infraestrutura de IA.
3. **Cliente que está migrando para BTP/S4HANA e quer AI Core no
   futuro**: a interface de conector (`app/connectors/base.py`) e o
   LLM Gateway já foram desenhados para essa troca não exigir reescrever
   o grafo — o caminho de evolução existe, sem forçar o cliente a pagar
   por ele antes de precisar.

## Ressalvas

Os números de SAP AI Core acima vêm de um único agregador de
licenciamento (não da SAP oficialmente) e variam por contrato,
região e negociação — trate como ordem de grandeza para a conversa
inicial com o cliente, não como cotação. Sempre confirme o cenário de
preço atual com a SAP ou o parceiro de licenciamento antes de uma
proposta formal.
