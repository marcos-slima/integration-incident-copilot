"""Conector RFC - simula chamadas RFC/BAPI e status de IDoc (modo
demo/mock, default) ou delega para uma chamada RFC real via `pyrfc`
(modo real, opt-in) quando disponivel e configurado.

Por que isso importa para o posicionamento do projeto: clientes ainda
em ECC on-premise (sem BTP, sem Integration Suite/CPI) normalmente so
tem RFC/BAPI como via de acesso automatizado ao sistema - e essa e
justamente a base de clientes que nao consegue adotar SAP AI Core (que
exige HANA Cloud). Por isso o conector RFC, nao o OData, e o caminho
mais relevante para esse publico-alvo.

Modo real (`RFCConnector(use_real=True)`): requer o pacote `pyrfc` e o
SAP NetWeaver RFC SDK instalado no sistema operacional (binario da SAP,
nao distribuido via PyPI - por isso nao esta em pyproject.toml). Sem
isso, `use_real=True` falha alto e claro (ConfigurationError) em vez de
silenciosamente cair no mock - evita o cliente achar que esta
conectado a um sistema real quando nao esta. A chamada real (comentada
abaixo) mostra a forma esperada de uma leitura de status de IDoc via
BAPI de monitoramento (`BAPI_IDOC_STATUS` / `RFC_READ_TABLE` sobre
EDIDC/EDID4, dependendo do que o cliente autorizar).

ATUALIZACAO (setembro/2026): status do bloqueio REVERTIDO. O SDK
NetWeaver RFC 7.50 PL19 foi obtido via S-user com contrato SAP ativo
e instalado em /usr/local/sap/nwrfcsdk. O `pyrfc` 3.3.1 (yanked no
PyPI, mas instalavel via versao especifica) compilou contra Python 3.12
e carregou o SDK real sem erros. Validado: RFCConnector(use_real=True)
inicializa e passa pela validacao de parametros do proprio SDK SAP
(erro RFC_INVALID_PARAMETER por falta de host destino - esperado sem
sistema SAP real disponivel, mas prova que o binding funciona).

O bloqueio anterior era pessoal (sem S-user/contrato SAP) - confirmado
que nao e um bloqueio tecnico do produto. Cliente real com licenca SAP
ativa usa esse conector sem restricao adicional.

O QUE "VALIDADO" SIGNIFICA AQUI (validacao 2026-10-07, M-10): o binding
pyrfc + SDK carrega e o logon funciona (RFC_SYSTEM_INFO no ABAP Trial). A
funcao que este conector chama, `BAPI_IDOC_STATUS`, NUNCA foi executada
contra um sistema real (indisponivel no Trial) - nome, parametros e formato
da tabela STATUS sao a hipotese documentada, nao um contrato verificado. Se
o modulo nao existir no sistema do cliente, o resultado agora e um erro
legivel (FU_NOT_FOUND), nao um 500.

Tambem em M-10: breaker de conector (falha de comunicacao conta; logon e
erro ABAP nao, porque sao respostas de um sistema de pe), excecoes do pyrfc
traduzidas em ConnectorResult e timeout por chamada (RFC_TIMEOUT_SECONDS).
Continua uma conexao por chamada (sem pool): o volume de diagnosticos e
baixo e o pool do SDK exigiria gerenciar estado entre requisicoes.
"""

from app.config import settings
from app.connectors.base import (
    ConnectorResult,
    SAPConnector,
    circuit_breaker_guard,
    connector_circuit_breaker,
    validate_identifier_charset,
)
from app.exceptions import ConfigurationError

try:
    import pyrfc  # type: ignore[import-not-found]

    HAS_PYRFC = True
except ImportError:
    pyrfc = None
    HAS_PYRFC = False

_MOCK_SCENARIOS: dict[str, ConnectorResult] = {
    "RFC-CONN-REFUSED-DEMO": ConnectorResult(
        source_system="RFC",
        status="error",
        error_code="RFC_COMMUNICATION_FAILURE",
        message="Connection refused ao testar destino RFC via SM59",
        raw=(
            "CALL FUNCTION 'RFC_PING' DESTINATION 'DEST_QA'\n"
            "EXCEPTION: COMMUNICATION_FAILURE\n"
            "Partner '10.20.30.40:3300' not reached"
        ),
    ),
    "RFC-IDOC-51-DEMO": ConnectorResult(
        source_system="RFC",
        status="error",
        error_code="51",
        message="IDoc com status 51 - Application Document Not Posted",
        raw=(
            "IDOC: 0000000001234567\n"
            "STATUS: 51\n"
            "MESSAGE: Erro ao criar documento de aplicacao - "
            "material 4711 nao cadastrado no centro 1000"
        ),
    ),
    "RFC-GWY-POOL-TIMEOUT-DEMO": ConnectorResult(
        source_system="RFC",
        status="error",
        error_code="RFC_GWY_POOL_EXHAUSTED",
        message="Timeout no RFC Gateway - pool de processos de dialogo esgotado",
        raw=(
            "CALL FUNCTION 'Z_INTEGRATION_SYNC' DESTINATION 'DEST_PRD'\n"
            "EXCEPTION: SYSTEM_FAILURE\n"
            "gwy/max_conn atingido - nenhum processo de dialogo livre no "
            "destino em ate 60s; comum em ECC on-premise sob pico de carga "
            "batch concorrente com integracao sincrona"
        ),
    ),
}

