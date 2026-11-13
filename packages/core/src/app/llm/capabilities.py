"""DA-45: perfil de CAPACIDADE por origin de destino.

O PROBLEMA QUE ISTO SUBSTITUI

`llm_send_seed: bool` era um unico booleano GLOBAL para um fato que e'
por destino. Ele nasceu de uma colisao real: o endpoint
OpenAI-compatible do Gemini responde **400 "Unknown name \\"seed\\""** e
alguns gateways self-hosted (vLLM/LM Studio) tambem, enquanto OpenAI e
Azure aceitam. Como o fato nao e' global, a solucao global e' errado
mesmo funcionando: desligar `seed` para atender o Gemini tira o
determinismo de DA-2 de TODOS os outros providers. Foi por isso que o
eval passava `LLM_SEND_SEED=0` no processo inteiro - o preco de uma
capacidade por provider expressada como flag de processo.

DA-45 troca o booleano global por uma TABELA POR ORIGIN, pelo mesmo
motivo que DA-43evaluate a politica por origin e nao por rotulo: a
pergunta "este destino aceita `seed`?" so tem resposta olhando a URL.

CONTRATO

- `seed` continua sendo enviado por DEFAULT (DA-2 e' invariante do
  projeto). A tabela remove casos CONHECIDOS, nunca adiciona
  conservativeismo silencioso: um destino desconhecido se comporta
  exatamente como hoje, e o override explicito continua disponivel.
- A decisao e' por origin, nao por rotulo: 'openai' apontando para
  generativelanguage.googleapis.com e' outra coisa de 'openai'
  apontando para api.openai.com, e a tabela sabe disso.
- `llm_send_seed` tri-state: None = consultar a tabela; True/False =
  override explicito, que sempre vence. O override existe para o caso
  legitimo de "descobri que meu gateway recusa seed e nao quero
  esperar eu registrar a origin aqui".
"""

from __future__ import annotations

from dataclasses import dataclass

#: Estado sem informacao. Mantido separado de False porque "nao aceita
#: seed" e "nao sei se aceita" sao coisas diferentes: o primeiro e' um
#: fato verificado, o segundo e' ausencia de dado.
UNKNOWN = None


@dataclass(frozen=True)
class ProviderCapabilities:
    """O que o destino aceita no corpo da requisicao.

    O motivo de ser dataclass congelada e o mesmo de EscalationDecision:
    este e' um dado lido de uma tabela de conformidade com provedores
    externos, e nao deve poder ser ajustado em runtime por um
    chamador que ache conveniente.
    """

    supports_seed: bool
    supports_response_format: bool
    supports_tools: bool

    def as_log_fields(self) -> dict:
        return {
            "supports_seed": self.supports_seed,
            "supports_response_format": self.supports_response_format,
            "supports_tools": self.supports_tools,
        }


#: OpenAI e Azure aceitam o contrato completo da API OpenAI.
_FULL = ProviderCapabilities(supports_seed=True, supports_response_format=True, supports_tools=True)

#: Default para origin desconhecida. Preserva o comportamento ATUAL
#: (seed enviado) para nao mudar nada silenciosamente: o destino
#: desconhecido se comporta como sempre, e continua podendo ser
#: ajustado por `llm_send_seed` explicito ou por entrada na tabela.
_DEFAULT = _FULL

#: Capacidades por ORIGIN, nao por rotulo. Key ja normalizada
#: (app/llm/origins.py::normalize_origin -> 'scheme://host[:port]').
#:
#: Entradas aqui sao fatos verificados por tentativa, nao suposicoes:
#: em 2026-09-28 o endpoint OpenAI-compatible do Gemini respondeu 400
#: "Unknown name \"seed\"" - e o erro e' da request INTEIRA, sem
#: fallback possivel.
CAPABILITIES_BY_ORIGIN: dict[str, ProviderCapabilities] = {
    # Gemini: recusa `seed`. response_format e tool-calling funcionam
    # (o comparativo de 2026-09-28 produziu diagnostico estruturado e
    # o loop ReAct executou com tool de busca web).
    "https://generativelanguage.googleapis.com": ProviderCapabilities(
        supports_seed=False,
        supports_response_format=True,
        supports_tools=True,
    ),
}

#: Loopback e sempre o proprio host: sem egress, contrato completo
#: assumido. Registrado explicitamente em vez de cair no _DEFAULT para
#: que a linha "por que isto existe" fique visivel na tabela.
CAPABILITIES_BY_ORIGIN.update(
    {
        "http://localhost": _FULL,
        "http://localhost:11434": _FULL,
        "http://127.0.0.1": _FULL,
        "http://127.0.0.1:11434": _FULL,
    }
)


def capabilities_for_origin(origin: str) -> ProviderCapabilities:
    """Capacidades do destino, por origin normalizada.

    Devolve `_DEFAULT` para origin desconhecida - ver a nota sobre o
    default acima: o objetivo e' nunca mudar o comportamento de um
    destino que funcionava, nao adivinhar com base em desconfianca.
    """
    if not origin:
        return _DEFAULT
    return CAPABILITIES_BY_ORIGIN.get(origin, _DEFAULT)


def should_send_seed(origin: str, override: bool | None = None) -> bool:
    """Decide se `seed` vai no corpo da requisicao (DA-2).

    `override` e' o `llm_send_seed` tri-state: None = pergunta a
    tabela; True/False = respeita o operador, sempre. O override vence
    porque quem opera o gateway conhece o gateway dele melhor do que
    uma tabela versionada junto com o codigo.
    """
    if override is not None:
        return bool(override)
    return capabilities_for_origin(origin).supports_seed
