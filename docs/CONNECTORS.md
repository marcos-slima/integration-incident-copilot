# Conectores multi-vendor — guia de integração (DA-59)

O Copilot tem **10 conectores**. Todos seguem o mesmo contrato:
`fetch(identifier) → ConnectorResult`. O conector traz o dado do sistema de
origem para o diagnóstico, e esse dado vira evidência com nível de confiança
próprio (DA-15).

> **Revisado na validação de 2026-10-07 (Bloco 5).** A versão anterior
> listava variáveis que o código nunca leu (OAUTH2_CLIENT_ID,
> ODATA_BASE_URL, RFC_USER, SERVICENOW_INSTANCE, CAP_BASE_URL…). Quem
> seguisse o guia ficava em **modo demo sem aviso**. O gate `docs_env_vars`
> agora reprova variável citada aqui que o projeto não lê.

## 1. Como o conector entra no pipeline

1. `app/agent/nodes.py::connector_node` chama `app/connectors/__init__.py::get_connector`
   com o `interface_type` da requisição e executa `fetch(identifier)`.
2. **Modo real ou demo** é decidido por **uma** variável por conector
   (`app/connectors/__init__.py::_REAL_MODE_SETTING`):
   - preenchida, o conector tenta o sistema real;
   - vazia, o conector devolve um **cenário de demonstração**, com
     `is_mock=True`.
   Os cenários são um dicionário por identificador (`_MOCK_SCENARIOS` em cada
   módulo); identificador fora deles devolve um resultado genérico de
   fallback (`is_fallback=True`).
3. O resultado vira evidência em `app/agent/nodes.py::_assemble_evidence`:
   - `system_observed` só com dado real;
   - `simulated` em modo demo.
   No guardrail, dado de fallback limita a confiança do diagnóstico a 0,4.
4. **Circuit breaker por sistema** (`app/connectors/base.py::connector_circuit_breaker`,
   com Redis quando `REDIS_URL` existe): conta só indisponibilidade (5xx,
   429, falha de rede). Credencial errada (401/403) e 4xx não abrem o
   circuito (validação 2026-10-07, M-06).
5. **Token OAuth2** fica em cache até `expires_in` menos 60 s.

`GET /health` (com `X-API-Key` ou sessão) mostra o modo de cada conector,
calculado por `app/connectors/__init__.py::connector_status`:

```json
{"connectors": {"odata": {"status": "mock", "note": "ODATA_SERVICE_URL nao configurado"},
                "rfc":   {"status": "misconfigured", "note": "SAP_ASHOST configurado, mas pacote 'pyrfc'/SDK NetWeaver RFC ausente"}}}
```

`real` quer dizer "a variável está preenchida", **não** que a chamada
funcionou. O teste de conectividade é o primeiro diagnóstico.

## 2. Conectores e variáveis

A coluna **Validado** repete a matriz de `docs/ARCHITECTURE.md`, que é a
fonte; o gate `connector_validation_matrix` exige que ela cubra todos.

