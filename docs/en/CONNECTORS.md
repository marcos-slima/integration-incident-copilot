# Multi-vendor connectors — integration guide (DA-59)

> English translation of [`docs/CONNECTORS.md`](../CONNECTORS.md) · source commit `812800c` · [Português](../CONNECTORS.md)
> If this page and the Portuguese source differ, the code and the Portuguese source prevail.

The Copilot has **10 connectors**. All follow the same contract:
`fetch(identifier) → ConnectorResult`. The connector brings data from the source system
to the diagnosis, and that data becomes evidence with its own confidence level
(DA-15).

> **Reviewed in the 2026-10-07 validation (Block 5).** The previous version
> listed variables that the code never read (OAUTH2_CLIENT_ID,
> ODATA_BASE_URL, RFC_USER, SERVICENOW_INSTANCE, CAP_BASE_URL…). Those who
> followed the guide would be in **demo mode without notice**. The `docs_env_vars`
> gate now fails if a variable listed here is not read by the project.

## 1. How the connector enters the pipeline

1. `app/agent/nodes.py::connector_node` calls `app/connectors/__init__.py::get_connector`
   with the `interface_type` from the request and executes `fetch(identifier)`.
2. **Real or demo mode** is decided by **one** variable per connector
   (`app/connectors/__init__.py::_REAL_MODE_SETTING`):
   - filled, the connector attempts the real system;
   - empty, the connector returns a **demo scenario**, with
     `is_mock=True`.
   The scenarios are a dictionary per identifier (`_MOCK_SCENARIOS` in each
   module); identifiers outside them return a generic fallback result
   (`is_fallback=True`).
3. The result becomes evidence in `app/agent/nodes.py::_assemble_evidence`:
   - `system_observed` only with real data;
   - `simulated` in demo mode.
   In the guardrail, fallback data limits diagnosis confidence to 0.4.
4. **Circuit breaker per system** (`app/connectors/base.py::connector_circuit_breaker`,
   with Redis when `REDIS_URL` exists): counts only unavailability (5xx,
   429, network failure). Wrong credential (401/403) and 4xx do not open the
   circuit (validated on 2026-10-07, M-06).
5. **OAuth2 token** stays in cache until `expires_in` minus 60 s.

`GET /health` (with `X-API-Key` or session) shows the mode of each connector,
calculated by `app/connectors/__init__.py::connector_status`:

```json
{"connectors": {"odata": {"status": "mock", "note": "ODATA_SERVICE_URL not configured"},
                "rfc":   {"status": "misconfigured", "note": "SAP_ASHOST configured, but 'pyrfc'/NetWeaver RFC SDK missing"}}}
```

`real` means "the variable is filled", **not** that the call
worked. The connectivity test is the first diagnosis.

## 2. Connectors and variables

The **Validated** column repeats the matrix of `docs/ARCHITECTURE.md`, which is the
source; the `connector_validation_matrix` gate requires that it covers all.

