"""Conector SAP PO/PI — DA-56.

SAP Process Orchestration / Process Integration e um middleware de integracao
A2A/B2B on-premise, muito adotado em LATAM e Europa. O conector le o
**Message Monitor** (mensagens em FAILED/HOLDING) e, quando ha identifier de
mensagem, o detalhe dela.

Tres coisas que a documentacao deste conector precisa deixar claras, porque
sao as que mudam a leitura do resultado:

1. **O PO/PI nao e' REST-first e a API nao e' publica.** `/mdt/api/1.0/facade`
   (Message Monitor) e a API que o proprio NWA consome; ela NAO esta no Help
   Portal como API suportada e **varia entre patches e releases** (7.3 != 7.4
   != 7.5). O caminho real esta implementado e testado contra o formato
   documentado, mas a forma do payload e' tolerante de proposito (ver
   `_extract_messages`) e nunca foi exercitado contra um PO/PI de verdade —
   mesma categoria de risco do `APIManagementConnector` (schema especulativo),
   que e' honesto demais para se esconder. Ver a matriz de conectores em
   `docs/ARCHITECTURE.md`.

2. **Como o PO/PI esta exposto e' problema de quem opera, nao do conector.**
   existem varios padroes de exposicao (proxy/WAF na DMZ, SAP Web Dispatcher,
   ADC/load balancer, API Management como fachada, BTP + Cloud Connector,
   reverse invoke) e o conector e' **agnesico quanto a eles**: informe o
   endpoint da FACHADA em `PO_BASE_URL` e ele fala com o PO/PI por tras.
   Expor a porta ICM do PO/PI direto na Internet e' anti-pattern e nao e' um
   modo que o conector possa (nem deva) suportar.

3. **Autenticacao nativa do PO/PI e' Basic Auth** (usuario/senha do stack
   ABAP) — nao existe OAuth2 no PO/PI. O modo `oauth2` existe para o caso de
   um API Management na FRENTE dele reescrever Basic -> OAuth2 Client
   Credentials (mesmo padrao de `odata_connector`/`ariba_connector`). Sem
   `po_auth_mode="oauth2"` + token URL, o conector nao inventa token.

Uso:
    from app.connectors.po_connector import POConnector
    result = POConnector().fetch("FAILED")        # mensagens com falha
    result = POConnector().fetch("<messageId>")   # detalhe de uma mensagem
"""

import base64
import json
from typing import Any

import httpx

from app.config import settings
from app.connectors.base import (
    ConnectorResult,
    ExternalSystemConnector,
    circuit_breaker_guard,
    connector_circuit_breaker,
    validate_identifier_charset,
)
from app.exceptions import ConfigurationError

_SOURCE = "SAP PO/PI"

# Filtros aceitos como "identifier" para listar mensagens. Sao literais
# fixos, nunca entrada livre: o valor vai para a query string.
_STATUS_FILTERS = ("FAILED", "HOLDING", "ALL")
_DEFAULT_FILTER = "FAILED"
_MAX_SUMMARIZED = 10  # teto de linhas no resumo (contexto do LLM)

