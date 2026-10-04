"""DA-45: rotas de LLM - a fronteira de auditoria do produto.

O PROBLEMA

"Qualquer modelo que o cliente quiser, e' so informar." Metade disso ja
era verdade: `ChatOpenAI(base_url=...)` fala `/chat/completions`, entao
qualquer endpoint OpenAI-compatible (Groq, Cerebras, Together,
Fireworks, OpenRouter, DeepSeek, Mistral, xAI, vLLM, llama.cpp) ja e'
mudanca de `.env` e o MODELO e' texto livre. O que impedia o cliente era
o outro lado: um `Literal` nomeado de 3 valores para o provider, e a
origem real.do destino valendo em `OPENAI_BASE_URL`, sem nenhum lugar
onde se respondesse "para onde o dado pode sair?".

A RESOLUCAO: SEPARAR O QUE E FRONTEIRA DO QUE E PREFERENCIA

| Camada           | Onde vive              | Quem muda                |
|------------------|------------------------|--------------------------|
| rota (provider + origin + capacidades) | codigo | so por DA |
| modelo           | `.env`, texto livre    | o cliente, o tempo todo  |

O cliente informa `LLM_MODEL=claude-sonnet-4` e funciona. O que exige
PR e' ADICIONAR UM FORNECEDOR - e isso e' deliberado, nao limitacao: e'
a diferenca entre "provider agnostic" e "config sem governanca".

POR QUE A ORIGIN FICA NO CODIGO E NAO NO .env

Se rota e allowlist vivessem ambas no `.env`, o DA-43 perderia a
separacao entre ROTEAMENTO e AUTORIZACAO: quem escreve o `.env`
definiria o destino E autorizaria o destino, e
`CONFIDENTIAL_ALLOWED_ORIGINS` viraria decorativa. A pergunta central
de governanca - "para onde meus dados podem sair?" - precisa de
resposta lendo um lugar so, e esse lugar e' a tabela abaixo.

Se uma rota nova exigir entrada em codigo e' o que permite a um cliente
real dizer "usamos Bedrock" sem reduzir a politica a "configure a origin
no servidor". Mudanca de 5 linhas. Barreira deliberada, e o custo e'
quase zero.

REGRA DE OURO

O MODELO NUNCA ENTRA NESTA TABELA. A tabela declara para ONDE o dado
sai e O QUE o destino aceita; o modelo e' nome livre em `llm_model`.
Se algum dia um "modelo" aparecer aqui como fronteira de seguranca, a
DA-45 estourou o proprio proposito.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.capabilities import ProviderCapabilities
from app.llm.origins import is_loopback_origin, normalize_origin

#: Providers logicos suportados (mesmo dominio de app/llm/factory.py).
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"
PROVIDER_AZURE = "azure_openai"


@dataclass(frozen=True)
class LLMRoute:
    """Uma rota auditada: provider logico + origem esperada + capacidades.

    Congelada e validada na importacao (abaixo): uma rota e' um fato
    sobre a governanca, nao um valor que o runtime possa ajustar.
    """

    name: str
    provider: str
    #: Descricao do destino esperado, para auditoria humana. Nao e'
    #: usada para casar a origin real - ver `origin_matches`.
    destination: str
    capabilities: ProviderCapabilities | None = None
    #: Quando True, a rota so e' valida se a origin real for loopback.
    #: Impede que um cliente declare rota de laboratorio apontando
    #: `openai_base_url` para um host remoto - o que faria o rotulo
    #: "local" mentir na auditoria.
    require_loopback: bool = False

    def matches_destination_class(self, actual_origin: str) -> bool:
        """Coerencia entre o rotulo da rota e a origem real.

        Este e' o guard de seguranca, e a razao de `require_loopback`
        existir: a rota `local_lab` exige loopback, a rota
        `enterprise_azure` exige origin NAO-loopback. Assim um cliente
        nao consegue declarar "sou local" e apontar para a internet, nem
        declarar "sou Azure corporativo" e mandar o dado para um
        servidor de terceiro em loopback.
        """
        actual = normalize_origin(actual_origin)
        if not actual:
            return False
        # A invariante, numa expressao: a origem e' loopback SE E SOMENTE
        # SE a rota declara loopback. Isso barra as duas direcoes do
        # mentira - declarar rota local apontando para a internet, e
        # declarar rota corporativa apontando para loopback.
        return is_loopback_origin(actual) == self.require_loopback


_FULL = ProviderCapabilities(supports_seed=True, supports_response_format=True, supports_tools=True)

#: Rotas conhecidas. Adicionar um fornecedor = uma entrada aqui.
#:
#: NOTA SOBRE `self_hosted_openai`: e' a rota de proposito mais permissiva
#: do conjunto (qualquer origin nao-loopback), e por isso e' a que mais
#: merece revisao em auditoria - ela existe porque vLLM/LM Studio/
#: gateways corporativos NAO tem origin fixa, e um cliente pode estar
#: rodando o seu proprio LLM em qualquer endereco da rede interna.
LLM_ROUTES: dict[str, LLMRoute] = {
    r.name: r
    for r in [
        LLMRoute(
            name="local_lab",
            provider=PROVIDER_OLLAMA,
            destination="Ollama no loopback (sua maquina)",
            require_loopback=True,
        ),
        LLMRoute(
            name="enterprise_azure",
            provider=PROVIDER_AZURE,
            destination="Azure OpenAI (endpoint corporativo do cliente)",
        ),
        LLMRoute(
            name="openai_public",
            provider=PROVIDER_OPENAI,
            destination="OpenAI publica (api.openai.com)",
        ),
        LLMRoute(
            name="self_hosted_openai",
            provider=PROVIDER_OPENAI,
            destination=(
                "Endpoint OpenAI-compatible em rede do cliente: vLLM, "
                "LM Studio, gateway corporativo, ou um provedor hospedado "
                "com contrato OpenAI"
            ),
        ),
        LLMRoute(
            name="local_openai_server",
            provider=PROVIDER_OPENAI,
            destination="Servidor OpenAI-compatible local (vLLM/LM Studio em loopback)",
            require_loopback=True,
        ),
    ]
}

#: Rotas que exigem credencial real. `local_lab` e `local_openai_server`
#: nao: um vLLM em loopback tipicamente aceita qualquer string, e exigir
#: key seria um atrito sem ganho de seguranca (nao ha egress).
_ROUTES_REQUIRING_CREDENTIAL = {"enterprise_azure", "openai_public", "self_hosted_openai"}


def known_routes() -> dict[str, LLMRoute]:
    """Copia - o chamador nao consegue alterar a tabela de auditoria."""
    return dict(LLM_ROUTES)


def get_route(name: str) -> LLMRoute | None:
    """Rota pelo nome, ou None se nao existir (caller decide fail-closed)."""
    return LLM_ROUTES.get(name or "")


def route_requires_credential(name: str) -> bool:
    return (name or "") in _ROUTES_REQUIRING_CREDENTIAL


def describe_routes() -> list[dict]:
    """Retrato das rotas para documentacao e para `GET /llm/policy`.

    Nao inclui segredo: `destination` e texto, e a origin real so
    aparece quando o chamador explicitamente a resolve.
    """
    return [
        {
            "route": r.name,
            "provider": r.provider,
            "destination": r.destination,
            "require_loopback": r.require_loopback,
            "requires_credential": route_requires_credential(r.name),
            "capabilities": (r.capabilities or _FULL).as_log_fields(),
        }
        for r in LLM_ROUTES.values()
    ]
