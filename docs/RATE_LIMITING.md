# Rate limiting

Implementação: [SlowAPI](https://slowapi.readthedocs.io/) em `app/rate_limit.py`,
aplicada pelo `SlowAPIMiddleware` (`app/main.py`) e por decorators
`@limiter.limit(...)` nas rotas.

> Este documento substitui a versão de 2026-10-06, que descrevia "1000 RPS",
> "backend Redis" e "username+IP" — nada disso existe no código. Ele também
> se rotulava "DA-03", número que já pertence à DA-3 (guardrails em código).
> Rate limiting não tem DA própria.

## Limites em vigor

| Rota | Limite | Chave do bucket |
|---|---|---|
| `POST /auth/login`, `POST /auth/verify/email`, `POST /auth/verify/phone` | 5/minuto | `login:<IP>`, `verify_email:<IP>`, `verify_phone:<IP>` (`request_client_identity_for_auth_endpoints`) |
| `POST /diagnose`, `POST /diagnose/async`, `POST /events/incident`, `POST /incidents/{id}/verify` | 10/minuto | `request_client_identity` (ver abaixo) |
| `POST /a2a` | 10/minuto | `request_client_identity` |
| Demais rotas | 60/minuto (`default_limits`) | `request_client_identity` |

`request_client_identity` usa, nesta ordem: o valor do header `X-A2A-Api-Key`
(`a2a:<valor>`), o valor de `X-API-Key` (`apikey:<valor>`) e, por último, o IP.

## Armazenamento

O `Limiter` é criado sem `storage_uri`, então os contadores ficam **em memória,
por processo**. Com várias réplicas (HPA no Kyma), cada pod conta separadamente.
Também não há Redis aqui, mesmo com `REDIS_URL` configurada.

## Limitações conhecidas (abertas)

- **Força bruta de chave de API.** O bucket das rotas protegidas é o próprio
  header enviado pelo cliente, e a autenticação recusa a chave inválida
  **antes** do decorator contar a tentativa. Resultado: tentativas com
  `X-API-Key` inválida não recebem 429 (70 tentativas seguidas em `/diagnose`
  sem nenhum 429, na validação de 2026-10-06).
- **Enumeração de usuário por tempo.** O login de um usuário inexistente
  responde em ~0,05 ms; o de um existente com senha errada, em ~160 ms
  (PBKDF2 só roda quando o usuário existe). O limite por IP não resolve isso.
- **Atrás de proxy.** Sem `--proxy-headers`/`forwarded-allow-ips` no uvicorn,
  atrás do Istio todos os clientes compartilham o IP do sidecar e, portanto,
  o mesmo bucket de login.
- **Sem limite por usuário ou por código** em `/auth/verify/phone` (código de
  6 dígitos).

## Teste manual

```bash
# 6a tentativa de login no mesmo minuto, mesmo IP -> 429
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/auth/login \
    -H "Content-Type: application/json" -d '{"username":"x","password":"y"}'
done
```

Teste automatizado: `tests/test_auth.py` (bucket do login independe de `X-API-Key`).
