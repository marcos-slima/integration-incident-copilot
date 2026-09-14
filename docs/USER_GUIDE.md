# Integration Incident Copilot — User Guide

> Versão 1.0 · Para arquitetos de integração, analistas de suporte e times de sustentação SAP

---

## O que é

O **Integration Incident Copilot** é um agente de diagnóstico de incidentes de integração. Dado um incidente — descrição em texto livre e, opcionalmente, dados estruturados de um sistema SAP ou multi-vendor — ele recupera o caso de troubleshooting mais relevante de uma base de conhecimento, analisa o contexto via LLM e retorna uma causa raiz provável com próximos passos concretos.

Não é uma ferramenta de correção automática. É um acelerador de diagnóstico — reduz o tempo gasto em triagem inicial de incidentes de integração, que em landscapes SAP complexos pode envolver múltiplos sistemas, logs distribuídos e conhecimento específico de protocolo (IDoc, RFC, OData, OAuth2).

---

## Para quem

- **Arquitetos de integração SAP** que precisam de um ponto de entrada rápido para incidentes de CPI/Integration Suite, iFlow, RFC e IDoc
- **Analistas de suporte N2/N3** que recebem chamados de integração sem contexto suficiente
- **Times de sustentação SAP** que mantêm landscapes heterogêneos (ECC, S/4HANA, SuccessFactors, sistemas não-SAP)
- **Desenvolvedores SAP BTP/CAP** que integram serviços via OData v4, Event Mesh e XSUAA

---

## Como funciona

O agente executa um pipeline de quatro etapas:

**1. Conector** — se um sistema de origem e identificador forem informados, o agente busca dados estruturados do incidente naquele sistema (código de erro, status, mensagem). Se não houver conector configurado, usa apenas a descrição textual.

**2. Recuperação (RAG híbrido)** — o texto do incidente é transformado em vetor (busca densa) e também em representação esparsa (BM25). Os dois resultados são fundidos via RRF (Reciprocal Rank Fusion), que garante que tanto a semântica quanto termos técnicos exatos (como `RFC_COMM_FAILURE` ou `status 51`) pesem na recuperação.

**3. Diagnóstico (LLM)** — o documento recuperado é usado como contexto para o modelo (`qwen3-coder-next`, 80B parâmetros, janela de 262K tokens). O modelo retorna causa raiz, confiança e próximos passos em formato estruturado.

**4. Guardrails** — a confiança é ajustada deterministicamente. Se o identificador não foi reconhecido pelo conector, a confiança é limitada a 0.4. Se nenhum documento foi recuperado com score suficiente, é limitada a 0.3. Isso evita que o agente retorne respostas confiantes quando o contexto é insuficiente.

---

## Como usar

### Via interface web

Acesse `http://localhost:8000` após iniciar o servidor. A interface tem três telas:

**Diagnóstico** — formulário principal com três campos:
- Descrição do incidente (obrigatório)
- Sistema de origem (dropdown — OData, RFC, ServiceNow, Salesforce, CAP, etc.)
- Identificador do incidente (opcional, mas melhora muito a precisão)

**Histórico** — lista dos diagnósticos realizados na sessão atual, com confiança e causa raiz resumida.

**Status da stack** — estado dos conectores (quais têm credencial real configurada, quais estão em modo demo/mock) e da infraestrutura (LLM, RAG, GraphRAG, A2A).

### Via API REST

```bash
curl -X POST http://localhost:8000/diagnose \
  -H "Content-Type: application/json" \
  -d '{
    "description": "IDoc travado com status 51 no sistema de destino",
    "interface_type": "rfc",
    "identifier": "RFC-IDOC-51-DEMO"
  }'
```

Resposta:

```json
{
  "matched_source": "idoc_status_51.md",
  "confidence": 0.95,
  "probable_root_cause": "O material 4711 não está cadastrado no centro 1000...",
  "next_steps": [
    "Verificar existência do material 4711 via MM03",
    "..."
  ]
}
```

A documentação interativa da API está disponível em `http://localhost:8000/docs` (Swagger UI).

