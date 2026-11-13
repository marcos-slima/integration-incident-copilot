"""DA-44: sinal deterministico de escalonamento em tres tiers.

PROBLEMA

Para o tier 3 (modelo pago: GPT/Claude/Gemini) o pipeline precisa de um
gatilho: quando a resposta do modelo local nao e' boa o bastante, escalar.
O sinal obvio seria `evidence_strength` (DA-15), e ele NAO serve, por tres
motivos - todos verificados no codigo, nenhum presumido:

1. PISO QUE SATURA. `nodes.py::_compute_evidence_strength()` faz
   `strength = max(rag_score, 0.75)` quando o conector e' real. Com dado
   real de conector, `evidence_strength` nunca fica abaixo de 0.75 - que
   e' exatamente o caminho de producao. Qualquer limiar de escalonamento
   acima de 0.75 NUNCA dispara com conector real.

2. NAO SABE DE QUE TIER VEIO A EVIDENCIA. O mesmo numero significa coisas
   diferentes conforme a origem. `sap_incident_docs` (40 pontos curados
   para incidente) e `sap_reference_library` (28.962 chunks de manuais
   genericos) passam pelo MESMO reranker e pela MESMA escala de sigmoid
   (DA-42), mas nao tem o mesmo peso probatorio. Um limiar unico nao
   esta' bem definido.

3. SATURA E NAO DISCRIMINA. Mede "quanto contexto existe", nao "o modelo
   acertou". Um modelo que alucina com confianca alta recebe o mesmo
   `evidence_strength` que um que acerta.

O QUE ESTE MODULO FAZ (e o que NAO faz)

Entrega um sinal NOVO, aditivo, que decide o escalonamento. NAO altera
`evidence_strength` em nada - mexer nele para acomodar a cascata
enfraqueceria DA-16, que existe justamente para o sinal ser forte.
Invariante #4 do CLAUDE.md: `_assemble_evidence()` nunca usa
autoavaliacao do LLM. O mesmo vale aqui.

O sinal e' derivado de APENAS fatos observaveis e ja decididos em codigo:

- `connector_data.is_mock` / `.is_fallback`   (base.py)
- `hit["collection"]`                          (retriever.py, l.182/209)
- `hit["rerank_score_calibrated"]`             (retriever.py, DA-42)
- `diagnosis["matched_source"] is None`        (nodes.py, l.740-767: o
  guardrail ja NULOU a fonte quando ela nao estava entre os documentos
  recuperados - ou seja, codescisao de abstencao, nao juizo do LLM)

VALIDACAO EMPIRICA (2026-09-28, provider local, pipeline real)

Este sinal foi exercitado contra o pipeline de verdade, nao so em teste
unitario. O resultado corrigiu uma hipotese minha:

- CASO QUE MOTIVOU A DA - "algo estranho aconteceu", odata, identifier
  XPTO-999-NAO-EXISTE. Resultado: `matched_source='odata_timeout_cpi.md'`
  com `abstained=False`, top_evidence=0.383, sinal `curated_tier_weak`,
  escala=True.
  A REGRA DE ABSTENCAO NAO DISPAROU. O guardrail so anula
  `matched_source` quando o documento cited NAO esta entre os
  recuperados; aqui o documento ESTAVA no conjunto recuperado, apenas
  com evidencia semantica fraca (0.383 < 0.45), entao nada o anulou.
  Quem pegou o caso foi a regra 3. Se a DA tivesse implementado apenas
  a abstencao, a falha real teria passado. Multiple regras, nao uma.
  Isso contraria a leitura de que abstencao seria "o sinal mais forte":
  ela e' a mais inequivoca quando dispara, mas nao cobre o modo de
  falha mais comum.

- CONTROLE - IDoc status 51 (rfc, RFC-IDOC-51-DEMO). Resultado:
  `matched_source='rule_engine:sap_idoc_status_51'`, top_evidence=1.000,
  sinal `grounded`, escala=False. Correto: o rule engine resolveu antes
  do LLM e a evidencia e' maxima.

LIMIACAO QUE AINDA NAO FOI EXERCITADA

O piso de 0.75 (`nodes.py:581`) so vale quando `connector_real` e'
verdadeiro, e NENHUM conector do lab produz isso: `rfc_connector.py` se
declara simulador (linha 1) e devolve `is_mock=True` mesmo para
identificador reconhecido. O caminho em que `evidence_strength` fica
cego e' portanto INEXPERIMENTAVEL aqui - foi verificado por leitura de
codigo, nao por execucao. Para exercita-lo de verdade seria preciso um
conector real (OData/RFC de verdade, nao simulado), e o resultado da
DA nesse caminho continua sendo uma expectativa, nao uma medicao.

LIMITACOES CONHECIDAS

1. `FLOOR_TIER_MIN_EVIDENCE = 0.62` e `CURATED_TIER_MIN_EVIDENCE =
   0.45` NAO foram calibrados contra o corpus de 28.962 chunks. Sao
   pontos de partida explicitos, escolhidos pela escala de sigmoid
   (DA-42), e precisam ser substituidos por medicao no re-baseline.
   Tratar como hipotese, nao como constante validada.

2. `REFERENCE_FALLBACK_THRESHOLD` (retriever.py) foi recalibrado em
   2026-10-01 contra o corpus real: 0.665, no meio da faixa de
   separacao medida entre queries verdadeiras (min 0.672) e falsas
   (max 0.658). O valor anterior, 0.85, era justificado por um
   comentario que citava "766k+ chunks" - corpus que nunca existiu aqui
   - e media empiricamente 20 de 20 verdadeiros rejeitados, ou seja,
   desligava o fallback inteiro. A medicao vale para
   `nomic-embed-text`: trocar o modelo de embedding desloca a escala
   de cosseno e a invalida. O corpus ainda estava incompleto (26%)
   quando a medicao foi feita.

3. O tier 3 (invocacao do modelo pago) NAO faz parte desta DA. Este
   modulo decide APENAS se ha caso para escalar. Quem invoca passa
   pelo AI Gateway (DA-26) e por isso responde a politica de
   data_sovereignty_mode / CONFIDENTIAL_ALLOWED_ORIGINS (DA-43).

ESCALA

Este sinal NAO tem piso de 0.75 - e' por isso que ele funciona no
caminho de conector real, onde `evidence_strength` satura. Ele responde
"a base probatoria sustenta a resposta?", nao "quanto contexto ha?".
"""

