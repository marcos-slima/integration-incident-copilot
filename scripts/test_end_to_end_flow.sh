#!/bin/bash
set -euo pipefail

# Script de teste end-to-end: criar usuário → email → phone → login → diagnose
# Requer backend rodando em http://127.0.0.1:8000 e Mailpit em 1025

BACKEND="http://127.0.0.1:8000"
API_KEY="${IIC_API_KEY:-test-api-key}"
ADMIN_KEY="${IIC_ADMIN_KEY:-test-admin-key}"

echo "=== Teste End-to-End: Fluxo completo de ativação ==="

# 1. Criar usuário via API admin
echo "- Criando usuário 'e2e_test'..."
USER_RESP=$(curl -s -X POST "${BACKEND}/admin/api/users" \
  -H "X-API-Admin-Key: ${ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "e2e_test@example.com",
    "password": "SenhaSegura123!",
    "name": "Teste End-to-End"
  }')
USER_ID=$(echo "$USER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  ID: ${USER_ID}"

# 2. Confirmar e-mail (extraído do Mailpit)
echo "- Aguardando e-mail de ativação..."
EMAIL_ID=$(curl -s "http://127.0.0.1:8025/api/v1/messages" | python3 -c "import sys,json; msgs=json.load(sys.stdin); print([m['ID'] for m in msgs if 'e2e_test' in m.get('Content.Body', '')][-1])")
if [ -z "$EMAIL_ID" ]; then
  echo "ERRO: E-mail não encontrado no Mailpit"
  exit 1
fi
echo "  E-mail ID: ${EMAIL_ID}"

TOKEN=$(curl -s "http://127.0.0.1:8025/api/v1/messages/${EMAIL_ID}/body" | python3 -c "import sys,json; body=json.load(sys.stdin); import re; m=re.search(r'/auth/verify/email\?token=([a-z0-9:]+)', body); print(m.group(1)) if m else print('')")
if [ -z "$TOKEN" ]; then
  echo "ERRO: Token não encontrado no e-mail"
  exit 1
fi
echo "  Token extraído: ${TOKEN:0:20}..."

curl -s -X POST "${BACKEND}/auth/verify/email" \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"${TOKEN}\"}" > /dev/null
echo "- E-mail confirmado"

# 3. Reemitir código SMS via admin API
echo "- Reemitindo código SMS..."
CODE_RESP=$(curl -s -X POST "${BACKEND}/admin/api/users/${USER_ID}/phone-code" \
  -H "X-API-Admin-Key: ${ADMIN_KEY}")
CODE=$(echo "$CODE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['sms_code'])")
echo "  Código SMS (out-of-band): ${CODE}"

# 4. Confirmar código SMS
echo "- Confirmando código SMS..."
curl -s -X POST "${BACKEND}/auth/verify/phone" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": ${USER_ID}, \"code\": \"${CODE}\"}" > /dev/null
echo "- Código SMS confirmado"

# 5. Login
echo "- Realizando login..."
LOGIN_RESP=$(curl -s -X POST "${BACKEND}/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"e2e_test@example.com\", \"password\": \"SenhaSegura123!\"}")
SESSION_ID=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "  Session ID: ${SESSION_ID:0:40}..."

# 6. Diagnóstico com mock OData
echo="- Executando diagnóstico..."
DIAG_RESP=$(curl -s -X POST "${BACKEND}/diagnose" \
  -H "Cookie: iic_session=${SESSION_ID}" \
  -H "Content-Type: application/json" \
  -d '{
    "connector_source_system": "odata",
    "connector_type": "odata",
    "identifier": "Product",
    "description": "Erro ao consultar entidade Product via OData: HTTP 500"
  }')

PROBLEM=$(echo "$DIAG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('probable_root_cause', 'N/A'))")
CONFIDENCE=$(echo "$DIAG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d.get('diagnosis_confidence', 0)*100:.0f}%\")")
echo "- Diagnóstico concluído:"
echo "  Causa: ${PROBLEM}"
echo "  Confiança: ${CONFIDENCE}"

echo "=== End-to-End: Sucesso! ==="