### Via linha de comando

```bash
uv run python -m app.agent.graph \
  --interface rfc \
  --id RFC-IDOC-51-DEMO \
  "IDoc travado com status 51"
```

---

## Como construir um bom prompt

A qualidade do diagnóstico depende diretamente da qualidade da descrição do incidente. O agente faz busca vetorial — quanto mais contexto técnico específico você fornecer, mais precisa será a recuperação do caso relevante.

### Princípio geral

> Descreva o incidente como você descreveria para um colega especialista em integração SAP que não tem acesso ao sistema no momento — com o erro exato, o sistema envolvido, e o que você já tentou.

### O que incluir

**Código de erro ou status** — é o sinal mais forte para a recuperação. Se você tem um código, coloque-o na descrição, mesmo que já esteja no identificador.

Ruim:
> "iFlow não está funcionando"

Bom:
> "iFlow retornando HTTP 401 ao tentar autenticar via OAuth2 no SAP Integration Suite"

---

**Nome do protocolo ou adaptador** — RFC, IDoc, OData, SOAP, REST, AS2. O agente tem documentos específicos por protocolo.

Ruim:
> "Erro de integração entre ECC e S/4HANA"

Bom:
> "Falha de comunicação RFC entre ECC 6.0 e S/4HANA — destino SM59 retornando connection refused na porta 3300"

---

**Status ou código específico do sistema** — para IDocs, o status numérico é crítico (51, 02, 53). Para OData, o HTTP status code. Para RFC, o código de exceção (RFC_COMM_FAILURE, SYSTEM_FAILURE).

Ruim:
> "IDoc com problema"

Bom:
> "IDoc ORDERS05 travado com status 51 — SAPSLL: documento de material não encontrado no centro de distribuição 1000"

---

**O que já foi tentado** — ajuda o agente a calibrar os próximos passos e evitar sugerir o que você já fez.

Ruim:
> "RFC não conecta"

Bom:
> "RFC connection refused no destino SM59. Já verificamos que o dispatcher SAP está ativo via SM50. Suspeita de bloqueio de firewall na porta 3300."

---

**Sistema de destino** — especialmente em landscapes multi-sistema, identificar o sistema alvo ajuda.

Ruim:
> "timeout ao chamar serviço OData"

Bom:
> "timeout ao chamar serviço OData /sap/opu/odata/sap/API_SALES_ORDER_SRV no SAP Gateway do S/4HANA — query sem filtro retornando mais de 50k registros"

---

### Exemplos completos

**Exemplo 1 — Incidente de autenticação OData**

```
Descrição: iFlow no SAP Integration Suite retornando HTTP 401 ao tentar
consumir serviço OData do S/4HANA. O adapter está configurado com
OAuth2 Client Credentials. A chamada funcionava ontem. Suspeita de
token expirado ou credencial inválida no Security Material.

Sistema: OData
Identificador: CPI-401-DEMO
```

Por que é bom: especifica o protocolo (OAuth2 CC), o sistema (Integration Suite → S/4HANA), o código de erro (401), e uma hipótese.

---

**Exemplo 2 — IDoc travado**

```
Descrição: IDoc ORDERS05 com status 51 no sistema receptor S/4HANA.
Erro na aplicação: "Material 4711 não encontrado no centro 1000".
IDoc enviado via porta parceiro para destino RECEPCAO_01. Tentativa
de reprocessamento via BD87 falhando com o mesmo erro.

Sistema: RFC
Identificador: RFC-IDOC-51-DEMO
```

Por que é bom: tipo de IDoc, status exato, mensagem de erro literal, sistema receptor, ação já tentada.

---

**Exemplo 3 — RFC connection refused**

```
Descrição: SM59 não consegue estabelecer conexão RFC com o sistema
de destino PRD. Teste de conexão retorna connection refused na
porta 3300. O sistema PRD está ativo (verificado via SSHADMIN).
Suspeita de regra de firewall alterada hoje durante janela de manutenção.

Sistema: RFC
Identificador: RFC-CONN-REFUSED-DEMO
```

