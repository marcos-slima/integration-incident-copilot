"""DA-45: resolucao de ORIGIN real de um provider de LLM.

MODULO NEUTRO DE PROPOSITO

`app/llm/gateway.py` importa de `app/llm/factory.py` (o gateway monta o
cliente). Portanto `factory.py` NAO pode importar `gateway.py` - seria
ciclo. Mas a resolucao de origin e' dependencia de ambos: o factory
precisa dela para descobrir as capacidades do destino (DA-45) e o
gateway precisa dela para a politica de soberania (DA-43).

Por isso as duas funcoes vivem aqui, sem import de nenhum dos dois, e
`gateway.py` as re-exporta. `from app.llm.gateway import
normalize_origin` continua funcionando - os testes existentes de DA-43
nao mudam.

POR QUE ORIGIN E NAO ROTULO

DA-43 ja estabeleceu: a resposta a "para onde vai meu dado?" e' uma URL,
nao um nome. 'openai' e' um rotulo que cobre api.openai.com, vLLM
self-hosted, Groq, Gemini, LiteLLM, um gateway corporativo - todos via
OPENAI_BASE_URL. Policy por rotulo trataria todos como a mesma coisa, o
que e' exatamente o que DA-43 existe para impedir. A mesma logica se
aplica as CAPACIDADES (DA-45): a resposta a "este destino aceita
`seed`?" tambem e' por origin, porque `seed` e' aceito pela OpenAI e
recusado com 400 pelo endpoint OpenAI-compatible do Gemini.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_origin(raw: str) -> str:
    """Normaliza uma URL para 'scheme://host[:port]' comparavel.

    Descarta caminho, query, fragmento e credenciais (userinfo) - o que
    interessa para governanca e ONDE o dado sai, nao o recurso nem o
    segredo. Compara minusculas e omite a porta default do scheme, para
    que 'https://api.openai.com', 'https://API.OpenAI.com/' e
    'https://api.openai.com:443' sejam a MESMA origem.
    """
    if not raw or not raw.strip():
        return ""
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return ""
    scheme = (parts.scheme or "").lower()
    # host-only: descarta userinfo (user:pass@) e preserva a porta
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return ""
    port = parts.port
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def resolve_provider_origin(provider: str, cfg=None) -> str:
    """DA-43: devolve a ORIGIN REAL do destino de um provider logico.

    'openai' e um rotulo, nao um lugar: com OPENAI_BASE_URL vazio o dado
    vai para api.openai.com, mas com ela preenchida pode ir para vLLM
    self-hosted, Groq, Gemini, LiteLLM... A policy de soberania e
    avaliada sobre esta origem, nunca sobre o rotulo.

    Provider desconhecido devolve "" - nunca um palpite. Um destino nao
    reconhecido e' fail-closed por definicao: sem origin nao ha como
    autorizar, e a politica de DA-43 trata "" como negado.
    """
    from app.config import settings

    c = cfg or settings
    if provider == "ollama":
        return normalize_origin(c.ollama_host)
    if provider == "openai":
        return normalize_origin(c.openai_base_url) or "https://api.openai.com"
    if provider == "azure_openai":
        return normalize_origin(c.azure_openai_endpoint)
    return ""


def is_loopback_origin(origin: str) -> bool:
    """True se a origin aponta para o proprio host.

    Usado para classificar rota de laboratorio: destino local nao
    configura egress de dado e por isso nao entra na allowlist de
    DA-43 (que so restringe origin REMOTA). Nao confundir "local" com
    "confiavel": o que se evita aqui e o dado sair da maquina, nao a
    qualidade do modelo.
    """
    if not origin:
        return False
    try:
        host = urlsplit(origin).hostname or ""
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
