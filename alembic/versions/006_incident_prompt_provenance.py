"""DA-53: migration 006 - proveniencia de prompt e modelo em `incidents`

Revision ID: 006
Revises: 005
Create Date: 2026-09-28

Ate aqui, `incidents` guardava `llm_provider_used` (QUAL transporte) e
`agent_domain` (QUAL persona), mas nao o NOME do modelo nem a versao do
prompt. Ou seja: o resultado de um diagnostico era irreproduzivel. Quando
o modelo canonico mudou (DA-4/8 e DA-12 trocaram `qwen2.5-coder:32b` por
`qwen3-coder-next:latest`), nao havia como responder "quais incidentes
antigos sairam do modelo antigo?".

Tres colunas, todas anulaveis:

- `llm_model` era o dado que ja circulava em `CopilotState.llm_model` e
  nunca era persistido. `String(128)`: nome de modelo em registry local
  (`qwen3-coder-next:latest`) e em cloud (deployment com sufixo longo)
  nao cabem em 64.
- `prompt_version` + `prompt_digest` (DA-53). O digest e' sha256 do
  artefato de prompt (app/agent/prompts.py) e responde "qual template
  gerou isso"; a versao legivel responde "qual revisao declarada".

**Por que anulaveis e nao NOT NULL com default.** Um diagnostico
encerrado pelo rule engine (DA-33) nao passou por prompt nenhum, e um
incidente gravado antes desta migration nao tem o dado. Default
`'desconhecido'` fabricaria uma procedencia que nao existe -- o mesmo
erro da invariante 13 (coagir `diagnosis_correct` para True infla a
acuracia). NULL significa "nao sei", que e a resposta correta.

Sem indice dedicado: a consulta de interesse ("modelo X nos ultimos 30
dias") ja tem indice composto por `created_at` em provider/domain, e
`llm_model` nao e selectivo o bastante para justificar outro indice numa
tabela que ja tem seis.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("llm_model", sa.String(length=128), nullable=True))
    op.add_column("incidents", sa.Column("prompt_version", sa.String(length=32), nullable=True))
    op.add_column("incidents", sa.Column("prompt_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "prompt_digest")
    op.drop_column("incidents", "prompt_version")
    op.drop_column("incidents", "llm_model")
