"""DA-53: o prompt de diagnostico como artefato versionado.

O texto do prompt vivia espalhado em f-strings dentro de
`app/agent/nodes.py`: a persona, o template e a instrucao JSON. Isso
tinha tres consequencias, todas silenciosas:

1. **Nao dava para atribuir um resultado ao prompt que o produziu.** A
   tabela `incidents` guarda `llm_provider_used` e `agent_domain`, mas
   nao o modelo nem o prompt. Quando o modelo canônico mudou (DA-4/8,
   DA-12) ninguem consegue dizer quais diagnosticos antigos foram
   produzidos pelo modelo antigo.
2. **Nao dava para saber se o prompt em producao era o prompt medido.**
   O promptfoo chama `run_diagnosis` de verdade, entao o 10/10 da
   DA-12 descreve EXATAMENTE o texto deste modulo. Trocar uma palavra
   aqui invalida a medicao sem deixar rastro -- diferente do
   `RERANKER_MODEL`, que tem invariante automatizada na DA-29.
3. **Editar um `Field(description=...)` do `DiagnosisModel` mudava o
   que o LLM recebia, sem alterar nenhuma linha deste arquivo.** O
   LangChain injeta essas descricoes no schema de tool-calling
   (`app/agent/state.py:18-24`), entao elas sao parte do prompt tanto
   quanto qualquer frase daqui.

**O que o digest cobre, e o que ele nao cobre.** O digest e' sha256 de
um tuplo canonico com: versao, template, nomes dos slots variaveis, as
tres personas, a instrucao JSON e os nomes+descricoes dos campos do
`DiagnosisModel`. Deliberadamente:

- **Nao** cobre o conteudo variavel (logs, payload, chunk RAG, dados do
  conector). Se cobrisse, cada incidente teria um digest diferente e a
  atribuicao nao serviria para nada. O que muda por incidente e'
  variavel *de proposito*.
- **Nao** pretendes ser um snapshot do que foi enviado. E' a identidade
  do *artefato* de prompt. Dois incidentes com o mesmo digest usaram o
  mesmo prompt, mas nao necessariamente o mesmo contexto.

**Por que a duplicacao entre a instrucao JSON e o schema continua.**
`json_instruction` e as `description=` do `DiagnosisModel` dizem coisas
quase iguais com palavras diferentes, e ja divergiram uma vez. Unificar
exigiria reescrever o texto do prompt -- e o texto medido. A DA-53
escolhe nao invalidar o benchmark por causa de um bug latente: o texto
fica como esta, e o digest passa a *incluir* os dois, de modo que mexer
em qualquer um dos dois muda o digest e o gate `prompt_digest_measured`
exige re-medicao. A unificacao fica registrada como trabalho que
precisa vir com promptfoo junto.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.agent.state import DiagnosisModel

# ---------------------------------------------------------------------------
# Artefato
# ---------------------------------------------------------------------------

#: Sobe quando o texto ou a estrutura do prompt muda de proposito. Faz parte
#: do digest, entao um bump sem mudanca real tambem reprova o gate -- que e o
#: comportamento desejado (a versao declarada e' a versao auditada).
PROMPT_VERSION = "1.0.0"

#: Template do prompt. Os `{...}` sao SLOTS: nomes estaveis que o digest
#: inclui, para que adicionar ou remover um bloco de contexto mude o digest
#: mesmo quando o texto dos blocos nao muda. `extras` e `connector_block` sao
#: adjacentes de proposito (o original tinha `{extras}{connector_block}` sem
#: separador).
DIAGNOSIS_TEMPLATE = """{persona}

Incidente reportado:
{description}
{extras}{connector_block}
Contexto recuperado da base de conhecimento de incidentes:
{context_block}
{others_note}{graph_block}{web_block}
Regra importante: baseie sua resposta EXCLUSIVAMENTE no documento de
contexto acima e, se disponivel, nos dados reais do conector (que tem
prioridade sobre a descricao textual do usuario, pois vem diretamente
do sistema). Nao combine informacoes de outros documentos. Se o
documento acima nao corresponder ao sintoma descrito, diga isso e use
confidence baixa em vez de inventar uma causa raiz combinando temas
diferentes.