Por que é bom: código de diagnóstico SAP (SM59), porta específica (3300), ações de verificação já realizadas, contexto temporal (janela de manutenção).

---

**Exemplo 4 — Incidente sem identificador**

```
Descrição: iFlow de integração entre SuccessFactors e SAP HCM travando
com timeout após 30 segundos. O iFlow chama um serviço OData do SAP
HCM via Cloud Connector. Não há filtros na query — ela traz todos os
colaboradores ativos (aproximadamente 45.000 registros). O problema
começou após a base de colaboradores crescer acima de 40.000.

Sistema: Sem conector
```

Por que é bom: mesmo sem identificador, a descrição contém o suficiente para o RAG recuperar o documento de timeout OData (volume de dados, ausência de filtro, comportamento esperado).

---

### O que evitar

**Linguagem ambígua sem referência técnica**

> "o sistema está lento" — qual sistema? qual operação? qual protocolo?

**Só o código de erro sem contexto**

> "erro 500" — 500 de qual sistema? OData, CPI, aplicação custom?

**Descrição em nível de negócio sem detalhe técnico**

> "a integração de pedidos de compra não está funcionando" — sem protocolo, sistema, código de erro ou comportamento específico

---

## Casos de uso típicos

### IDoc travado (status 51)

Ocorre quando o IDoc chega ao sistema receptor mas falha na aplicação — geralmente dado mestre ausente (material, fornecedor, conta contábil) ou validação de negócio.

O que informar: tipo do IDoc (ORDERS05, MATMAS, DEBMAS), status numérico (51), mensagem de erro da transação WE05, sistema receptor.

Identificadores de demo: `RFC-IDOC-51-DEMO`

### Timeout OData

Ocorre quando uma query OData sem filtro traz volume excessivo de dados, ou quando o sistema de backend está sobrecarregado.

O que informar: URL do serviço OData, tempo de timeout configurado, presença ou ausência de filtros `$filter` e `$top`, volume estimado de registros.

Identificadores de demo: sem identificador específico (use descrição textual)

### Falha de autenticação CPI (HTTP 401)

Ocorre quando o token OAuth2 expira e o adapter não renova automaticamente, ou quando as credenciais do Security Material estão incorretas/expiradas.

O que informar: tipo de autenticação (OAuth2 CC, Basic Auth, Certificate), sistema de destino, se houve rotação de credenciais recente.

Identificadores de demo: `CPI-401-DEMO`

### RFC connection refused

Ocorre quando o sistema SAP de destino está inacessível na porta 3300 — instância parada, firewall, alteração de rede.

O que informar: host e porta do sistema de destino, resultado do teste SM59, se o sistema está ativo (SM50/SM51), mudanças recentes de infraestrutura.

Identificadores de demo: `RFC-CONN-REFUSED-DEMO`

### Pool timeout de gateway RFC

Ocorre quando todas as conexões do pool de trabalho do SAP Gateway estão ocupadas — geralmente sob carga alta ou processos RFC pendurados.

O que informar: mensagem de erro exata do gateway, horário do incidente (pico de uso?), resultado de SM66 (processos ativos).

Identificadores de demo: sem identificador específico (use descrição textual)

---

## Interpretando o resultado

### Badge de confiança

O agente retorna um valor de confiança entre 0 e 1, exibido como badge colorido:

- **Alta (verde, ≥ 70%)** — o agente recuperou um documento com alta similaridade ao incidente e o caso é bem coberto pela base de conhecimento. Siga os próximos passos com confiança.
- **Média (amarelo, 40–69%)** — o documento recuperado é relevante mas o match não é perfeito. Use os próximos passos como ponto de partida, mas investigue outras possibilidades.
- **Baixa (vermelho, < 40%)** — o incidente não corresponde bem a nenhum caso documentado, ou o identificador não foi reconhecido pelo sistema. Os próximos passos são genéricos. Considere adicionar mais contexto à descrição ou consultar documentação específica do sistema.

### Documento de referência

