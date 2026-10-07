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

`request_client_identity` usa, nesta ordem: `X-A2A-Api-Key` (`a2a:<hash>`),
`X-API-Key` (`apikey:<hash>`) e, por último, o IP. O header só define o bucket
quando a chave é **válida** (comparada com `A2A_API_KEY`/`API_KEY`); chave
inválida ou ausente cai no bucket do IP. `<hash>` são os 16 primeiros
caracteres hex do SHA-256 da chave — a chave crua nunca vira chave do storage.

## Armazenamento

O `Limiter` é criado sem `storage_uri`, então os contadores ficam **em memória,
por processo**. Com várias réplicas (HPA no Kyma), cada pod conta separadamente.
Também não há Redis aqui, mesmo com `REDIS_URL` configurada.

## Limitações conhecidas (abertas)

- **Contadores por processo.** Ver "Armazenamento": com várias réplicas, o
  limite efetivo é N × o configurado.
- **Atrás de proxy, depende de configuração.** O `CMD` do `Dockerfile` já roda
  o uvicorn com `--proxy-headers` (DEP-01), mas ele só confia no
  `X-Forwarded-For` vindo dos IPs em `FORWARDED_ALLOW_IPS` (default do uvicorn:
  `127.0.0.1,::1`). Atrás do Istio, defina as faixas internas do cluster
  (`deploy/kyma/configmap.yaml`); sem isso, todos os clientes compartilham o
  IP do sidecar e o mesmo bucket. Nunca use `*`: o cliente passaria a escolher
  o próprio "IP".

## Resolvido (SEC-03, validação 2026-10-07)

- **Força bruta de chave de API.** As dependencies de chave recusam antes do
  decorator contar, então `app/auth_guard.py` conta cada falha por IP e por
  superfície (`api`, `event_mesh`, `admin`, `a2a`) numa janela fixa de 60 s;
  acima de `AUTH_FAILURES_PER_MINUTE` (default 10) a resposta vira 429. Com
  `REDIS_URL` o contador é compartilhado entre réplicas; sem Redis, fica em
  memória do processo.
- **Enumeração de usuário por tempo.** Usuário inexistente agora roda
  `burn_password_check` (`app/auth.py`): o mesmo PBKDF2 contra um sal
  descartável, com custo igual ao de um usuário existente com senha errada.
- **Código de telefone sem limite.** Cada código vigente aceita no máximo
  `PHONE_CODE_MAX_ATTEMPTS` erros (default 5, `app/webusers.py`); depois é
  invalidado e só o admin reemite, com a mesma resposta genérica.

## Teste manual

```bash
# 6a tentativa de login no mesmo minuto, mesmo IP -> 429
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/auth/login \
    -H "Content-Type: application/json" -d '{"username":"x","password":"y"}'
done
```

Teste automatizado: `tests/test_auth.py` (bucket do login independe de `X-API-Key`).
