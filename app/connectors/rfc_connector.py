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
"""

from app.config import settings
from app.connectors.base import ConnectorResult, SAPConnector
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
        if self.use_real:
            return self._fetch_real(identifier)
        return _MOCK_SCENARIOS.get(identifier, _DEFAULT)

    def _fetch_real(self, identifier: str) -> ConnectorResult:
        """Chamada RFC real via pyrfc - esqueleto documentado, nao
        exercitado em CI (exige SDK/credenciais reais de um sistema
        SAP, que este projeto de portfolio nao possui). Mantido
        separado de `fetch()` para o caminho mock continuar 100%
        testavel sem essa dependencia.
        """
        conn = pyrfc.Connection(
            ashost=settings.sap_ashost,
            sysnr=settings.sap_sysnr,
            client=settings.sap_client,
            user=settings.sap_user,
            passwd=settings.sap_password,
        )
        try:
            result = conn.call("BAPI_IDOC_STATUS", IDOCNUMBER=identifier)
        finally:
            conn.close()

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