O campo `matched_source` indica qual documento da base de conhecimento foi usado como referência. Se você quiser entender o diagnóstico mais a fundo, esse documento contém o caso completo com causas, diagnóstico e resolução típica.

### Confiança artificialmente limitada

Situações em que a confiança é automaticamente reduzida, independente da análise do LLM:

- Identificador não reconhecido pelo conector → confiança máxima de 0.4
- Nenhum documento recuperado acima do threshold de similaridade → confiança máxima de 0.3
- Conector em modo demo/mock → não afeta a confiança, mas o dado estruturado é simulado

---

## Limitações conhecidas

**O agente diagnostica, não corrige** — os próximos passos são recomendações; a execução é sempre do analista/arquiteto responsável.

**A base de conhecimento é limitada** — o agente só conhece o que está indexado em `data/sample_docs/`. Incidentes muito específicos de customização de cliente, paisagens altamente customizadas ou erros de versões antigas do SAP podem não ter correspondência na base.

**Conectores em modo mock** — quando não há credencial configurada, o conector retorna dados simulados. O diagnóstico ainda funciona, mas é baseado em dados de exemplo, não nos dados reais do incidente.

**Sem acesso a logs em tempo real** — o agente não acessa o monitoramento do Integration Suite, SM21, ST22, ou qualquer sistema SAP diretamente. O dado do sistema vem do conector via API/RFC, não de leitura de log.

**Não é um sistema de tickets** — o histórico de diagnósticos existe apenas na sessão atual. Não há persistência entre sessões nem integração com sistemas ITSM (ServiceNow, Jira) nativamente.

---

## Glossário

**IDoc** (Intermediate Document) — formato de mensagem padrão SAP para troca de dados entre sistemas via EDI ou ALE. Cada IDoc tem um tipo (ex: ORDERS05, MATMAS) e um status numérico que indica seu estado de processamento.

**RFC** (Remote Function Call) — protocolo proprietário SAP para chamadas de função entre sistemas ABAP. Usa a porta 3300 (instância 00) por padrão.

**OData** — protocolo REST padronizado (Open Data Protocol) usado pelo SAP Gateway para expor dados ABAP via HTTP/JSON.

**iFlow** — fluxo de integração no SAP Integration Suite (CPI). Cada iFlow orquestra a transformação e roteamento de mensagens entre sistemas.

**OAuth2 Client Credentials** — fluxo de autenticação machine-to-machine onde o sistema cliente se autentica com client_id e client_secret para obter um token de acesso.

**Security Material** — repositório de credenciais do SAP Integration Suite onde são armazenadas senhas, certificados e tokens para uso nos adapters dos iFlows.

**SM59** — transação SAP para gerenciar destinos RFC (conexões com sistemas externos).

**BD87** — transação SAP para reprocessamento de IDocs com erros de aplicação.

**WE05** — transação SAP para monitoramento de IDocs.

**RAG** (Retrieval-Augmented Generation) — técnica que combina recuperação de documentos (busca vetorial) com geração de texto por LLM. O agente usa RAG para fundamentar o diagnóstico em casos documentados, não em "conhecimento geral" do modelo.

**XSUAA** — serviço de autenticação e autorização do SAP BTP (Business Technology Platform), baseado em OAuth2/OIDC.

---

## Próximos passos como usuário

Depois do diagnóstico, o agente entrega um ponto de partida. A partir daí, o fluxo típico é:

1. **Confirmar a hipótese** usando as transações SAP indicadas (SM59, MM03, WE05, etc.)
2. **Executar a correção** — que pode ser um reprocessamento, uma configuração de adapter, ou uma criação de dado mestre
3. **Validar** que o incidente foi resolvido — reprocessar o IDoc via BD87, re-executar o iFlow, re-testar a conexão RFC
4. **Documentar** — se o incidente revelou um caso novo não coberto pela base de conhecimento, considere adicionar um documento em `data/sample_docs/` e reindexar com `uv run python -m app.rag.ingest --target incidents --reset`

---

*Integration Incident Copilot · github.com/marcos-slima/sap-integration-copilot*
