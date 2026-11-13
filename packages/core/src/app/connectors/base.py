"""Interface comum para conectores de sistemas externos (SAP e nao-SAP).

Todo conector (OData, RFC, ServiceNow, e futuros como IDoc/SOAP/
Salesforce/Workday) implementa este contrato, permitindo que o grafo
LangGraph trate qualquer sistema de origem de forma uniforme -
inclusive combinando SAP com nao-SAP no mesmo incidente (ex: alerta
aberto no ServiceNow sobre uma falha de conexao RFC no SAP).

O nome da classe (`SAPConnector`) ficou historico desde quando o
projeto so falava com SAP; o contrato em si sempre foi generico
(`source_system` sempre aceitou "outros"). `ExternalSystemConnector` e
o alias recomendado para conectores novos que nao sao SAP - ver
`app/connectors/servicenow_connector.py`.

NOTA sobre "mock": todo conector segue o MESMO criterio - config
ausente = modo demo/mock (simula respostas realistas para fins de
prototipagem/portfolio, sem depender de acesso a um sistema real);
config presente = chamada real (HTTP/OAuth2 de verdade). RFC continua
mock-only ate `use_real=True` + pyrfc + SAP NetWeaver RFC SDK estarem
disponiveis (SDK exige S-user de cliente/parceiro, nao ha atalho
gratuito). Todos os demais (OData, ServiceNow, Salesforce, Workday,
Ariba, CAP) suportam o caminho real hoje - ver ARCHITECTURE.md,
secao "Conectores - mock vs. real, hoje" para o estado de validacao
de cada um.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.circuit_breaker import CircuitBreaker
from app.config import settings
from app.contracts.model import Contract


@dataclass
class ConnectorResult:
    source_system: str  # "OData" | "RFC" | "ServiceNow" | outros
    status: str  # "ok" | "error"
    error_code: str | None
    message: str
    raw: str  # payload/log bruto (real ou simulado, ver is_mock)
    is_mock: bool = True
    is_fallback: bool = False  # True quando o identificador nao foi reconhecido
    # (dado generico, nao um cenario real mapeado)


class SAPConnector(ABC):
    """Contrato comum para qualquer conector de sistema externo (SAP
    ou nao-SAP - ver nota de modulo acima sobre o nome historico)."""

    @abstractmethod
    def fetch(self, identifier: str) -> ConnectorResult:
        """Busca o estado/erro de uma interface a partir de um
        identificador (ex: nome do iFlow, RFC destination, numero de
        IDoc, numero de incidente ServiceNow). Implementacoes mock
        ignoram credenciais reais; implementacoes reais (ex:
        ServiceNowConnector configurado) fazem a chamada de fato.
        """
        raise NotImplementedError

    def fetch_contract(self) -> Contract | None:
        """DA-52: o contrato que o proprio sistema publica, quando publica.

        Interface segregada, e nao mais um metodo obrigatorio: forcar
        `fetch_contract` nos 8 conectores faria os 7 que nao temem
        introsspeccao devolverem `None` em seis lugares, e o ganho seria
        zero. Quem sabe ler contrato implementa; os outros herdam o
        `None` e o detector registra `nao_introspectavel` como estado
        proprio -- que e' diferente de "o contrato nao mudou".

        Deliberadamente NAO faz parte do ABC: nenhum pipeline atual
        chama isto, e o default nunca e' exercitado por testes de
        conector, entao o contrato da classe base fica minimo.
        """
        return None


# Alias preferido para conectores de sistemas que nao sao SAP (o nome
# da classe em si continua `SAPConnector` por compatibilidade com todo
# o codigo/testes existentes - ver nota de modulo).
ExternalSystemConnector = SAPConnector


# Avaliacao externa (medio prazo, item 3): "Circuit breaker nos
# conectores (ex.: tenacity + contador de falhas)". Antes desta
# mudanca, so o AI Gateway (DA-26, app/llm/gateway.py) tinha circuit
# breaker - uma falha de rede num conector HTTP (ex.: ServiceNow fora
# do ar) significava esperar o timeout completo (settings.*_timeout,
# quando existe) EM TODA chamada seguinte, mesmo sabendo que o sistema
# acabou de falhar. Singleton em nivel de modulo, compartilhado por
# TODOS os conectores (a chave e o source_system de cada um, ex.:
# "ServiceNow", "Salesforce" - circuitos independentes por sistema,
# mesma instancia de CircuitBreaker). Mesmo nao-objetivo do AI
# Gateway: in-memory, por processo, nao compartilhado entre replicas.
connector_circuit_breaker = CircuitBreaker(namespace="conn")  # DA-41: Redis distribuido


def _is_availability_failure(status_code: int) -> bool:
    """Validacao 2026-10-07 (M-06): so indisponibilidade conta para o breaker.

    5xx e 429 dizem "o sistema nao esta atendendo" - e o que o circuito
    existe para evitar martelar. 4xx (401/403 de credencial, 404 de
    identificador) e uma resposta VALIDA de um sistema saudavel; contar como
    falha abria o circuito por erro de configuracao e passava a esconder o
    401 real atras de "CIRCUIT_OPEN"."""
    return status_code >= 500 or status_code == 429


def record_response_outcome(source_system: str, status_code: int) -> None:
    """Registra o resultado de uma resposta HTTP no breaker do conector.

    Antes: `record_success` incondicional depois de qualquer resposta - um
    servico devolvendo 503 em toda chamada nunca abria o circuito."""
    if _is_availability_failure(status_code):
        connector_circuit_breaker.record_failure(
            source_system,
            settings.connector_circuit_failure_threshold,
            settings.connector_circuit_cooldown_seconds,
        )
    else:
        connector_circuit_breaker.record_success(source_system)


def record_network_failure(source_system: str) -> None:
    """Falha de rede (timeout, DNS, conexao recusada): sempre conta."""
    connector_circuit_breaker.record_failure(
        source_system,
        settings.connector_circuit_failure_threshold,
        settings.connector_circuit_cooldown_seconds,
    )


def json_or_error(
    response, source_system: str, *, expect: type = dict
) -> tuple[object, ConnectorResult | None]:
    """Validacao 2026-10-07 (M-06): `response.json()` sem tratamento.

    Um 200 com corpo HTML (pagina de login de proxy/SSO, pagina de
    manutencao) ou JSON de outro formato derrubava o diagnostico inteiro com
    500. Devolve `(dados, None)` ou `(None, ConnectorResult de erro)`."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, expect):
        return None, ConnectorResult(
            source_system=source_system,
            status="error",
            error_code="INVALID_RESPONSE",
            message=(
                f"{source_system} respondeu HTTP {response.status_code} com corpo que nao e "
                "o JSON esperado (pagina de login/proxy ou API diferente da configurada?)"
            ),
            raw=(response.text or "")[:2000],
            is_mock=False,
            is_fallback=True,
        )
    return data, None


