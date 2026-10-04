#!/usr/bin/env python3
"""
Script de teste end-to-end: criar usuário → email → phone → login → diagnose.

Requisitos:
- Backend rodando em http://127.0.0.1:8000
- Mailpit rodando em http://127.0.0.1:8025 (API) e 1025 (SMTP)
- ADMIN_API_KEY configurada no .env (chave estática ou capturada via docker logs)

Uso:
    uv run python scripts/test_e2e_flow.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.exceptions import RequestException

# Carregar variáveis do .env (se existir)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from dataclasses import dataclass
from typing import Any

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://127.0.0.1:8025")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", None)
API_KEY = os.environ.get("API_KEY", None)
SESSION = requests.Session()

if not ADMIN_API_KEY:
    print("ERRO: ADMIN_API_KEY não configurada. Configure no .env ou via variável de ambiente.")
    sys.exit(1)
if not API_KEY:
    print("ERRO: API_KEY não configurada. Configure no .env ou via variável de ambiente.")
    sys.exit(1)


@dataclass
class TestResult:
    name: str
    passed: bool
    details: str = ""


def log_pass(msg: str) -> None:
    print(f"✅ {msg}")


def log_fail(msg: str) -> None:
    print(f"❌ {msg}")


def http_post(
    url: str, json_data: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> requests.Response:
    try:
        response = SESSION.post(url, json=json_data, headers=headers, timeout=10)
        response.raise_for_status()
        return response
    except RequestException as exc:
        raise RuntimeError(f"HTTP POST falhou em {url}: {exc}") from exc


def http_get(url: str) -> requests.Response:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response
    except RequestException as exc:
        raise RuntimeError(f"HTTP GET falhou em {url}: {exc}") from exc


def main() -> int:
    print("=" * 80)
    print("TESTE END-TO-END: Fluxo completo de ativação e diagnóstico")
    print("=" * 80)
    print()

    results: list[TestResult] = []

    # 1. Criar usuário via admin API
    print("[1/7] Criando usuário via admin API...")
    username = f"e2e_{uuid.uuid4().hex[:8]}"
    email = f"{username}@example.com"
    phone = "+5511999999999"
    password = "<redact>"

    try:
        resp = http_post(
            f"{BACKEND_URL}/admin/api/users",
            json_data={
                "username": username,
                "email": email,
                "phone": phone,
                "password": password,
            },
            headers={"X-API-Admin-Key": ADMIN_API_KEY},
        )
        user_data = resp.json()
        user_id = user_data["id"]
        log_pass(f"Usuário criado: {username} (id={user_id})")
        results.append(TestResult("create_user", True, f"status={user_data['status']}"))
    except Exception as exc:  # noqa: BLE001
        log_fail(f"Falha ao criar usuário: {exc}")
        results.append(TestResult("create_user", False, str(exc)))
        return 1

    # Aguardar e-mail chegar ao Mailpit
    print("[2/7] Aguardando e-mail de ativação no Mailpit...")
    max_wait = 30
    elapsed = 0
    email_msg = None
    while elapsed < max_wait:
        try:
            resp = http_get(f"{MAILPIT_URL}/api/v1/messages")
            messages = resp.json().get("messages", [])
            email_msg = next((m for m in messages if email in str(m.get("To", []))), None)
            if email_msg:
                break
        except Exception as exc:  # noqa: BLE001
            print(f"  Aguardando Mailpit (erro: {exc})...")
        time.sleep(1)
        elapsed += 1

    if not email_msg:
        log_fail("E-mail não encontrado no Mailpit após 30s")
        results.append(TestResult("email_delivered", False, "Timeout aguardando e-mail"))
        return 1

    log_pass(f"E-mail recebido (ID: {email_msg['ID']})")
    results.append(TestResult("email_delivered", True, f"email_id={email_msg['ID']}"))

    # Extrair token do e-mail
    print("[3/7] Extracting activation token from e-mail...")
    snippet = email_msg.get("Snippet", "")
    token = None
    if "verify-email:" in snippet:
        start = snippet.index("verify-email:")
        token = snippet[start : start + 128]  # token base64 urlsafe ~100+ chars
        log_pass(f"Token extraído (prefixo: {token[:30]}...)")
        results.append(TestResult("token_extracted", True, f"token_prefix={token[:30]}"))
    else:
        log_fail("Token não encontrado no snippet do e-mail")
        results.append(TestResult("token_extracted", False, "Token não encontrado"))
        return 1

    # Confirmar e-mail
    print("[4/7] Confirming e-mail via /auth/verify/email...")
    try:
        resp = http_post(
            f"{BACKEND_URL}/auth/verify/email",
            json_data={"username": username, "token": token},
        )
        email_result = resp.json()
        assert email_result.get("ok") is True, "E-mail não confirmado"
        assert email_result.get("next_step") == "phone", (
            f"next_step esperado 'phone', got '{email_result.get('next_step')}'"
        )
        log_pass("E-mail confirmado, próximo passo: phone")
        results.append(TestResult("confirm_email", True, f"next_step={email_result['next_step']}"))
    except Exception as exc:  # noqa: BLE001
        log_fail(f"Falha ao confirmar e-mail: {exc}")
        results.append(TestResult("confirm_email", False, str(exc)))
        return 1

    # Reemitir código SMS via admin API
    print("[5/7] Reemitindo código SMS via admin API...")
    try:
        resp = http_post(
            f"{BACKEND_URL}/admin/api/users/{user_id}/phone-code",
            headers={"X-API-Admin-Key": ADMIN_API_KEY},
        )
        sms_data = resp.json()
        # Resposta do fallback out-of-band: activation.phone_code
        sms_code = sms_data.get("activation", {}).get("phone_code")
        assert sms_code is not None, "Código SMS não retornado"
        assert sms_code.isdigit() and len(sms_code) == 6, f"Código SMS inválido: {sms_code}"
        log_pass(f"Código SMS retornado (out-of-band): {sms_code}")
        results.append(TestResult("sms_code_emitted", True, f"sms_code={sms_code}"))
    except Exception as exc:  # noqa: BLE001
        log_fail(f"Falha ao reemitir código SMS: {exc}")
        results.append(TestResult("sms_code_emitted", False, str(exc)))
        return 1

    # Confirmar código SMS
    print("[6/7] Confirming SMS code via /auth/verify/phone...")
    try:
        resp = http_post(
            f"{BACKEND_URL}/auth/verify/phone",
            json_data={"username": username, "code": sms_code},
        )
        phone_result = resp.json()
        assert phone_result.get("ok") is True, "Código SMS não confirmado"
        assert phone_result.get("status") == "active", (
            f"status esperado 'active', got '{phone_result.get('status')}'"
        )
        log_pass("Código SMS confirmado, usuário ativo")
        results.append(TestResult("confirm_sms", True, f"status={phone_result['status']}"))
    except Exception as exc:  # noqa: BLE001
        log_fail(f"Falha ao confirmar código SMS: {exc}")
        results.append(TestResult("confirm_sms", False, str(exc)))
        return 1

    # Login
    print("[7/7] Executando login...")
    try:
        resp = http_post(
            f"{BACKEND_URL}/auth/login",
            json_data={"username": username, "password": password},
        )
        login_data = resp.json()
        assert login_data.get("authenticated") is True, "Login não autenticado"
        log_pass(f"Login bem-sucedido (username: {username})")
        results.append(TestResult("login", True, f"authenticated=true, username={username}"))

        # Agora, fazer diagnóstico com a sessão obtida
        print()
        print("-" * 80)
        print("EXECUTANDO DIAGNÓSTICO COM CONECTOR MOCK ODATA")
        print("-" * 80)

        # Cookie da sessão é definido via set-cookie no response do login
        # Para reutilizar, extraí-lo manualmente ou autenticar via X-API-Key para diagnose
        # O cookie é do formato: iic_session=VALUE; HttpOnly; Path=/
        set_cookie = resp.headers.get("set-cookie", "")
        session_cookie = None
        if set_cookie:
            for part in set_cookie.split(";"):
                if part.strip().startswith("iic_session="):
                    session_cookie = part.strip()
                    break

        headers_diag = {}
        if session_cookie:
            headers_diag["Cookie"] = session_cookie

        # Tenta com cookie primeiro, se não tiver, usa API_KEY
        if not session_cookie:
            headers_diag["X-API-Key"] = API_KEY

        resp_diag = http_post(
            f"{BACKEND_URL}/diagnose",
            json_data={
                "connector_source_system": "odata",
                "connector_type": "odata",
                "identifier": "Product",
                "description": "Erro ao consultar entidade Product via OData: HTTP 500",
            },
            headers=headers_diag if headers_diag else None,
        )
        diag_data = resp_diag.json()

        print()
        print("DIAGNÓSTICO RECEBIDO:")
        print(f"  Probable root cause: {diag_data.get('probable_root_cause', 'N/A')[:100]}...")
        print(f"  Diagnosis confidence: {diag_data.get('diagnosis_confidence', 0):.0%}")
        print(f"  Model confidence: {diag_data.get('model_confidence', 0):.0%}")
        print(f"  Evidence strength: {diag_data.get('evidence_strength', 0):.0%}")
        print(f"  Matched source: {diag_data.get('matched_source', 'N/A')}")
        print(f"  LLM provider: {diag_data.get('llm_provider_used', 'N/A')}")
        print(f"  Agent domain: {diag_data.get('agent_domain', 'N/A')}")
        print(f"  LLM model: {diag_data.get('llm_model', 'N/A')}")

        results.append(
            TestResult(
                "diagnosis",
                True,
                f"confidence={diag_data.get('diagnosis_confidence', 0):.0%}, source={diag_data.get('matched_source', 'N/A')}",
            )
        )

    except Exception as exc:  # noqa: BLE001
        log_fail(f"Falha no login ou diagnóstico: {exc}")
        results.append(TestResult("login_or_diagnosis", False, str(exc)))
        return 1

    # Summary
    print()
    print("=" * 80)
    print("RESUMO DOS RESULTADOS")
    print("=" * 80)

    all_passed = True
    for result in results:
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"{status}: {result.name}")
        if result.details:
            print(f"       {result.details}")
        if not result.passed:
            all_passed = False

    print()
    if all_passed:
        print("🎉 Todos os testes passaram!")
        return 0
    else:
        print("❌ Alguns testes falharam")
        return 1


if __name__ == "__main__":
    sys.exit(main())