| `interface_type` | Sistema | Liga o modo real | Demais variáveis | Cenários demo | Validado contra sistema real |
|---|---|---|---|---|---|
| `odata` | SAP CPI / Integration Suite (OData v2) | `ODATA_SERVICE_URL` | `ODATA_OAUTH_TOKEN_URL`, `ODATA_CLIENT_ID`, `ODATA_CLIENT_SECRET` | `CPI-401-DEMO`, `CPI-TIMEOUT-DEMO` | não |
| `rfc` | ECC / S/4HANA via pyrfc | `SAP_ASHOST` | `SAP_SYSNR`, `SAP_CLIENT`, `SAP_USER`, `SAP_PASSWORD`, `RFC_TIMEOUT_SECONDS` | `RFC-IDOC-51-DEMO`, `RFC-CONN-REFUSED-DEMO`, `RFC-GWY-POOL-TIMEOUT-DEMO` | logon sim (ABAP Trial); `BAPI_IDOC_STATUS` não |
| `servicenow` | ServiceNow ITSM (Table API, Basic Auth) | `SERVICENOW_INSTANCE_URL` | `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD` | `INC0010001` | sim (PDI) |
| `salesforce` | Salesforce (OAuth2 client credentials + SOQL) | `SALESFORCE_INSTANCE_URL` | `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_API_VERSION` | `SF-CASE-00847-DEMO` | sim (Developer Edition) |
| `workday` | Workday (OAuth2 + REST) | `WORKDAY_TENANT` | `WORKDAY_REST_BASE_URL`, `WORKDAY_TOKEN_URL` (opcional), `WORKDAY_CLIENT_ID`, `WORKDAY_CLIENT_SECRET` | `WD-SYNC-FAIL-DEMO` | não |
| `ariba` | SAP Ariba / Business Network (OAuth2 + REST) | `ARIBA_BASE_URL` | `ARIBA_OAUTH_TOKEN_URL`, `ARIBA_CLIENT_ID`, `ARIBA_CLIENT_SECRET` | `ARIBA-PO-BLOCKED-DEMO` | não |
| `successfactors` | SuccessFactors EC (OData v2 `PerPerson('<id>')`) | `SFSF_BASE_URL` | `SFSF_OAUTH_TOKEN_URL`, `SFSF_CLIENT_ID`, `SFSF_CLIENT_SECRET` | `SFSF-REPL-FAIL-DEMO`, `SFSF-INACTIVE-DEMO`, `SFSF-AUTH-FAIL-DEMO` | não |
| `cap` | SAP CAP (OData v4 + XSUAA) | `CAP_SERVICE_URL` | `CAP_XSUAA_TOKEN_URL`, `CAP_CLIENT_ID`, `CAP_CLIENT_SECRET` | `CAP-PO-APPROVAL-DEMO` | sim (BTP Trial) |
| `apim` | SAP API Management — **eventos de analytics** | `APIM_ANALYTICS_URL` | `APIM_OAUTH_TOKEN_URL`, `APIM_CLIENT_ID`, `APIM_CLIENT_SECRET` | `APIM-RATE-LIMIT-DEMO` | não; schema especulativo |
| `po` | SAP PO/PI on-premise (Message Monitor, via fachada) | `PO_BASE_URL` | `PO_AUTH_MODE` (`basic`/`oauth2`), `PO_USERNAME`, `PO_PASSWORD`, `PO_OAUTH_TOKEN_URL`, `PO_OAUTH_CLIENT_ID`, `PO_OAUTH_CLIENT_SECRET` | `PO-FAILED-001`, `PO-HOLDING-001` | não; API não pública |

> **`odata` — contrato do filtro (gap A1, 2026-11-11).** O filtro passou de
> `MessageId eq '<id>'` para `MessageGuid eq '<id>'`, que é o identificador da
> API de Message Processing Logs. Quem aponta `ODATA_SERVICE_URL` para uma
> fachada própria que ainda filtra por `MessageId` precisa ajustá-la. A
> mensagem é lida de `StatusText` → `LogText` → `CustomStatus`; o detalhe do
> recurso `ErrorInformation`, a paginação por next-link e a janela temporal
> ainda não são implementados, e nada disso foi validado contra tenant real.

**Observações por conector** (as que mudam o resultado):

- **RFC.** "Validado" significa logon (`RFC_SYSTEM_INFO`), não a leitura de
  IDoc. Com `SAP_ASHOST` preenchido e sem `pyrfc`/SDK, o status é
  `misconfigured`. Exceções do pyrfc viram resultado de erro: só falha de
  comunicação conta para o circuito (M-10).
- **SuccessFactors.** A leitura é pela chave (`PerPerson('<id>')`), e o
  `personIdExternal` devolvido é conferido. O status do vínculo vem de
  `EmpEmployment.endDate`. O estado da replicação EC→ERP **não** é lido; o
  conector diz isso na mensagem (M-07).
- **Workday.** Sem `WORKDAY_TOKEN_URL`, o token é pedido ao host de
  `WORKDAY_REST_BASE_URL` (`/ccx/oauth2/<tenant>/token`) (M-09).
- **PO/PI.** Informe em `PO_BASE_URL` a fachada (Web Dispatcher, proxy,
  APIM), não o PO/PI direto. `ALL` traz todas as mensagens (M-08).
- **APIM.** Lê eventos de analytics: é observabilidade, não orquestração, e
  por isso **não** cobre a coluna `integration_suite` do `docs/COVERAGE_MAP.md`.

Identificadores passam por validação (`fullmatch`, sem `..`) antes de ir para
a URL (M-05).

## 3. Como testar

