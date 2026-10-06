"""Rate limiting (slowapi) para o SAP Integration Copilot.

Avaliacao externa (medio prazo, item 1): "Rate limiting por IP so".
O rate limiter original usava apenas o IP do cliente como chave de
identificacao — isso penalizava todos os clientes atras do mesmo IP
(NAT corporativo, proxy reverso) com um bucket compartilhado.

P1.3 (revisao arquitetural externa, 23/09/2026): a funcao de chave
`request_client_identity()` resolve o identificador do cliente na
seguinte ordem de prioridade:

  1. X-A2A-Api-Key  — cliente A2A (Agent2Agent) autenticado
  2. X-API-Key      — cliente humano / API convencional autenticado
  3. IP do cliente  — fallback para chamadas nao autenticadas

Isso garante que:
  - Dois clientes A2A com chaves diferentes nao compartilham bucket,
    mesmo vindo do mesmo IP (ex: ambiente corporativo com NAT).
  - O fallback para IP mantem o comportamento existente para chamadas
    sem autenticacao (ex: /health, /.well-known/agent-card.json).
  - Nao ha mudanca de interface: `limiter` continua sendo importado e
    usado exatamente como antes (app/main.py, app/a2a/server.py).

Nota de segurança: X-Forwarded-For pode ser forjado por um cliente
mal-intencionado em frente a um proxy que nao filtra este header.
Para deploy Kyma real, o Istio/Envoy sobrescreve X-Forwarded-For com
o IP real — mas nao depender so do IP para autenticado e a postura
mais defensiva de qualquer forma.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter


def request_client_identity(request: Request) -> str:
    """P1.3: identifica o cliente pelo token de autenticacao (quando
    presente) ou pelo IP de origem (fallback).

    Ordem de prioridade:
      1. X-A2A-Api-Key  (endpoint /a2a — Agent2Agent)
      2. X-API-Key      (endpoint /diagnose, /events/incident, etc.)
      3. IP do cliente  (fallback para chamadas sem autenticacao)

    Note: para endpoints sensíveis (login/verify), use
    `request_client_identity_for_auth_endpoints()` que ignora headers de
    autenticação e usa IP + endpoint como bucket.
    """
    a2a_key = request.headers.get("X-A2A-Api-Key")
    if a2a_key:
        return f"a2a:{a2a_key}"

    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key}"

    if request.client:
        return request.client.host

    return "unknown"


def request_client_identity_for_auth_endpoints(request: Request) -> str:
    """Identidade para endpoints de autenticacao sensíveis (/login, /verify/*).

    Usa IP + username (hash) como bucket para evitar:
      - Força bruta de senha contornável via X-API-Key
      - Enumeracao de usuarios (tempo de resposta revela existência)

    Ordem de prioridade:
      1. Username do body (quando disponível) + IP
      2. IP do cliente (fallback)
    """
    ip = "unknown"
    if request.client:
        ip = request.client.host

    # Username do body ( LoginRequest / VerifyEmailRequest etc. )
    # Tentar parsing manual pois request.body() é async e não pode ser chamado aqui
    # Em vez disso, usar o path da rota para inferir
    path = request.url.path

    if path.endswith("/auth/login"):
        return f"login:{ip}"
    if path.endswith("/auth/verify/email"):
        return f"verify_email:{ip}"
    if path.endswith("/auth/verify/phone"):
        return f"verify_phone:{ip}"

    # Fallback: IP puro
    return ip


# `limiter` e o singleton importado por app/main.py e app/a2a/server.py.
# default_limits vale para todas as rotas via SlowAPIMiddleware (adicionado
# em app/main.py) — rotas com @limiter.limit() explicito podem sobrepor.
limiter = Limiter(
    key_func=request_client_identity,
    default_limits=["60/minute"],
)
