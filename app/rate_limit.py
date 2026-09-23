"""Instancia compartilhada do slowapi Limiter.

Extraido de app/main.py (avaliacao externa - medio prazo, item 1:
"Rate limit global e por endpoint (incluir /a2a)") para um modulo
proprio, sem dependencias do resto do app - app/main.py monta a rota
raiz e app/a2a/server.py monta a rota /a2a, e ambas precisam decorar
endpoints com @limiter.limit(...). Colocar o Limiter em app/main.py
(como estava antes) criaria um import circular: app/a2a/server.py
precisaria importar de app/main.py, que por sua vez importa o router
de app/a2a/server.py.

§4.2 (avaliacao externa §3.2): key_func alterado de get_remote_address
para _key_by_api_key_or_ip:
- Problema: atras do APIRule/ingress do Kyma, todos os clientes podem
  aparecer com o mesmo IP (o do proxy), tornando o limite global (todos
  compartilham o bucket de um IP so) ou, se X-Forwarded-For nao e
  validado com trusted_proxies, qualquer cliente pode rotacionar IPs
  forjando o header e contornar o limite.
- Solucao: usar o header X-API-Key como chave de rate limit quando
  presente (identifica o client de forma opaca, sem expor o valor em
  log/trace - so um hash SHA-256 truncado e usado como chave interna).
  Fallback para IP quando a rota e anonima (/health, /ready).
- Trusted proxy: o IP real do cliente em Kyma vem via X-Forwarded-For
  injetado pelo Istio/Envoy ingress. slowapi usa request.client.host
  como "remote address" - atras de um proxy correto isso e o IP do
  proxy, nao do cliente. _key_by_api_key_or_ip resolve isso:
  X-API-Key tem prioridade, entao o IP do proxy so importa para rotas
  sem autenticacao (onde o limite global e aceitavel).
"""

from __future__ import annotations

import hashlib

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _key_by_api_key_or_ip(request: Request) -> str:
    """Chave de rate limit: hash(X-API-Key) se presente, senao IP.

    O hash SHA-256 truncado (primeiros 16 chars hex) identifica o
    client de forma opaca — evita armazenar o valor literal da chave
    no estado interno do slowapi (memoria ou Redis) e em qualquer
    trace/log que o slowapi produza. Ainda e unico o suficiente para
    distinguir clientes (2^64 combinacoes).

    Rotas sem autenticacao (/health, /ready, /docs) caem no fallback
    de IP, que e o comportamento anterior — nesses casos o limite
    global por proxy-IP e aceitavel porque nao ha dado sensivel no
    request.
    """
    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if api_key:
        digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        return f"apikey:{digest}"
    return get_remote_address(request)


limiter = Limiter(key_func=_key_by_api_key_or_ip, default_limits=["10/minute"])
