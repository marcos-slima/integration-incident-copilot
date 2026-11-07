# SECURITY — Threat Model + Test Suite

## 1. Overview

This document describes the **threat model** and **security test suite** for the **Integration Incident Copilot (IIC)**.

---

## 2. Threat Model (STRIDE per element)

### 2.1. Data flow

```
[User Browser] → HTTPS → [FastAPI (port 8000)]
                              ↓
                  [PostgreSQL (port 5432)]
                              ↓
                   [Qdrant (port 6333)]
                              ↓
                      [Ollama (port 11434)]
```

### 2.2. STRIDE analysis

| Element | Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **User authentication** | **Spoofing**: fake API key | Medium | High | `X-API-Key` header required, validated against `web_users` table. |
| **LLM calls** | **Tampering**: modify prompt | Low | Critical | Hash digest of prompt stored in `incidents.prompt_digest`; changes → reject. |
| **Evidence storage** | **Repudiation**: deny evidence | Low | Medium | `evidence_json` encrypted with Fernet (`LLM_CREDENTIALS_MASTER_KEY`). |
| **Policy engine** | **Elevation of privilege**: bypass routing | Low | Critical | Fail-closed: unknown origin → no LLM access (`app/mcp/policy.py`). |
| **Web search** | **Information disclosure**: PII leak | Low | High | PII redaction before LLM call (DA-30); URLs truncated. |
| **Incident report** | **Repudiation**: alter diagnosis | Medium | High | `incidents.diagnosis_correct` flag (cannot coerce `true` from `false`; DA-13). |
| **Metering** | **Repudiation**: fake usage | Low | Medium | Real usage from `usage_metadata` (DA-48). |

---

## 3. Security test suite

### 3.1. Test categories

| Category | Tests | File |
|---|---|---|
| **Authentication** | `test_diagnose_requires_api_key_when_configured` | `tests/test_api.py` |
| **Rate limiting** | `test_login_rate_limit_com_x_api_key_nao_e_contornado` | `tests/test_auth.py` |
| **PII redaction** | `test_redact_pii_text_email`, `test_redact_pii_text_cpf_formatted` | `tests/test_redaction.py` |
| **Prompt tampering** | `test_passa_com_digest_igual` | `tests/test_prompt_versioning.py` |
| **Policy bypass** | `test_enforce_denies_unregistered_tool_fail_closed` | `tests/test_mcp_policy.py` |
| **Fernet encryption** | `test_fernet_roundtrip` | `tests/test_security.py` |

### 3.2. Test execution

```bash
# Run security tests
uv run pytest tests/ -m "security" -v

# Run all unit tests + security
uv run pytest tests/ -m "not integration" -v --cov=app --cov-fail-under=80
```

### 3.3. Security gates

| Gate | Pass condition | Block on failure? |
|---|---|---|
| `test_auth_missing_key` | Returns `401 Unauthorized` | Yes |
| `test_auth_invalid_key` | Returns `403 Forbidden` | Yes |
| `test_rate_limit_exceeded` | Returns `429 Too Many Requests` | Yes |
| `test_pii_redaction` | No PII present in prompt/diagnosis | Yes |
| `test_policy_fail_closed` | Unknown origin → no LLM access | Yes |
| `test_fernet_roundtrip` | Decrypt same as encrypt | Yes |

---

## 4. Compliance checks

### 4.1. Checklist

- [ ] All secrets encrypted at rest (`app/admin/crypto.py`).
- [ ] PII redaction before any LLM call (`nodes.py::_redact_pii`).
- [ ] API key validation on `/diagnose`, `/mcp`, `/a2a` (`main.py:Depends(APIKeyHeader)`).
- [ ] Rate limiting on `/incidents/{id}/review` (`main.py:rate_limiter(10, 60)`).
- [ ] Circuit breaker enabled (`app/llm/gateway.py:GatewayConfig`).
- [ ] Audit log enabled (`app/metrics.py`).
- [ ] HTTPS enforced in production (via ingress / load balancer).

### 4.2. Automated scans

| Tool | Command | Frequency |
|---|---|---|
| **gitleaks** | `gitleaks detect --source . --verbose` | Pre-commit |
| **bandit** | `uv run bandit -r app/ -c pyproject.toml` | CI |
| **pylint** | `uv run pylint app/` | CI |
| **mypy** | `uv run mypy app/` | CI |

---

## 5. Incident response

### 5.1. Steps