# Chave sob a qual a lista de mensagens pode aparecer. A API nao e' publica
# nem estavel (ver docstring do modulo), entao o parser e' tolerante em vez
# de casar com um unico formato: um payload levemente diferente nao pode
# virar traceback no grafo, tem de virar evidencia legivel.
_MESSAGE_KEYS = ("messages", "messageLog", "results", "d")

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "PO-FAILED-001": ConnectorResult(
        source_system=_SOURCE,
        status="error",
        error_code="FAILED",
        message=(
            "PO/PI: mensagem em FAILED na interface Z_S4_ORDER_OUT — "
            "Connection refused pelo sistema receptor (destino SAP S/4HANA)"
        ),
        raw=(
            "MESSAGE_LOG: PO-FAILED-001\n"
            "STATUS: FAILED\n"
            "INTERFACE: Z_S4_ORDER_OUT\n"
            "SENDER: SAP PO/PI\n"
            "RECEIVER: S4HANA PRD\n"
            "ERROR: java.net.ConnectException: Connection refused\n"
            "START_TIME: 2026-09-30T09:00:00Z"
        ),
    ),
    "PO-HOLDING-001": ConnectorResult(
        source_system=_SOURCE,
        status="ok",
        error_code=None,
        message=(
            "PO/PI: mensagem em HOLDING na interface Z_CUST_MDM_IN — "
            "aguardando retorno de um receiver externo"
        ),
        raw=(
            "MESSAGE_LOG: PO-HOLDING-001\n"
            "STATUS: HOLDING\n"
            "INTERFACE: Z_CUST_MDM_IN\n"
            "ERROR: aguardando confirmacao do receiver\n"
            "START_TIME: 2026-09-30T08:41:00Z"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system=_SOURCE,
    status="error",
    error_code="404",
    message="PO/PI: nenhuma mensagem encontrada para o filtro informado (modo demo)",
    raw=("SAP PO/PI Message Monitor: nenhum registro para o identificador informado (dados mock)"),
    is_fallback=True,
)

_LIST = ConnectorResult(
    source_system=_SOURCE,
    status="error",
    error_code=_DEFAULT_FILTER,
    message=(
        "PO/PI: mensagens com falha no Message Monitor (modo demo). "
        "Use um messageId para o detalhe de uma mensagem especifica."
    ),
    raw="\n".join(r.raw for r in _MOCK_SCENARIOS.values()),
)


class POConnector(ExternalSystemConnector):
    """`fetch(identifier)` busca no Message Monitor do PO/PI.

    `identifier` pode ser um **messageId** (detalhe da mensagem) ou um dos
    filtros `FAILED` / `HOLDING` / `ALL` (lista de mensagens). Vazio = lista
    de FAILED.

    `use_real=True` forca o caminho HTTP mesmo sem `PO_BASE_URL` — nesse caso
    falha alto e claro (`ConfigurationError`) em vez de cair em mock, mesmo
    padrao de `ODataConnector`.

    `client`: injecao opcional de um `httpx.Client` ja configurado, usado
    pelos testes para exercitar o caminho HTTP real via `MockTransport` sem
    depender de um PO/PI de verdade (mesmo padrao dos demais conectores).
    """

    def __init__(
        self,
        use_real: bool = False,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ):
        if use_real and not settings.po_base_url:
            raise ConfigurationError(
                "POConnector(use_real=True) exige PO_BASE_URL configurado no "
                ".env - sem isso nao ha para onde chamar. Aponte para a "
                "fachada exposta (proxy/Web Dispatcher/APIM), nao para a "
                "porta ICM do PO/PI."
            )
        if settings.po_auth_mode == "oauth2" and not settings.po_oauth_token_url:
            # Fail-closed: sem token URL nao existe OAuth2 possivel, e cair
            # silenciosamente em Basic Auth seria mandar Basic para um token
            # endpoint e receber 401 sem explicacao.
            raise ConfigurationError(
                "PO_AUTH_MODE=oauth2 exige PO_OAUTH_TOKEN_URL configurado no "
                ".env (o PO/PI em si so' faz Basic Auth; OAuth2 so' existe "
                "quando ha API Management na frente dele)."
            )
        self.use_real = use_real
        self.timeout = timeout
        self._injected_client = client

    def fetch(self, identifier: str) -> ConnectorResult:
        if self.use_real or settings.po_base_url:
            return self._fetch_real(identifier)
        if not identifier or identifier in _STATUS_FILTERS:
            return _LIST
        return _MOCK_SCENARIOS.get(identifier, _DEFAULT)

    # ------------------------------------------------------------------
    # Caminho real
    # ------------------------------------------------------------------

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        if (blocked := circuit_breaker_guard(_SOURCE)) is not None:
            return blocked

        is_filter = identifier in _STATUS_FILTERS or not identifier
        # O filtro vem de uma tupla de literais (nao de entrada livre), entao
        # nao passa pela validação de charset — que existe para o identifier
        # do usuario, interpolado na query string.
        if (
            not is_filter
            and (invalid := validate_identifier_charset(identifier, _SOURCE)) is not None
        ):
            return invalid

        client = self._injected_client or httpx.Client(timeout=self.timeout)
        try:
            headers = {"Accept": "application/json"}
            if settings.po_auth_mode == "oauth2":
                headers["Authorization"] = f"Bearer {_oauth2_token(client)}"
            else:
                # Basic Auth nativo do PO/PI (usuario/senha do stack ABAP).
                headers["Authorization"] = "Basic " + _basic_auth_header(
                    settings.po_username, settings.po_password
                )

            url = f"{settings.po_base_url.rstrip('/')}/mdt/api/1.0/facade"
            params: dict[str, str] = {"type": "message"}
            if is_filter:
                params["status"] = _DEFAULT_FILTER if identifier in ("", "ALL") else identifier
            else:
                params["id"] = identifier

            response = client.get(url, params=params, headers=headers)
        except httpx.RequestError as exc:
            connector_circuit_breaker.record_failure(
                _SOURCE,
                settings.connector_circuit_failure_threshold,
                settings.connector_circuit_cooldown_seconds,
            )
            return ConnectorResult(
                source_system=_SOURCE,
                status="error",
                error_code="CONNECTION_ERROR",
                message=f"Falha de rede ao consultar SAP PO/PI: {exc}",
                raw=str(exc),
                is_mock=False,
                is_fallback=True,
            )
        finally:
            if self._injected_client is None:
                client.close()

        connector_circuit_breaker.record_success(_SOURCE)

        if response.status_code != 200:
            return ConnectorResult(
                source_system=_SOURCE,
                status="error",
                error_code=str(response.status_code),
                message=f"PO/PI Message Monitor retornou HTTP {response.status_code}",
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        # A API pode responder XML em vez de JSON conforme o path/patch; um
        # JSONDecodeError aqui viraria traceback no grafo em vez de evidencia.
        try:
            payload: Any = response.json()
        except ValueError:
            return ConnectorResult(
                source_system=_SOURCE,
                status="error",
                error_code="UNEXPECTED_CONTENT_TYPE",
                message=(
                    "PO/PI nao devolveu JSON (a API do Message Monitor nao e' "
                    "publica e varia entre releases). Verifique o path "
                    "/mdt/api/1.0/facade e o formato aceito nesta versao."
                ),
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        messages = _extract_messages(payload)
        if not messages:
            return ConnectorResult(
                source_system=_SOURCE,
                status="error",
                error_code="404",
                message=(
                    f"PO/PI: nenhuma mensagem encontrada para {identifier or _DEFAULT_FILTER}"
                ),
                raw=response.text[:2000],
                is_mock=False,
                is_fallback=True,
            )

        return _result_from_messages(messages, response.text)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _basic_auth_header(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode()
    return base64.b64encode(raw).decode()


def _oauth2_token(client: httpx.Client) -> str:
    """OAuth2 Client Credentials — só quando ha API Management na frente do
    PO/PI (o PO/PI em si nao tem OAuth2). `__init__` ja falha alto se o
    modo for oauth2 sem token URL."""
    response = client.post(
        settings.po_oauth_token_url,
        data={"grant_type": "client_credentials"},
        auth=(settings.po_oauth_client_id, settings.po_oauth_client_secret),
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _extract_messages(payload: Any) -> list[dict[str, Any]]:
    """Isola a lista de mensagens de um payload de formato nao garantido.

    A API do Message Monitor nao e' publica nem estavel (ver docstring do
    modulo), entao o leitor aceita as formas que aparecem na pratica — lista
    direta, envelope com "messages"/"messageLog"/"results", envelope OData
    ({"d": {"results": [...]}}) e objeto unico — em vez de casar com um
    unico formato e estourar KeyError no grafo quando o PO/PI responder
    outra coisa.
    """
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if not isinstance(payload, dict):
        return []

    for key in _MESSAGE_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [m for m in value if isinstance(m, dict)]
        if isinstance(value, dict):
            inner = value.get("results")
            if isinstance(inner, list):
                return [m for m in inner if isinstance(m, dict)]
            return [value]
    return [payload]


def _result_from_messages(messages: list[dict[str, Any]], raw_text: str) -> ConnectorResult:
    """Monta o ConnectorResult. `status="error"` quando a mensagem consultada
    esta FAILED — mesma convencao dos demais conectores (um incidente do
    ITSM, um IDoc 51 e uma mensagem FAILED sao todos `error` com um
    `error_code` de dominio, nao de HTTP)."""
    summary = _summarize(messages)
    statuses = {str(m.get("status") or m.get("Status") or "").upper() for m in messages}
    failed = "FAILED" in statuses or "ERROR" in statuses
    error_code = "FAILED" if failed else (min(statuses) if statuses else None)

    return ConnectorResult(
        source_system=_SOURCE,
        status="error" if failed else "ok",
        error_code=error_code,
        message=summary,
        raw=raw_text[:4000],
        is_mock=False,
        is_fallback=False,
    )


def _summarize(messages: list[dict[str, Any]]) -> str:
    """Resumo textual das mensagens, limitado a `_MAX_SUMMARIZED` linhas para
    nao estourar o contexto do LLM (o `raw` completo vai junto, para o
    RAG/evidencia poderem citar)."""
    lines: list[str] = []
    for m in messages[:_MAX_SUMMARIZED]:
        status = m.get("status") or m.get("Status") or "?"
        mid = m.get("messageId") or m.get("MessageId") or m.get("id") or "?"
        iface = m.get("interface") or m.get("interfaceName") or m.get("Interface") or "?"
        err = (
            m.get("errorText")
            or m.get("ErrorText")
            or m.get("error")
            or m.get("ErrorMessage")
            or ""
        )
        lines.append(f"[{status}] {mid} | Interface: {iface} | Erro: {str(err)[:200]}")
    if len(messages) > _MAX_SUMMARIZED:
        lines.append(f"... (+{len(messages) - _MAX_SUMMARIZED} mensagens)")
    return "\n".join(lines) if lines else json.dumps(messages[:1], ensure_ascii=False)