class _TokenCache:
    """Validacao 2026-10-07 (M-06): token OAuth2 sem cache.

    Cada diagnostico pedia um token novo ao IdP (uma ida a mais na rede por
    chamada, e IdPs costumam limitar emissao). Cache por (url, client_id),
    valido ate `expires_in` menos 60 s de folga; sem `expires_in`, 5 min.
    Por processo - o mesmo nao-objetivo do breaker em memoria."""

    _SAFETY_SECONDS = 60.0
    _DEFAULT_TTL = 300.0

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._items: dict[tuple, tuple[str, float]] = {}

    def get(self, key: tuple, fetch) -> str:
        """`fetch()` faz o POST ao token endpoint e devolve o `httpx.Response`."""
        import time

        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item and item[1] > now:
                return item[0]
        response = fetch()
        response.raise_for_status()
        try:
            body = response.json()
            token = body["access_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise TokenResponseError(
                "token endpoint respondeu sem access_token (JSON invalido ou outro formato)"
            ) from exc
        try:
            ttl = float(body.get("expires_in") or self._DEFAULT_TTL)
        except (TypeError, ValueError):
            ttl = self._DEFAULT_TTL
        with self._lock:
            self._items[key] = (token, now + max(0.0, ttl - self._SAFETY_SECONDS))
        return token

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class TokenResponseError(ValueError):
    """Token endpoint respondeu 2xx sem um `access_token` legivel.

    Subclasse de ValueError: quem ja tratava "JSON invalido" como ValueError
    (ex.: ODataConnector.fetch_contract) continua cobrindo este caso."""


oauth_token_cache = _TokenCache()