No campo matched_source, copie EXATAMENTE o nome do arquivo indicado
apos "fonte=" no cabecalho do documento mais relevante mostrado acima
(exemplo: se o cabecalho diz "fonte=cpi_http_401.md", o valor de
matched_source deve ser exatamente "cpi_http_401.md", sem alteracoes).
Se nenhum documento corresponder ao incidente, use null nesse campo.

"confidence" deve ser um numero entre 0.0 e 1.0. Se houver dados reais
do conector confirmando o diagnostico, a confidence pode ser mais alta
(o dado do sistema e mais confiavel que so a descricao textual)."""

#: Nomes dos slots, na ordem em que aparecem no template. Entra no digest
#: como `tuple` ordenada (invariante 18: nada de depender de dict ou XML cru).
VARIABLE_SLOTS: tuple[str, ...] = (
    "persona",
    "description",
    "extras",
    "connector_block",
    "context_block",
    "others_note",
    "graph_block",
    "web_block",
)

#: Instruction de saida do agente ReAct. Verificar `.response_format`
#: pode tornar isto redundante, mas o texto ja estava medido em 10/10 e
#: depende do modelo: alguns provedores locais ignoram `response_format`.
JSON_INSTRUCTION = """

Apos sua analise (usando o tool de busca se necessario), retorne OBRIGATORIAMENTE
um JSON valido com exatamente esta estrutura (sem texto adicional antes ou depois):
{
  "matched_source": "nome_do_arquivo.md ou null",
  "probable_root_cause": "causa raiz em uma ou duas frases",
  "confidence": 0.0,
  "next_steps": ["passo 1", "passo 2"]
}"""

# ---------------------------------------------------------------------------
# Personas (DA-22: uma por dominio, escolhida por classify_domain())
# ---------------------------------------------------------------------------

SAP_SPECIALIST_PERSONA = (
    "Voce e um especialista em integracao SAP (OData, IDoc, RFC, CPI/Integration Suite, BTP)."
)

ENTERPRISE_SPECIALIST_PERSONA = (
    "Voce e um especialista em integracoes empresariais multi-fornecedor "
    "(ServiceNow, Salesforce, Workday, Ariba e APIs corporativas em geral) - "
    "conhece padroes tipicos de falha em REST/OAuth2, webhooks, rate limits "
    "e sincronizacao de dados entre sistemas terceiros. Quando o fornecedor "
    "especifico do incidente nao estiver identificado, aplique o mesmo "
    "raciocinio generalista de troubleshooting de integracao de sistemas."
)

# DA-22: persona propria para incidentes sem dominio identificado
# (agent_domain="generic"). Usa linguagem agnosta de fornecedor -- foco em
# protocolo, transporte e middleware -- sem assumir vocabulario SAP nem
# SaaS especifico.
GENERIC_INTEGRATION_PERSONA = (
    "Voce e um especialista em integracao de sistemas e middleware, com dominio "
    "amplo em padroes de comunicacao (REST, SOAP, gRPC, mensageria), protocolos "
    "de autenticacao (OAuth2, SAML, mTLS), formatos de dados (JSON, XML, CSV) "
    "e ferramentas de integracao (ESB, iPaaS, API gateways). Nao assuma "
    "nenhum fornecedor especifico — analise o incidente com base nos sinais "
    "tecnicos observados (erros HTTP, timeouts, falhas de autenticacao, "
    "problemas de mapeamento) e recomende acoes pragmaticas de troubleshooting."
)

#: Persona por `agent_domain`. O `supervisor.py` decide o dominio; aqui so
#: fica a traducao para texto.
PERSONAS: dict[str, str] = {
    "sap": SAP_SPECIALIST_PERSONA,
    "saas": ENTERPRISE_SPECIALIST_PERSONA,
    "generic": GENERIC_INTEGRATION_PERSONA,
}

#: Fallback de persona para dominio desconhecido. Fail-closed em espaco de
#: texto e' irrelevante, mas um `KeyError` em producao derrubaria o
#: incidente inteiro por um rotulo novo do supervisor.
DEFAULT_PERSONA = GENERIC_INTEGRATION_PERSONA


def persona_for(agent_domain: str | None) -> str:
    """Persona para o dominio. `None`/desconhecido cai na generic."""
    return PERSONAS.get(agent_domain or "", DEFAULT_PERSONA)


# ---------------------------------------------------------------------------
# Schema structured output (parte do prompt, ver docstring do modulo)
# ---------------------------------------------------------------------------


def _schema_fingerprint_parts() -> dict[str, Any]:
    """Nome + descricao + constraints de cada campo do `DiagnosisModel`.

    Incluidos no digest porque o LangChain injeta isso no schema de
    tool-calling: mudar uma `description=` muda o que o LLM le, mesmo
    com este arquivo intacto.
    """
    parts: dict[str, Any] = {}
    for name, field in DiagnosisModel.model_fields.items():
        parts[name] = {
            "annotation": str(field.annotation),
            "description": field.description or "",
            "required": field.is_required(),
            "ge": getattr(field.metadata[0], "ge", None) if field.metadata else None,
            "le": getattr(field.metadata[0], "le", None) if field.metadata else None,
        }
    return parts


# ---------------------------------------------------------------------------
# Spec e digest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSpec:
    """Identidade do artefato de prompt de diagnostico."""

    name: str
    version: str
    digest: str
    slots: tuple[str, ...]
    schema_fields: tuple[str, ...]

    def provenance(self) -> dict[str, str]:
        """O que vai para a resposta, o evento e a tabela `incidents`."""
        return {"prompt_version": self.version, "prompt_digest": self.digest}


def _canonical_payload() -> dict[str, Any]:
    return {
        "name": "diagnosis",
        "version": PROMPT_VERSION,
        "template": DIAGNOSIS_TEMPLATE,
        "slots": list(VARIABLE_SLOTS),
        "personas": dict(PERSONAS),
        "default_persona": DEFAULT_PERSONA,
        "json_instruction": JSON_INSTRUCTION,
        "schema": _schema_fingerprint_parts(),
    }


def compute_digest() -> str:
    """SHA-256 do payload canonico. Estavel entre processos e versoes de Python.

    `sort_keys=True` + `separators` fixos: o digest nao pode depender da
    ordem de insercao do dict nem de espacos de `json.dumps` (o mesmo
    cuidado do `sorted(blob)` do fingerprint de contrato na DA-52).
    """
    blob = json.dumps(
        _canonical_payload(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_SPEC: PromptSpec | None = None


def get_spec() -> PromptSpec:
    """Spec memoizada. O digest e' estavel no processo; recalcular a cada
    incidente seria trabalho jogado fora dentro de um hot path."""
    global _SPEC
    if _SPEC is None:
        _SPEC = PromptSpec(
            name="diagnosis",
            version=PROMPT_VERSION,
            digest=compute_digest(),
            slots=VARIABLE_SLOTS,
            schema_fields=tuple(DiagnosisModel.model_fields),
        )
    return _SPEC


def prompt_version() -> str:
    return get_spec().version


def prompt_digest() -> str:
    return get_spec().digest


def render(**slots: str) -> str:
    """Renderiza o template. `KeyError` se faltar slot ou sobrar.

    Deliberadamente NAO tem default: um slot novo esquecido aqui
    quebraria em producao, mas um slot novo silenciosamente ignorado
    tiraria um bloco de contexto do prompt sem erro nenhum. Falhar alto.
    """
    faltando = [s for s in VARIABLE_SLOTS if s not in slots]
    sobrando = [s for s in slots if s not in VARIABLE_SLOTS]
    if faltando or sobrando:
        raise ValueError(
            f"slots invalidos para o prompt de diagnostico: faltando={faltando} sobrando={sobrando}"
        )
    return DIAGNOSIS_TEMPLATE.format(**slots)