1. **Detect** → alert via `#iic-alerts` Slack channel.
2. **Isolate** → disable compromised origin / API key.
3. **Eviscerate** → rotate all secrets (`LLM_CREDENTIALS_MASTER_KEY`, database password).
4. **Remediate** → patch code + deploy hotfix.
5. **Postmortem** → criar relatório em formato livre (exigido para P1/P2).

### 5.2. Escalation

| Level | Severity | Response SLA | Escalate to |
|---|---|---|---|
| **P1** | Data breach, auth bypass | 15 minutes | Tech Lead + Security team |
| **P2** | LLM credential leak | 1 hour | Tech Lead |
| **P3** | Minor vulnerability (no PII exposure) | 24 hours | Dev team |

---

## 6. Audit trail

### 6.1. Logs

| Field | Description | Retention |
|---|---|---|
| `timestamp` | ISO 8601 UTC | 7 years |
| `origin` | API key origin (SHA-256) | 7 years |
| `endpoint` | `/diagnose`, `/mcp`, `/review`, etc. | 7 years |
| `ip_address` | Client IP (optional) | 7 years |
| `request_id` | Trace correlation | 7 years |
| `response_status` | HTTP status code | 7 years |
| `tokens_used` | LLM tokens (DA-48) | 7 years |
| `cost_usd` | USD cost | 7 years |

### 6.2. Access control

| Role | Read | Write | Audit log |
|---|---|---|---|
| **analyst** | Own incidents | Create/update own incidents | ❌ |
| **admin** | All incidents | All operations | ✅ |
| **tech_lead** | All incidents + metrics | All operations + config | ✅ |

---

## 7. Security test cases

### 7.1. Authentication

```python
def test_auth_missing_key_returns_401(client):
    response = client.post("/diagnose", json={"description": "test"})
    assert response.status_code == 401


def test_auth_invalid_key_returns_403(client):
    headers = {"X-API-Key": "invalid_key"}
    response = client.post("/diagnose", headers=headers, json={"description": "test"})
    assert response.status_code == 403
```

### 7.2. Rate limiting

```python
def test_rate_limit_exceeded_returns_429(client):
    headers = {"X-API-Key": "valid_key"}
    for _ in range(11):
        response = client.post("/incidents/123/review", headers=headers, json={"action": "approve"})
    assert response.status_code == 429
```

### 7.3. PII redaction

```python
def test_pii_redaction_credit_card(client):
    headers = {"X-API-Key": "valid_key"}
    payload = {"description": "Cartão 4111111111111111 bloqueado", "locale": "pt-BR"}
    response = client.post("/diagnose", headers=headers, json=payload)
    r = response.json()
    assert "4111111111111111" not in r["diagnosis"]["prompt"]
    assert "[REDACTED_CREDIT_CARD]" in r["diagnosis"]["prompt"]
```

### 7.4. Prompt digest (anti-tampering)

```python
def test_prompt_digest_mismatch(client):
    # First request
    headers = {"X-API-Key": "valid_key"}
    payload = {"description": "test", "locale": "pt-BR"}
    response = client.post("/diagnose", headers=headers, json=payload)
    prompt_digest_1 = response.json()["diagnosis"]["prompt_digest"]

    # Second request (modified prompt)
    payload = {"description": "test modificado", "locale": "pt-BR"}
    response = client.post("/diagnose", headers=headers, json=payload)
    prompt_digest_2 = response.json()["diagnosis"]["prompt_digest"]

    # Digests should differ
    assert prompt_digest_1 != prompt_digest_2
```

### 7.5. Policy fail-closed

```python
def test_policy_fail_closed_unknown_origin():
    # Unknown API key origin
    headers = {"X-API-Key": "unknown_key_12345"}
    payload = {"description": "test", "origin": "unknown"}
    response = client.post("/diagnose", headers=headers, json=payload)
    # Should return error before any LLM call
    assert "origin" in response.json()["detail"].lower()
```

### 7.6. Fernet encryption

```python
def test_fernet_roundtrip():
    from app.admin.fernet import encrypt, decrypt
    from app.config import settings

    master_key = settings.llm_credentials_master_key
    secret = "sensitive_data_12345"

    encrypted = encrypt(secret, master_key)
    decrypted = decrypt(encrypted, master_key)

    assert decrypted == secret
```

---

## 8. Checklist de segurança

Antes de cada release:

- [ ] Gitleaks scan clean.
- [ ] Bandit scan clean.
- [ ] All security tests passing.
- [ ] No secrets in logs or environment variables.
- [ ] HTTPS enforced (via ingress).
- [ ] Rate limits configured.
- [ ] Circuit breaker enabled.
- [ ] Audit logging enabled.
- [ ] PII redaction tested.

---

## 9. Referências

- **OWASP Top 10**: https://owasp.org/www-project-top-ten/
- **STRIDE**: https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats
- **DA-30 (PII redaction)**: `./DECISOES_DE_ARQUITETURA.md`
- **DA-39 (Soberania de dados)**: `./DECISOES_DE_ARQUITETURA.md`
- **DA-41 (Circuit breaker)**: `./DECISOES_DE_ARQUITETURA.md`
- **DA-48 (Metering)**: `./DECISOES_DE_ARQUITETURA.md`

---

**Últmina revisão:** 2026-10-19
**Próxima revisão:** 2026-11-19
**Responsável:** Tech Lead

## Controles efetivos por superfície

A SPA usa `POST /auth/login`, `POST /auth/logout` e `GET /auth/session`.
A sessão usa assinatura HMAC, validade e cookie HttpOnly. O atributo
Secure depende da configuração. Logout revoga a sessão e remove o cookie.
A rota de diagnóstico aceita sessão ou chave conforme
`app/main.py::verify_session_or_api_key`. O middleware MCP reutiliza `X-API-Key` de REST. A2A, eventos e
administração verificam, respectivamente, `X-A2A-Api-Key`,
`X-Event-Mesh-Api-Key` e `X-API-Admin-Key`. A autorização de capabilities no MCP é adicional à
autenticação de transporte. Nas operações administrativas, o controle
observado é a chave administrativa compartilhada. O login da SPA não
concede acesso a essas operações por papel.

Em `app/admin/ui.py`, as páginas Jinja são shells. O JavaScript envia
`X-API-Admin-Key` nas chamadas protegidas e mantém a chave no
`sessionStorage`. A CSP de `app/main.py` se aplica às respostas HTML
com exceções explícitas para a documentação interativa. Limites de
tentativas de autenticação e de requisições de aplicação são controles
distintos. Confira o backend de rate limiting ao distribuir réplicas.

`app/admin/crypto.py` usa Fernet para credenciais e evidência estruturada.
As rotas admin de credenciais exibem metadados, e os consumidores
autorizados recuperam a credencial para uso. O caminho de evidência
também admite leitura de JSON legado. A disponibilidade e retenção do
histórico dependem dos dados, da chave e da política operacional do
ambiente. Logs, traces e caches têm fronteiras próprias. A presença de
redação em um destino não comprova ausência de PII nos demais.

`app/redaction.py` implementa padrões e detecção contextual de CPF.
`tests/test_dlp_ner.py` checa a presença de funções e campos, com alcance
insuficiente para demonstrar NER ou cobertura de dados sensíveis. A DA-79
tem campos e filtros em repositórios para incidentes e credenciais, com
consultas globais também disponíveis. A inspeção não confirmou uma
cadeia de identidade autenticada, autorização por locatário e filtro
obrigatório em todos os protocolos, filas e armazenamentos.

A ativação usa `POST /auth/verify/email` e `POST /auth/verify/phone`.
`app/notifications/providers.py` seleciona Mailpit ou Resend para e-mail.
Para telefone a factory atual não devolve um provedor. O código é
gerenciado pela administração e entregue fora da aplicação. A geração
do código e a validação de sua expiração são capacidades distintas
da entrega SMS.

Relatórios em `/admin/reports/compliance/export`, painel de compliance e
materiais de ISO 27001/SOC 2 são insumos de governança. Certificação e
eficácia dos controles exigem evidência organizacional independente.

A busca web passa por `app/agent/nodes.py::_web_search_allowed`.
A execução exige habilitação, política compatível e fonte aprovada por
tipo em `web_search_sources`. O modo `public_only` consulta a
classificação de sensibilidade. A consulta omite a descrição original e
usa o termo cadastrado, o filtro de site e, quando disponível, a
mensagem do conector após redação. Resultados são truncados, sanitizados
e mantidos como `web_untrusted`; URLs têm query e fragmento removidos.
Esses controles reduzem exposição e interferência de conteúdo externo.
A cobertura depende dos padrões reconhecidos e dos campos envolvidos.