from __future__ import annotations

from dataclasses import dataclass

# Collections - espelham app/rag/retriever.py::COLLECTIONS.
CURATED_COLLECTION = "sap_incident_docs"
FLOOR_COLLECTION = "sap_reference_library"

#: Calibracao inicial do tier de piso. NAO calibrado contra o corpus de
#: 28.962 chunks ainda - e' ponto de partida explicito, para ser
#: substituido pelo re-baseline. Ver DA-44 "Limitacoes".
FLOOR_TIER_MIN_EVIDENCE = 0.62

#: Teto de evidencia abaixo do qual um documento curado tambem e' suspeito.
#: Abaixo disso, mesmo vindo de `sap_incident_docs`, o cross-encoder nao
#: conseguiu sustentar o par (query, chunk).
CURATED_TIER_MIN_EVIDENCE = 0.45


class Reason:
    """Codigos de razao. Estaveis - viram contrato para o gateway, os
    testes e o log de auditoria. Nao usar texto livre aqui."""

    #: Evidencia do tier curado acima do piso. Nada a escalar.
    GROUNDED = "grounded"
    #: Sem contexto nenhum: nem conector, nem chunk recuperado.
    NO_CONTEXT = "no_context"
    #: Guardrail anulou matched_source - conviccao sem lastro.
    ABSTAINED = "abstained"
    #: Evidencia veio do tier de piso (manuais genericos) e o reranker
    #: nao sustentou o par acima de FLOOR_TIER_MIN_EVIDENCE.
    FLOOR_TIER_WEAK = "floor_tier_weak"
    #: Documento curado, mas o reranker nao sustentou o par.
    CURATED_TIER_WEAK = "curated_tier_weak"


@dataclass(frozen=True)
class EscalationDecision:
    """Decisao de escalonamento. Imutavel: e' um veredito ja decidido, nao
    um acumulador que o chamador possa ajustar por engano."""

    should_escalate: bool
    reason: str
    tier: str
    abstained: bool
    top_evidence: float
    connector_real: bool

    @property
    def escalate_to(self) -> str | None:
        """Tier de destino quando ha escalonamento. None quando nao ha.

        Deliberadamente NAO aqui: qual provider pago atende. Isso e'
        decisao do AI Gateway (DA-26), que aplica a politica de
        data_sovereignty_mode / CONFIDENTIAL_ALLOWED_ORIGINS (DA-43).
        Este modulo nao conhece, e nao pode contornar, a politica.
        """
        return "cloud_premium" if self.should_escalate else None

    def as_log_fields(self) -> dict:
        """Campos para log/auditoria. Sem conteudo de documento e sem PII
        - so o veredito e os numeros que o produziram."""
        return {
            "escalation": self.reason,
            "should_escalate": self.should_escalate,
            "tier": self.tier,
            "abstained": self.abstained,
            "top_evidence": round(self.top_evidence, 3),
            "connector_real": self.connector_real,
        }


