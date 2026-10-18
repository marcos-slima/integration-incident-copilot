"""DA-74: idioma do conteudo do diagnostico.

Sem LLM, sem Qdrant: o que se testa e' o contrato - pt-BR continua
byte a byte o comportamento de antes (prompt medido, regras, avisos,
relatorio, ativacao) e `en` troca o texto sem trocar a decisao.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent import nodes, prompts
from app.agent.ontology_loader import GENERIC_ROOT_CAUSE, load_error_rules
from app.agent.rules import KNOWN_ERROR_RULES, match_known_error
from app.i18n.checks import content_i18n_problems, rule_categories
from app.i18n.content import content_text, localize_rule
from app.models import DiagnosisResponse, IncidentEventData, IncidentRequest

ROOT = Path(__file__).resolve().parent.parent
IDOC_51 = "IDoc travado com status 51 no sistema de destino"


# ---------------------------------------------------------------------------
# Pedido e resposta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("en", "en"),
        ("en-US", "en"),
        ("pt-BR", "pt-BR"),
        ("pt-PT", "pt-BR"),
    ],
)
def test_request_normaliza_a_tag_do_navegador(raw, expected):
    assert IncidentRequest(description="x", locale=raw).locale == expected
    assert IncidentEventData(description="x", locale=raw).locale == expected


@pytest.mark.parametrize("raw", ["es", "xx", "klingon"])
def test_request_rejeita_idioma_sem_conteudo(raw):
    with pytest.raises(ValidationError, match="locale nao suportado"):
        IncidentRequest(description="x", locale=raw)


def test_resposta_declara_o_idioma_com_default_pt_br():
    resp = DiagnosisResponse.model_fields["locale"]
    assert resp.default == "pt-BR"


def test_run_diagnosis_leva_o_idioma_ao_estado_e_a_resposta(monkeypatch):
    from app.agent import graph

    seen = {}

    def fake_invoke(initial_state, timeout_seconds=None, thread_id=None):
        seen["locale"] = initial_state["locale"]
        return {**initial_state, "diagnosis": {}, "report_markdown": ""}

    monkeypatch.setattr(graph, "_invoke_graph_with_timeout", fake_invoke)
    monkeypatch.setattr(graph, "record_incident", lambda *a, **k: None, raising=False)

    assert graph.run_diagnosis(IncidentRequest(description="x", locale="en")).locale == "en"
    assert seen["locale"] == "en"
    assert graph.run_diagnosis(IncidentRequest(description="x")).locale == "pt-BR"
    assert seen["locale"] == "pt-BR"


# ---------------------------------------------------------------------------
# Prompt: pt-BR e' o medido; en tem digest proprio
# ---------------------------------------------------------------------------


def test_digest_pt_br_continua_o_medido():
    baseline = json.loads((ROOT / "data/eval/prompt_baseline.json").read_text(encoding="utf-8"))
    assert prompts.compute_digest() == baseline["prompt_digest"]
    assert prompts.compute_digest("pt-BR") == baseline["prompt_digest"]


def test_en_tem_digest_e_instrucao_proprios():
    assert prompts.output_language_instruction("pt-BR") == ""
    assert prompts.output_language_instruction(None) == ""
    assert "English" in prompts.output_language_instruction("en")
    assert prompts.compute_digest("en") != prompts.compute_digest()
    assert prompts.get_spec("en").locale == "en"
    assert prompts.get_spec("en").digest == prompts.compute_digest("en")
    # idioma sem prompt proprio usa o default
    assert prompts.get_spec("xx").digest == prompts.compute_digest()
    assert prompts.get_spec(None).locale == "pt-BR"


def test_mudar_a_instrucao_en_muda_so_o_digest_en(monkeypatch):
    pt, en = prompts.compute_digest(), prompts.compute_digest("en")
    monkeypatch.setitem(prompts.OUTPUT_LANGUAGE_INSTRUCTIONS, "en", "\n\nAnswer in English.")
    assert prompts.compute_digest() == pt
    assert prompts.compute_digest("en") != en


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------


def test_regra_do_ttl_herda_a_causa_curada_de_rules_py():
    """Antes da DA-74 a regra do TTL sem probableRootCause devolvia o texto
    generico e escondia a causa curada da regra em codigo de mesma categoria."""
    hard = {r.category: r for r in KNOWN_ERROR_RULES}
    by_cat = {r.category: r for r in load_error_rules()}
    assert (
        by_cat["sap_idoc_status_51"].probable_root_cause
        == hard["sap_idoc_status_51"].probable_root_cause
    )
    assert all(r.probable_root_cause != GENERIC_ROOT_CAUSE for r in by_cat.values())


def test_passo_com_virgula_nao_e_quebrado():
    steps = {r.category: r for r in load_error_rules()}["auth_unauthorized"].next_steps
    assert "Validar credenciais (token, API key, Basic Auth)" in steps
    assert len(steps) == 4


def test_regra_em_pt_br_por_default_e_em_en_quando_pedido():
    pt = match_known_error(IDOC_51)
    en = match_known_error(IDOC_51, locale="en")
    assert pt["rule_engine_category"] == en["rule_engine_category"] == "sap_idoc_status_51"
    assert pt["probable_root_cause"].startswith("IDoc com status 51")
    assert en["probable_root_cause"].startswith("IDoc in status 51")
    assert en["confidence"] == pt["confidence"]  # a decisao nao muda
    assert en["next_steps"][0].startswith("Use transaction BD87")


def test_padrao_em_ingles_casa_e_responde_no_idioma_pedido():
    out = match_known_error("RFC_COMMUNICATION_FAILURE network unreachable", locale="en")
    assert out is not None and out["probable_root_cause"].startswith("RFC communication failure")


def test_regra_sem_traducao_cai_no_pt_br():
    root, steps = localize_rule("categoria_nova", "causa pt", ["passo"], "en")
    assert (root, steps) == ("causa pt", ["passo"])


# ---------------------------------------------------------------------------
# Avisos dos guardrails e relatorio
# ---------------------------------------------------------------------------


def _llm_diag(**kw):
    base = {
        "probable_root_cause": "causa X",
        "confidence": 0.9,
        "matched_source": None,
        "next_steps": [],
    }
    base.update(kw)
    return base


def test_aviso_de_confianca_no_idioma_pedido(monkeypatch):
    # sem documento nem conector; evidencia alta forcada para o teto de
    # 0.3 ser o que corta (e o aviso ser acrescentado)
    monkeypatch.setattr(nodes, "_compute_evidence_strength", lambda state: 0.9)
    pt = nodes._apply_confidence_guardrails(_llm_diag(), {"description": "x"})
    en = nodes._apply_confidence_guardrails(_llm_diag(), {"description": "x", "locale": "en"})
    assert pt["probable_root_cause"] == (
        "[confianca limitada - nenhum documento relevante encontrado] causa X"
    )
    assert en["probable_root_cause"] == "[limited confidence - no relevant document found] causa X"


def test_aviso_de_fonte_inventada_no_idioma_pedido():
    state = {"description": "x", "locale": "en", "retrieved_context": [{"source": "a.md"}]}
    out = nodes._apply_confidence_guardrails(_llm_diag(matched_source="inventado.md"), state)
    assert out["probable_root_cause"].startswith(
        "[matched_source 'inventado.md' is not among the retrieved documents"
    )


def _report_state(locale=None):
    data = SimpleNamespace(
        is_fallback=False, is_mock=True, source_system="SAP S/4", status="51", error_code="E1"
    )
    state = {
        "description": "IDoc parado",
        "connector_data": data,
        "agent_domain": "sap",
        "llm_model": "m1",
        "diagnosis": {
            "probable_root_cause": "causa",
            "next_steps": ["p1"],
            "matched_source": None,
            "model_confidence": 0.5,
            "diagnosis_confidence": 0.4,
            "evidence_strength": 0.3,
            "llm_provider_used": "ollama",
            "prompt_version": "1.0.0",
        },
    }
    if locale:
        state["locale"] = locale
    return state


def test_relatorio_pt_br_identico_ao_de_antes(monkeypatch):
    monkeypatch.setattr(nodes, "_assemble_evidence", lambda state: [])
    monkeypatch.setattr(nodes, "_record_quality_metrics", lambda state, diag: None)
    report = nodes.report_node(_report_state())["report_markdown"]
    expected = """## Diagnostico do Incidente

