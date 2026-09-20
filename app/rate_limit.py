"""Instancia compartilhada do slowapi Limiter.

Extraido de app/main.py (avaliacao externa - medio prazo, item 1:
"Rate limit global e por endpoint (incluir /a2a)") para um modulo
proprio, sem dependencias do resto do app - app/main.py monta a rota
raiz e app/a2a/server.py monta a rota /a2a, e ambas precisam decorar
endpoints com @limiter.limit(...). Colocar o Limiter em app/main.py
(como estava antes) criaria um import circular: app/a2a/server.py
precisaria importar de app/main.py, que por sua vez importa o router
de app/a2a/server.py.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# Limite default por IP: 10 requests/minuto. Antes desta mudanca, esse
# "default_limits" so valia para rotas com "@limiter.limit(...)"
# explicito (/diagnose, /events/incident, /incidents/{id}/verify) -
# SlowAPIMiddleware nunca era registrado no app FastAPI, entao rotas
# sem decorator (ex.: /a2a, /mcp, /health) ficavam SEM NENHUM limite,
# apesar do "default_limits" sugerir o contrario. app/main.py agora
# registra SlowAPIMiddleware, fazendo esse default valer para toda
# rota sem limite proprio - e /a2a ganha, alem disso, seu proprio
# "@limiter.limit(...)" explicito (ver app/a2a/server.py), pelo mesmo
# motivo de /diagnose ja ter um: documentar a intencao no proprio
# endpoint, independente do que o default global for no futuro.
limiter = Limiter(key_func=get_remote_address, default_limits=["10/minute"])