```bash
# Contrato de cada conector (HTTP simulado com httpx.MockTransport)
uv run pytest tests/test_connectors.py tests/test_cap_connector.py tests/test_apimanagement_connector.py -q

# Circuit breaker e validação de identificador
uv run pytest tests/test_connector_circuit_breaker.py tests/test_connector_identifier_validation.py -q

# Diagnóstico de ponta a ponta com o cenário demo
uv run python -m app.agent.graph --interface rfc --id RFC-IDOC-51-DEMO "IDoc travado com status 51"
```

Para o modo real, preencha no `.env` as variáveis da linha do conector,
reinicie a API e confira o `status` em `GET /health`.

## 4. Como adicionar um conector

Um conector novo precisa aparecer em **9 superfícies**. O gate
`connector_reachable` confere oito delas, e o `connector_coverage` confere a
nona:

| # | Superfície | Onde |
|---|---|---|
| 1 | Registro e variável de modo real | `app/connectors/__init__.py::_REGISTRY` e `_REAL_MODE_SETTING` |
| 2 | Literal da requisição | `IncidentRequest.interface_type` em `app/models/models.py` |
| 3 | Literal do evento | `IncidentEventData.interface_type` em `app/models/models.py` |
| 4 | Domínio de roteamento (DA-63) | `domains.sap` ou `domains.saas` em `data/skills/diagnose_incident.yaml`; o supervisor (`app/agent/supervisor.py::_DOMAIN_INDEX`) deriva o roteamento daí (fora dos dois, como o `apim`, o domínio sai da descrição) |
| 5 | CLI | `choices` de `--interface` em `app/agent/graph.py` |
| 6 | Catálogo admin | `app/admin/models.py::CONNECTOR_TYPES` |
| 7 | Seed da busca web (DA-57) | `SEED` em `alembic/versions/008_*.py` (nova migration, não edite a 008) |
| 8 | Formulário de sistemas | `<select name="connector_type">` em `app/admin/templates/systems.html` |
| 9 | Mapa de cobertura (DA-58) | `data/connector_coverage.yaml`, depois `uv run python scripts/coverage_map.py --write` |

Além disso:

- campos no `app/config.py` com o prefixo do conector;
- linha na matriz de validação de `docs/ARCHITECTURE.md`;
- linha na tabela da seção 2 deste documento;
- testes com `httpx.MockTransport`.

## 5. Limitações

- **Só leitura por chamada.** O conector não faz polling nem assinatura de
  eventos. Para disparo automático, use o webhook CloudEvents ou o
  consumidor AMQP (UC-07 em `docs/CASOS_DE_USO.md`).
- **Sem retry no conector.** A falha volta como resultado de erro. A
  repetição fica com quem chama (RQ, broker) e o circuito evita martelar um
  sistema fora do ar.
- **Credenciais só por ambiente** (`.env` ou Secret Kyma). O registro
  cifrado do admin (DA-46/47) é para provedores de LLM, não para conectores.

## Proveniência da validação real

As datas abaixo identificam o registro no Git. A validação não foi
reexecutada nesta atualização. Cassettes e `MockTransport` verificam
contratos simulados e têm alcance separado.

| Conector | Registro | Ambiente e recorte relatado | Limite |
|---|---|---|---|
| RFC | 11/09/2026, `285a216` | ABAP Cloud Developer Trial A4H rel. 754, logon e `RFC_SYSTEM_INFO`, pyrfc 3.3.1 e SDK 7.50 PL19 | O Trial não confirmou a leitura por `BAPI_IDOC_STATUS` |
| ServiceNow | 04/09/2026, `8dffbc8` | PDI, consulta pela Table API com Basic Auth | O registro não estabelece cobertura das variantes da API ou de instâncias de clientes |
| Salesforce | 04/09/2026, `b261564` | Developer Edition, OAuth2 Client Credentials e consulta SOQL | O registro não estabelece cobertura de políticas e schemas customizados |
| CAP | 08/09/2026, `22b2280` | BTP Trial, OData v4 com XSUAA e correção do filtro e do mapeamento | O registro não estabelece compatibilidade com qualquer serviço CAP |

Para drift OData, `ODataConnector.fetch_contract` reaproveita OAuth do
conector, usa a URL administrativa do serviço com sufixo `/$metadata`,
timeout HTTP e parsing dedicado. O operador deve apontar para o serviço
autorizado e configurar HTTPS. Erros tratados retornam contrato ausente,
que o observador classifica como `unverified`. O fingerprint representa
o contrato normalizado, separado do XML recebido. A observação do CLI e
a comparação do histórico são descritas no capítulo 14 do
[manual](MANUAL.md).