**Descricao reportada:** IDoc parado

**Dados do sistema (SAP S/4 - simulado):** status=51, codigo=E1

**Causa raiz provavel:** causa

**Confianca:**
- `diagnosis_confidence` (pipeline): 40% — use este para automacao
- `model_confidence` (LLM pos-guardrail): 50%
- `evidence_strength` (retrieval/conector): 30%
- Provider: ollama | Agente: sap | Modelo: m1 | Prompt: 1.0.0

**Documento usado como base:** nenhum documento especifico identificado

**Proximos passos:**
- p1

**Primary Evidence** (observado diretamente pelo sistema — alta confianca):
- (nenhuma evidencia direta do sistema — diagnostico baseado em inferencia)

**Supporting Facts** (RAG / busca web / relato do usuario — revisao humana recomendada):
- (nenhum fato de suporte)
"""
    assert report == expected


def test_relatorio_en(monkeypatch):
    monkeypatch.setattr(nodes, "_assemble_evidence", lambda state: [])
    monkeypatch.setattr(nodes, "_record_quality_metrics", lambda state, diag: None)
    report = nodes.report_node(_report_state("en"))["report_markdown"]
    assert report.startswith("## Incident Diagnosis")
    assert "**System data (SAP S/4 - simulated):** status=51, code=E1" in report
    assert "**Document used as basis:** no specific document identified" in report
    assert "Diagnostico" not in report


def test_texto_inserido_nao_e_reinterpretado():
    assert content_text("en", "diagnosis.capInvalidSource", source="{{source}}").startswith(
        "[matched_source '{{source}}'"
    )


# ---------------------------------------------------------------------------
# Ativacao
# ---------------------------------------------------------------------------


def test_mensagens_de_ativacao_pt_br_iguais_as_de_antes():
    from app.webusers import activation_email_text, activation_sms_text

    assert activation_email_text("T", None) == (
        "Ativacao de acesso - Integration Incident Copilot",
        "Token de ativacao: T",
    )
    assert activation_sms_text("123", None) == "Codigo de ativacao: 123"
    assert activation_email_text("T", "en")[1] == "Activation token: T"
    assert activation_sms_text("123", "en") == "Activation code: 123"


def test_create_user_grava_o_idioma_e_envia_no_idioma(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.admin.models import WebUser
    from app.config import settings
    from app.webusers import create_user

    monkeypatch.setattr(settings, "session_secret", "segredo-de-teste")
    sent = {}

    def fake_deliver(to, body, subject=None):
        sent.update(to=to, body=body, subject=subject)
        return SimpleNamespace(delivered=False)

    monkeypatch.setattr("app.webusers.deliver_email", fake_deliver)

    async def run():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(WebUser.__table__.create, checkfirst=True)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user, _ = await create_user(
                session,
                username="bob",
                email="bob@corp.com",
                phone="+5511777777777",
                password="senha-inicial-789",
                created_by="test",
                locale="en",
            )
            return user.locale

    assert asyncio.run(run()) == "en"
    assert sent["subject"] == "Access activation - Integration Incident Copilot"
    assert sent["body"].startswith("Activation token: ")


# ---------------------------------------------------------------------------
# Textos de backend que as interfaces exibem
# ---------------------------------------------------------------------------


def test_status_do_conector_traz_a_variavel_para_a_ui():
    from app.connectors import connector_status

    info = connector_status()["odata"]
    assert info["setting"].isupper() and info["setting"] in info["note"]


def test_motivo_de_soberania_tem_codigo_e_o_texto_de_antes():
    from app.config import Settings
    from app.llm.routing import _provider_allows_sensitivity, sovereignty_decision

    cfg = Settings(_env_file=None, openai_api_key="x", data_sovereignty_mode="strict")
    d = sovereignty_decision("openai", "confidential", cfg)
    assert (d.allowed, d.code) == (False, "mode_required")
    assert d.params == {"mode": "strict"}
    assert _provider_allows_sensitivity("openai", "confidential", cfg) == (
        False,
        "dado confidencial para cloud exige data_sovereignty_mode='cloud_with_dlp' (atual: 'strict')",
    )


def test_llm_policy_expoe_os_codigos():
    from app.llm.routing import describe_effective_policy

    ollama = describe_effective_policy()["providers"]["ollama"]
    assert ollama["confidential_reason_code"] in {
        "local_origin",
        "origin_unresolved",
        "allowlisted",
    }
    assert isinstance(ollama["confidential_reason_params"], dict)


def test_catalogos_das_uis_traduzem_todos_os_codigos_de_soberania():
    from app.llm.routing import _SOVEREIGNTY_TEXT

    for path in (
        ROOT / "frontend/src/i18n/locales/en.json",
        ROOT / "frontend/src/i18n/locales/pt-BR.json",
        ROOT / "app/i18n/locales/admin.en.json",
        ROOT / "app/i18n/locales/admin.pt-BR.json",
    ):
        cat = json.loads(path.read_text(encoding="utf-8"))
        assert set(cat["sovereigntyReason"]) == set(_SOVEREIGNTY_TEXT), path.name


# ---------------------------------------------------------------------------
# Gate content_i18n
# ---------------------------------------------------------------------------


def test_gate_passa_no_repositorio():
    assert content_i18n_problems(ROOT) == []


def _tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "app/i18n/locales").mkdir(parents=True)
    (tmp_path / "app/ontology").mkdir(parents=True)
    for loc in ("pt-BR", "en"):
        shutil.copy(
            ROOT / f"app/i18n/locales/content.{loc}.json",
            tmp_path / f"app/i18n/locales/content.{loc}.json",
        )
    shutil.copy(ROOT / "app/ontology/error_codes.ttl", tmp_path / "app/ontology/error_codes.ttl")
    return tmp_path


def _edit_en(root: Path, fn) -> None:
    path = root / "app/i18n/locales/content.en.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_gate_reprova_regra_sem_traducao(tmp_path):
    root = _tmp_root(tmp_path)
    _edit_en(root, lambda d: d["rules"].pop("sap_idoc_status_51"))
    assert any("sap_idoc_status_51" in p for p in content_i18n_problems(root))


def test_gate_reprova_variavel_divergente(tmp_path):
    root = _tmp_root(tmp_path)
    _edit_en(root, lambda d: d["activation"].update(emailBody="Activation token"))
    assert any("activation.emailBody" in p for p in content_i18n_problems(root))


def test_categorias_cobrem_ttl_e_codigo():
    cats = rule_categories(ROOT)
    assert {r.category for r in KNOWN_ERROR_RULES} <= cats
    assert "oauthtokenrefreshfailed" in cats