def token_error_result(source_system: str, exc: Exception, label: str) -> ConnectorResult:
    """Resultado de erro padrao para falha ao obter o token OAuth2.

    401/403 do token endpoint e credencial/configuracao: NAO conta para o
    breaker (M-06). 5xx/429 conta."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if _is_availability_failure(code):
            record_network_failure(source_system)
        return ConnectorResult(
            source_system=source_system,
            status="error",
            error_code=str(code),
            message=f"Falha ao obter token OAuth2 {label}: HTTP {code}",
            raw=exc.response.text[:2000],
            is_mock=False,
            is_fallback=True,
        )
    return ConnectorResult(
        source_system=source_system,
        status="error",
        error_code="INVALID_TOKEN_RESPONSE",
        message=f"Falha ao obter token OAuth2 {label}: {exc}",
        raw="",
        is_mock=False,
        is_fallback=True,
    )


def circuit_breaker_guard(source_system: str) -> ConnectorResult | None:
    """Chamado no INICIO de `_fetch_real(...)` de cada conector, antes
    de abrir a conexao HTTP. Devolve um `ConnectorResult` de erro
    imediato (sem tentar a rede) se o circuito daquele source_system
    estiver aberto, ou `None` se a chamada pode prosseguir normalmente.

    Uso tipico dentro de um conector:

        def _fetch_real(self, identifier: str) -> ConnectorResult:
            if (blocked := circuit_breaker_guard("ServiceNow")) is not None:
                return blocked
            try:
                ...chamada HTTP real...
            except httpx.RequestError as exc:
                connector_circuit_breaker.record_failure(
                    "ServiceNow", settings.connector_circuit_failure_threshold, settings.connector_circuit_cooldown_seconds)
                ...ConnectorResult de erro, como ja acontecia antes...
            else:
                connector_circuit_breaker.record_success("ServiceNow")
                ...
    """
    if connector_circuit_breaker.is_open(
        source_system, settings.connector_circuit_cooldown_seconds
    ):
        return ConnectorResult(
            source_system=source_system,
            status="error",
            error_code="CIRCUIT_OPEN",
            message=(
                f"Circuito aberto para {source_system}: muitas falhas de rede "
                "consecutivas recentes - chamada pulada sem tentar a rede de "
                "novo (aguardando o cooldown do circuit breaker)."
            ),
            raw="circuit breaker aberto (app/connectors/base.py::circuit_breaker_guard)",
            is_mock=False,
            is_fallback=True,
        )
    return None


# Avaliacao externa (nova revisao, P1 - "Injection nos conectores
# reais"): identifier (nome do iFlow, RFC destination, numero de
# IDoc/incidente/PO - ver app/models.py::IncidentRequest.identifier)
# vinha de entrada do usuario e era interpolado CRU em query strings
# (OData $filter, SOQL WHERE, ServiceNow sysparm_query, CAP $filter)
# e em segmentos de path de URL (Workday, Ariba) sem nenhum escape ou
# validacao - um identifier como "x' or 1 eq 1" (OData/SOQL) ou
# "../outro-recurso" (path) alterava a query/URL de verdade. Um
# charset restrito (SAP/ITSM/CRM ja usam so alfanumerico + separadores
# tipicos: numero de IDoc, RFC destination, CaseNumber, nome de iFlow)
# elimina a classe inteira de injection sem exigir escape especifico
# por protocolo - aspas, "/", "?", "#", espacos etc. nunca chegam a
# fazer parte da query/URL, entao nao ha o que escapar depois.
# Validacao 2026-10-07 (M-05): `re.match` + `$` aceitava "abc\n" (o `$` casa
# antes de uma quebra de linha final) e o charset deixava passar ".." - que,
# interpolado no PATH das URLs (Workday, Ariba, SuccessFactors), sobe um
# nivel no servidor. Agora: fullmatch e nenhum segmento so de pontos.
_IDENTIFIER_CHARSET_RE = re.compile(r"[A-Za-z0-9._-]{1,200}")


def validate_identifier_charset(identifier: str, source_system: str) -> ConnectorResult | None:
    """Chamado no INICIO de `_fetch_real(...)` de cada conector real
    (depois do circuit_breaker_guard, antes de montar a query/URL com
    o identifier). Devolve um `ConnectorResult` de erro imediato (sem
    tentar a rede) se o identifier tiver qualquer caractere fora do
    charset permitido, ou `None` se pode prosseguir normalmente. Os
    identifiers de demo (ex.: "CPI-401-DEMO", "RFC-IDOC-51-DEMO") e
    qualquer identifier SAP/ITSM/CRM real (numero de IDoc, RFC
    destination, CaseNumber, nome de iFlow, numero de PO) ja respeitam
    esse charset - nenhum uso legitimo e afetado."""
    if (
        not isinstance(identifier, str)
        or not _IDENTIFIER_CHARSET_RE.fullmatch(identifier)
        or ".." in identifier
        or identifier.strip(".") == ""
    ):
        return ConnectorResult(
            source_system=source_system,
            status="error",
            error_code="INVALID_IDENTIFIER",
            message=(
                f"Identificador invalido para {source_system}: aceita so letras, "
                "numeros, '.', '_' e '-' (1 a 200 caracteres), sem '..'. Caracteres "
                "como aspas, espacos, quebras de linha, '/', '?' ou '#' nao sao permitidos."
            ),
            raw="",
            is_mock=False,
            is_fallback=True,
        )
    return None
