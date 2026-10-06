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
- Key master: LLM_CREDENTIALS_MASTER_KEY. So e exigida quando ha linhas
  com evidencia em claro; banco novo (CI) migra sem a chave.
- A PII reconhecivel e redigida antes de cifrar (mesma regra do runtime,
  app/admin/crypto.py::encrypt_evidence).
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

import sqlalchemy as sa

from alembic import op
from app.admin.crypto import _get_fernet

logger = logging.getLogger(__name__)

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, evidence_json FROM incidents")).fetchall()
    pendentes = [
        (row[0], row[1])
        for row in rows
        if row[1] is not None and not (isinstance(row[1], str) and row[1].startswith("gAAA"))
    ]
    if not pendentes:
        # Banco novo (CI) ou ja migrado: nada a cifrar, a chave nao e exigida.
        logger.info("[migration 009] nenhuma evidencia em claro - nada a fazer")
        return

    from app.admin.crypto import encrypt_evidence

    try:
        _get_fernet()
    except Exception as exc:
        raise RuntimeError(
            f"{len(pendentes)} incidente(s) com evidence_json em claro e "
            "LLM_CREDENTIALS_MASTER_KEY ausente/invalida (DA-60). Defina a chave no "
            ".env e rode o alembic de novo."
        ) from exc

    for incident_id, evidence_json in pendentes:
        # encrypt_evidence redige PII antes de cifrar (mesma regra do runtime).
        token = encrypt_evidence(evidence_json)
        conn.execute(
            sa.text("UPDATE incidents SET evidence_json = :enc WHERE id = :id"),
            # JSONB aceita string JSON: o token vira um valor string no documento.
            {"enc": json.dumps(token, ensure_ascii=False), "id": incident_id},
        )
    logger.info("[migration 009] %d evidencia(s) cifradas", len(pendentes))


def downgrade() -> None:
    """Desfaz criptografia de evidence_json (apenas para rollback imediato)."""
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, evidence_json FROM incidents")).fetchall()
    if not any(isinstance(r[1], str) and r[1].startswith("gAAA") for r in rows):
        return  # nada cifrado: a chave nao e exigida

    try:
        fernet = _get_fernet()
    except Exception as exc:
        raise RuntimeError(
            "LLM_CREDENTIALS_MASTER_KEY não configurada (DA-60 downgrade). "
            "Impossível decifrar Evidence."
        ) from exc

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