def _top_hit(state) -> dict:
    hits = state.get("retrieved_context") or []
    return hits[0] if hits else {}


def _top_evidence(hit: dict) -> float:
    """Evidencia do melhor chunk, NA escala calibrada (DA-42).

    Usa `rerank_score_calibrated` e nao `score` (cosseno bruto) porque os
    dois tiers passam pelo mesmo reranker: um e' e' o unico valor
    comparavel entre `sap_incident_docs` e `sap_reference_library`.
    """
    if not hit:
        return 0.0
    return float(hit.get("rerank_score_calibrated", 0.0))


def compute_escalation_signal(state) -> EscalationDecision:
    """Decide se o resultado local justifica consultar um modelo pago.

    Determinista por construcao: le facts do state, nao chama LLM, nao
    le confianca auto-relatada. Duas chamadas com o mesmo state produzem
    a mesma decisao.

    Ordem das regras e' significante e vai do sinal mais forte ao mais
    fraco; a primeira que casar define a razao.
    """
    data = state.get("connector_data")
    diagnosis = state.get("diagnosis") or {}
    hit = _top_hit(state)
    top_evidence = _top_evidence(hit)
    collection = hit.get("collection") or ""
    connector_real = bool(data) and not data.is_mock and not data.is_fallback
    abstained = diagnosis.get("matched_source", "") is None

    has_context = bool(hit) or bool(data)

    # --- regra 0: sem contexto -------------------------------------------------
    # NAO escala. Modelo maior nao cria evidencia que nao existe: sem chunk e
    # sem conector, o que o tier 3 produziria nao e' resposta melhor, e'
    # alucina mais confiante - exatamente o modo de falha que o Evidence/
    # Trust Layer (DA-25) existe para conter. Pior para o engenheiro de
    # suporte do que receber "dados insuficientes".
    # Se o portfolio quiser escalar aqui mesmo assim, e' uma mudanca de
    # produto e nao de guardrail - e o custo e' por conta de quem decide.
    if not has_context:
        return EscalationDecision(
            should_escalate=False,
            reason=Reason.NO_CONTEXT,
            tier="none",
            abstained=abstained,
            top_evidence=0.0,
            connector_real=False,
        )

    # --- regra 1: abstencao ---------------------------------------------------
    # O guardrail anulou matched_source: o sistema nao conseguiu lastrear a
    # resposta em nenhum documento recuperado. Sinal mais forte que existe
    # aqui, e' decidido em codigo (nodes.py, validacao de matched_source).
    if abstained:
        return EscalationDecision(
            should_escalate=True,
            reason=Reason.ABSTAINED,
            tier="ungrounded",
            abstained=True,
            top_evidence=top_evidence,
            connector_real=connector_real,
        )

    # --- regra 2: tier de piso com evidencia fraca ----------------------------
    if collection == FLOOR_COLLECTION and top_evidence < FLOOR_TIER_MIN_EVIDENCE:
        return EscalationDecision(
            should_escalate=True,
            reason=Reason.FLOOR_TIER_WEAK,
            tier="floor",
            abstained=False,
            top_evidence=top_evidence,
            connector_real=connector_real,
        )

    # --- regra 3: tier curado com evidencia fraca ----------------------------
    if collection in (CURATED_COLLECTION, "") and top_evidence < CURATED_TIER_MIN_EVIDENCE:
        return EscalationDecision(
            should_escalate=True,
            reason=Reason.CURATED_TIER_WEAK,
            tier="curated_weak",
            abstained=False,
            top_evidence=top_evidence,
            connector_real=connector_real,
        )

    # --- regra 4: evidenceado -------------------------------------------------
    return EscalationDecision(
        should_escalate=False,
        reason=Reason.GROUNDED,
        tier="curated" if collection == CURATED_COLLECTION else "floor",
        abstained=False,
        top_evidence=top_evidence,
        connector_real=connector_real,
    )