| `interface_type` | System | Turns on real mode | Other variables | Demo scenarios | Validated against real system |
|---|---|---|---|---|---|
| `odata` | SAP CPI / Integration Suite (OData v2) | `ODATA_SERVICE_URL` | `ODATA_OAUTH_TOKEN_URL`, `ODATA_CLIENT_ID`, `ODATA_CLIENT_SECRET` | `CPI-401-DEMO`, `CPI-TIMEOUT-DEMO` | no |
| `rfc` | ECC / S/4HANA via pyrfc | `SAP_ASHOST` | `SAP_SYSNR`, `SAP_CLIENT`, `SAP_USER`, `SAP_PASSWORD`, `RFC_TIMEOUT_SECONDS` | `RFC-IDOC-51-DEMO`, `RFC-CONN-REFUSED-DEMO`, `RFC-GWY-POOL-TIMEOUT-DEMO` | logon sim (ABAP Trial); `BAPI_IDOC_STATUS` no |
| `servicenow` | ServiceNow ITSM (Table API, Basic Auth) | `SERVICENOW_INSTANCE_URL` | `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD` | `INC0010001` | yes (PDI) |
| `salesforce` | Salesforce (OAuth2 client credentials + SOQL) | `SALESFORCE_INSTANCE_URL` | `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_API_VERSION` | `SF-CASE-00847-DEMO` | yes (Developer Edition) |
| `workday` | Workday (OAuth2 + REST) | `WORKDAY_TENANT` | `WORKDAY_REST_BASE_URL`, `WORKDAY_TOKEN_URL` (optional), `WORKDAY_CLIENT_ID`, `WORKDAY_CLIENT_SECRET` | `WD-SYNC-FAIL-DEMO` | no |
| `ariba` | SAP Ariba / Business Network (OAuth2 + REST) | `ARIBA_BASE_URL` | `ARIBA_OAUTH_TOKEN_URL`, `ARIBA_CLIENT_ID`, `ARIBA_CLIENT_SECRET` | `ARIBA-PO-BLOCKED-DEMO` | no |
| `successfactors` | SuccessFactors EC (OData v2 `PerPerson('<id>')`) | `SFSF_BASE_URL` | `SFSF_OAUTH_TOKEN_URL`, `SFSF_CLIENT_ID`, `SFSF_CLIENT_SECRET` | `SFSF-REPL-FAIL-DEMO`, `SFSF-INACTIVE-DEMO`, `SFSF-AUTH-FAIL-DEMO` | no |
| `cap` | SAP CAP (OData v4 + XSUAA) | `CAP_SERVICE_URL` | `CAP_XSUAA_TOKEN_URL`, `CAP_CLIENT_ID`, `CAP_CLIENT_SECRET` | `CAP-PO-APPROVAL-DEMO` | yes (BTP Trial) |
| `apim` | SAP API Management — **analytics events** | `APIM_ANALYTICS_URL` | `APIM_OAUTH_TOKEN_URL`, `APIM_CLIENT_ID`, `APIM_CLIENT_SECRET` | `APIM-RATE-LIMIT-DEMO` | no; speculative schema |
| `po` | SAP PO/PI on-premise (Message Monitor, via facade) | `PO_BASE_URL` | `PO_AUTH_MODE` (`basic`/`oauth2`), `PO_USERNAME`, `PO_PASSWORD`, `PO_OAUTH_TOKEN_URL`, `PO_OAUTH_CLIENT_ID`, `PO_OAUTH_CLIENT_SECRET` | `PO-FAILED-001`, `PO-HOLDING-001` | no; non-public API |

**Notes per connector** (those that change the result):

- **RFC.** "Validated" means logon (`RFC_SYSTEM_INFO`), not IDoc reading. With `SAP_ASHOST` filled and without `pyrfc`/SDK, the status is
  `misconfigured`. Pyrfc exceptions become error result: only communication failure counts for the circuit (M-10).
- **SuccessFactors.** Reading is by key (`PerPerson('<id>')`), and the returned `personIdExternal` is checked. The employment status comes from
  `EmpEmployment.endDate`. The EC→ERP replication state **is not** read; the connector says it in the message (M-07).
- **Workday.** Without `WORKDAY_TOKEN_URL`, the token is requested from the host of
  `WORKDAY_REST_BASE_URL` (`/ccx/oauth2/<tenant>/token`) (M-09).
- **PO/PI.** Inform `PO_BASE_URL` the facade (Web Dispatcher, proxy,
  APIM), not the PO/PI directly. `ALL` brings all messages (M-08).
- **APIM.** Reads analytics events: it is observability, not orchestration, and
  therefore **does not** cover the `integration_suite` column of `docs/COVERAGE_MAP.md`.

Identifiers go through validation (`fullmatch`, no `..`) before going to
the URL (M-05).

## 3. How to test

```bash
# Contract of each connector (HTTP simulated with httpx.MockTransport)
uv run pytest tests/test_connectors.py tests/test_cap_connector.py tests/test_apimanagement_connector.py -q

# Circuit breaker and identifier validation
uv run pytest tests/test_connector_circuit_breaker.py tests/test_connector_identifier_validation.py -q

# End-to-end diagnosis with demo scenario
uv run python -m app.agent.graph --interface rfc --id RFC-IDOC-51-DEMO "IDoc stuck with status 51"
```

For real mode, fill in the variables of the connector line in `.env`,
restart the API and check the `status` in `GET /health`.
