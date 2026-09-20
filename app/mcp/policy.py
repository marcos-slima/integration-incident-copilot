"""Capability Registry + Agent Execution Policy (DA-27) do servidor
MCP (app/mcp/server.py, DA-19).

Uma terceira revisao arquitetural externa apontou que faltava, antes
de qualquer evolucao do MCP para tools de ESCRITA, um modelo de risco
por ferramenta - hoje so existe autenticacao de TRANSPORTE (X-API-Key
compartilhada, RequireApiKeyMiddleware em app/mcp/server.py), sem
distincao entre "consultar um diagnostico" e uma tool hipotetica como
"reiniciar um iFlow" ou "fechar um chamado". A consequencia pratica de
um prompt injection muda de "diagnostico errado" (hoje, so tools
read-only) para "alterar SAP/reiniciar iFlow/executar operacao" no dia
em que uma tool de escrita for adicionada - a partir dai, prompt
injection deixa de ser so um problema de qualidade de resposta e vira
um problema de AUTORIZACAO OPERACIONAL.

Este modulo cria a base para isso, mesmo as duas tools atuais sendo
100% read-only (nao ha nada de destrutivo pra bloquear hoje):

- CAPABILITY_REGISTRY: uma entrada `ToolPolicy` por tool exposta,
  declarando risk_level, se e destrutiva, quais scopes exige, se
  exige aprovacao humana explicita, e a sensibilidade do dado que
  manipula (mesma classificacao confidential/public do AI Gateway,
  DA-26 - ver app/llm/gateway.py::classify_sensitivity).
- enforce(tool_name, context): FAIL-CLOSED - uma tool sem entrada no
  registry e negada por padrao, nunca permitida por omissao. Verifica
  scopes concedidos e aprovacao (quando exigida) antes de deixar a
  tool executar.
- Toda tool NOVA (especialmente qualquer futura tool de ESCRITA - ex:
  "restart_iflow", "close_ticket") DEVE ganhar uma entrada aqui
  ANTES de ser exposta via @mcp.tool() em app/mcp/server.py - e o
  proprio fail-closed de enforce() que impede alguem de esquecer isso
  (a tool simplesmente seria negada em runtime, nao silenciosamente
  permitida).

Nao-objetivos explicitos desta v1 (backlog em aberto, ver
learnings.md do projeto):
- Granularidade de scope por CHAVE de API: hoje ha uma unica
  X-API-Key (settings.api_key) compartilhada por todo o servidor MCP -
  DEFAULT_EXECUTION_CONTEXT reflete isso (todo caller autenticado
  recebe os MESMOS scopes de leitura). Multiplas chaves com scopes
  diferentes exigiria um esquema de credenciais mais rico (ex:
  OAuth2/TokenVerifier, que app/mcp/server.py ja documenta como
  sobre-engenharia para o estagio atual do projeto).
- Fluxo de aprovacao humana de verdade (uma UI/endpoint onde um
  humano aprova uma execucao pendente antes dela rodar) - o campo
  `approved` de ExecutionContext existe para o enforce() ja saber
  verificar isso, mas nao ha, ainda, nenhum mecanismo real que o
  preencha com True (nenhuma tool atual exige aprovacao, entao isso
  nunca e exercitado em producao hoje).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high", "critical"]
DataSensitivity = Literal["public", "confidential"]


class PolicyDeniedError(Exception):
    """Levantada quando o Capability Registry ou a Agent Execution
    Policy negam a execucao de uma tool - engloba tanto "tool nao
    registrada" (fail-closed) quanto "scope/aprovacao insuficientes"."""


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    risk_level: RiskLevel
    destructive: bool
    scopes_required: tuple[str, ...]
    approval_required: bool
    data_sensitivity: DataSensitivity
    timeout_seconds: float = 30.0
    max_retries: int = 0


@dataclass(frozen=True)
class ExecutionContext:
    """'Quem' esta chamando a tool. granted_scopes vem hoje do MESMO
    X-API-Key ja usado por todo o servidor MCP (uma unica chave
    concede um conjunto fixo de scopes - ver nao-objetivo no
    docstring do modulo). approved: True somente quando uma tool com
    approval_required=True foi explicitamente aprovada por um
    mecanismo externo (nao implementado nesta v1)."""

    granted_scopes: frozenset[str]
    approved: bool = False


# Capability Registry: uma entrada por tool exposta pelo servidor MCP
# (app/mcp/server.py). enforce() e FAIL-CLOSED - uma tool ausente
# daqui e negada, nunca permitida por omissao.
CAPABILITY_REGISTRY: dict[str, ToolPolicy] = {
    "diagnose_incident": ToolPolicy(
        name="diagnose_incident",
        risk_level="low",
        destructive=False,
        scopes_required=("integration.read",),
        approval_required=False,
        # Pode envolver dado real de conector (nao mock) na resposta -
        # mesma classificacao usada pelo AI Gateway (DA-26).
        data_sensitivity="confidential",
        timeout_seconds=60.0,
    ),
    "list_connectors": ToolPolicy(
        name="list_connectors",
        risk_level="low",
        destructive=False,
        scopes_required=("integration.read",),
        approval_required=False,
        # So inspeciona config local (settings) - nunca faz chamada de
        # rede nem devolve dado de incidente algum, ver docstring da
        # tool em app/mcp/server.py.
        data_sensitivity="public",
        timeout_seconds=5.0,
    ),
}

# Contexto default usado quando o caller (app/mcp/server.py) nao passa
# um explicito - reflete o estado ATUAL do servidor MCP: uma unica
# X-API-Key valida (ja verificada por RequireApiKeyMiddleware antes da
# tool ser chamada) concede escopo de LEITURA - nenhuma tool de
# escrita existe ainda, entao "integration.write" nunca e concedido
# por padrao, mesmo sem nenhuma tool exigi-lo hoje.
DEFAULT_EXECUTION_CONTEXT = ExecutionContext(granted_scopes=frozenset({"integration.read"}))


def enforce(tool_name: str, context: ExecutionContext = DEFAULT_EXECUTION_CONTEXT) -> ToolPolicy:
    """Verifica se `context` pode executar `tool_name` - levanta
    PolicyDeniedError se nao. Retorna a ToolPolicy correspondente
    quando permitido (o caller pode usar timeout_seconds/max_retries
    dela, por exemplo).

    FAIL-CLOSED: uma tool sem entrada em CAPABILITY_REGISTRY e negada
    por padrao - nunca permitida so por nao ter sido explicitamente
    bloqueada."""
    policy = CAPABILITY_REGISTRY.get(tool_name)
    if policy is None:
        raise PolicyDeniedError(
            f"Tool '{tool_name}' nao esta no Capability Registry - negada "
            "por padrao (fail-closed). Registre a tool em "
            "app/mcp/policy.py::CAPABILITY_REGISTRY antes de expor via @mcp.tool()."
        )

    missing_scopes = set(policy.scopes_required) - context.granted_scopes
    if missing_scopes:
        raise PolicyDeniedError(
            f"Tool '{tool_name}' exige escopo(s) {sorted(missing_scopes)}, nao "
            "concedido(s) neste contexto."
        )

    if policy.approval_required and not context.approved:
        raise PolicyDeniedError(
            f"Tool '{tool_name}' exige aprovacao explicita antes de executar "
            f"(approval_required=True, risk_level='{policy.risk_level}') - nao "
            "aprovada neste contexto."
        )

    return policy