_DEFAULT = ConnectorResult(
    source_system="RFC",
    status="error",
    error_code="RFC_ERROR",
    message="Erro generico simulado em chamada RFC (identificador nao reconhecido)",
    raw="RFC call failed (dados mock, identificador desconhecido)",
    is_fallback=True,
)


class RFCConnector(SAPConnector):
    """`use_real=True` forca o caminho RFC real explicitamente na
    construcao. Alem disso, `fetch()` segue o MESMO criterio de
    auto-habilitacao usado por `ODataConnector.fetch()`
    (`self.use_real or settings.odata_service_url`): quando SAP_ASHOST
    estiver configurado no .env, o modo real e usado mesmo que o
    caller nao passe use_real=True - isso importa porque
    `app.connectors.get_connector("rfc")` (usado por /diagnose) sempre
    instancia `RFCConnector()` sem argumentos, entao antes desse
    alinhamento nao havia NENHUM jeito de /diagnose alcancar o
    conector RFC real, mesmo com SAP_ASHOST configurado."""

    def __init__(self, use_real: bool = False):
        self.use_real = use_real
        if use_real and not HAS_PYRFC:
            raise ConfigurationError(
                "RFCConnector(use_real=True) exige o pacote 'pyrfc' e o SAP "
                "NetWeaver RFC SDK instalado no sistema operacional (binario "
                "distribuido pela SAP, nao via PyPI). Sem isso, use "
                "RFCConnector() (modo demo/mock, default) para prototipar "
                "sem depender de um sistema SAP real."
            )

    def fetch(self, identifier: str) -> ConnectorResult:
        if self.use_real or settings.sap_ashost:
            if not HAS_PYRFC:
                raise ConfigurationError(
                    "SAP_ASHOST esta configurado no .env, mas o pacote "
                    "'pyrfc' e o SAP NetWeaver RFC SDK nao estao instalados "
                    "no sistema operacional (binario distribuido pela SAP, "
                    "nao via PyPI) - sem isso nao ha como fazer a chamada "
                    "RFC real. Remova SAP_ASHOST do .env para usar o modo "
                    "demo/mock, ou instale pyrfc + o SDK."
                )
            return self._fetch_real(identifier)
        return _MOCK_SCENARIOS.get(identifier, _DEFAULT)

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        """Chamada RFC real via pyrfc - esqueleto documentado, nao
        exercitado em CI (exige SDK/credenciais reais de um sistema
        SAP, que este projeto de portfolio nao possui). Mantido
        separado de `fetch()` para o caminho mock continuar 100%
        testavel sem essa dependencia.
        """
        if (blocked := circuit_breaker_guard("RFC")) is not None:
            return blocked
        if (invalid := validate_identifier_charset(identifier, "RFC")) is not None:
            return invalid
        try:
            conn = pyrfc.Connection(
                ashost=settings.sap_ashost,
                sysnr=settings.sap_sysnr,
                client=settings.sap_client,
                user=settings.sap_user,
                passwd=settings.sap_password,
            )
            try:
                result = conn.call(
                    "BAPI_IDOC_STATUS",
                    options={"timeout": settings.rfc_timeout_seconds},
                    IDOCNUMBER=identifier,
                )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - toda excecao do pyrfc vira resultado
            return _rfc_error_result(exc)
        connector_circuit_breaker.record_success("RFC")

        status_records = result.get("STATUS", [])
        latest = status_records[-1] if status_records else {}
        status_code = str(latest.get("STATUS", ""))
        return ConnectorResult(
            source_system="RFC",
            status="ok" if status_code in {"03", "53"} else "error",
            error_code=status_code or None,
            message=latest.get("STATXT", "Sem mensagem de status retornada"),
            raw=str(result),
            is_mock=False,
        )


def _rfc_error_result(exc: Exception) -> ConnectorResult:
    """M-10: traduz excecoes do pyrfc em ConnectorResult.

    Antes nenhuma era tratada: logon recusado, destino fora do ar ou modulo
    inexistente viravam 500 no /diagnose. Classes do pyrfc (CommunicationError,
    LogonError, ABAPApplicationError, ABAPRuntimeError, ExternalRuntimeError)
    sao reconhecidas pelo nome - o modulo nao existe quando o SDK nao esta
    instalado, e o teste injeta classes falsas com os mesmos nomes."""
    kind = type(exc).__name__
    key = getattr(exc, "key", "") or ""
    message = getattr(exc, "message", "") or str(exc)
    if kind == "CommunicationError":
        # Indisponibilidade de rede/gateway: e o que o breaker existe para cortar.
        connector_circuit_breaker.record_failure(
            "RFC",
            settings.connector_circuit_failure_threshold,
            settings.connector_circuit_cooldown_seconds,
        )
        code = "RFC_COMMUNICATION_FAILURE"
    elif kind == "LogonError":
        code = "RFC_LOGON_FAILURE"
    elif kind in {"ABAPApplicationError", "ABAPRuntimeError"}:
        code = key or "RFC_ABAP_ERROR"
    else:
        code = key or "RFC_ERROR"
    return ConnectorResult(
        source_system="RFC",
        status="error",
        error_code=code,
        message=f"Chamada RFC falhou ({kind}): {message}"[:500],
        raw="",
        is_mock=False,
        is_fallback=True,
    )
