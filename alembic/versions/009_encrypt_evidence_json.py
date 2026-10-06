"""Criptografa evidence_json em incidents (DA-60).

Revision ID: 009
Revises: 008
Create Date: 2026-10-06

Objetivo: Criptografar o campo evidence_json (JSONB) em todas as linhas da
tabela incidents. O campo evidence_json contém estruturas sensíveis (códigos
de erro, traces, paths de sistemas) e precisa de criptografia em repouso.

Decisiones:
- Fernet (DA-47): mesmo sistema usado para llm_credentials — consistência
  e reused de infra já validada.
- Key master: LLM_CREDENTIALS_MASTER_KEY (existe no .env.example:120).
  Se ausente, a migration falha com ConfigurationError.
- Encrypt all rows: não há dados sensíveis em evidence_json que possam
  ser lidos sem criptografia — todos os registros devem ser cifrados.
- Safe to run: a migration e' idempotente (re-encrypt com mesma key
  gera token diferente, mas decrypt funciona). Rodar varias vezes e' seguro.

Migrations anteriores:
- 001: cria incidents (evidence_json JSONB nullable)
- 002: fix evidence_strength type (float)
- 003: admin tables (models, runtime, credentials)
- 004: integration_systems
- 005: system_contracts (append-only, sem FK)
- 006: incident_prompt_provenance
- 007: web_users
- 008: web_search_sources
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.admin.crypto import _get_fernet

logger = logging.getLogger(__name__)

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def _encrypt_evidence(evidence: Any) -> str | None:
    """Cifra evidence_json e devolve token Fernet ou None."""
    if evidence is None:
        return None
    try:
        payload = json.dumps(evidence, ensure_ascii=False)
        return _get_fernet().encrypt(payload.encode()).decode()
    except Exception:  # noqa: BLE001
        logger.warning("[migration] Falha ao cifrar evidence_json, deixando em claro")
        return None


def upgrade() -> None:
    # 1. Verifica que a master key está configurada (levanta ConfigurationError se faltar)
    conn = op.get_bind()
    try:
        _get_fernet()
    except Exception as exc:
        raise RuntimeError(
            "LLM_CREDENTIALS_MASTER_KEY não configurada (DA-60). Defina no .env e reinicie alembic."
        ) from exc

    # 2. Atualiza linha por linha (não pode ser pure SQL pois usa criptografia)
    total = 0
    updated = 0

    # Get all incidents ids
    result = conn.execute(sa.text("SELECT id, evidence_json FROM incidents"))
    rows = result.fetchall()

    for row in rows:
        total += 1
        incident_id = row[0]
        evidence_json = row[1]

        # Skip if already encrypted or None
        if evidence_json is None:
            continue

        # Try to detect if already encrypted (Fernet tokens begin with "gAAA"):
        # Se ja for um token Fernet, pula (migration idempotente)
        if isinstance(evidence_json, str) and evidence_json.startswith("gAAA"):
            logger.debug("[migration] Incident %s ja cifrado, pulando", incident_id)
            continue

        # Encrypt
        encrypted = _encrypt_evidence(evidence_json)
        if encrypted is None:
            continue

        # Update row (JSONB field requires JSON type, not raw string)
        # Encrypted token é uma string, mas JSONB aceita strings como JSON válido
        # É preciso enviar como JSON string (doble quoted)
        json_value = json.dumps(encrypted, ensure_ascii=False)
        conn.execute(
            sa.text("UPDATE incidents SET evidence_json = :enc WHERE id = :id"),
            {"enc": json_value, "id": incident_id},
        )
        updated += 1

        if total % 1000 == 0:
            logger.info("[migration] Encrypt evidence_json: %d linhas processadas", total)

    logger.info(
        "[migration] Encrypt evidence_json: %d linhas processadas, %d cifradas",
        total,
        updated,
    )


def downgrade() -> None:
    """Desfaz criptografia de evidence_json (apenas para rollback imediato)."""
    conn = op.get_bind()

    try:
        fernet = _get_fernet()
    except Exception as exc:
        raise RuntimeError(
            "LLM_CREDENTIALS_MASTER_KEY não configurada (DA-60 downgrade). "
            "Impossível decifrar Evidence."
        ) from exc

    result = conn.execute(sa.text("SELECT id, evidence_json FROM incidents"))
    rows = result.fetchall()

    total = 0
    decompressed = 0

    for row in rows:
        incident_id = row[0]
        evidence_json = row[1]

        if evidence_json is None:
            continue

        # Detect se é ciphertext (começa com "gAAA")
        if not (isinstance(evidence_json, str) and evidence_json.startswith("gAAA")):
            continue

        try:
            payload = fernet.decrypt(evidence_json.encode()).decode()
            decrypted = json.loads(payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[migration] Falha ao decifrar evidence_json do incident %s",
                incident_id,
            )
            continue

        # JSONB field aceita dict, mas psycopg v3 não adapta dict para placeholder
        # Converter para string JSON (psycopg2 handle json.loads no lado cliente)
        json_value = json.dumps(decrypted, ensure_ascii=False)
        conn.execute(
            sa.text("UPDATE incidents SET evidence_json = :dec WHERE id = :id"),
            {"dec": json_value, "id": incident_id},
        )
        decompressed += 1
        total += 1

        if total % 1000 == 0:
            logger.info("[migration] Decrypt evidence_json: %d linhas processadas", total)

    logger.info(
        "[migration] Decrypt evidence_json: %d linhas, %d decifradas",
        total,
        decompressed,
    )
