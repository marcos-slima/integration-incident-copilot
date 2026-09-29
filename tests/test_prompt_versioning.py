"""DA-53: o prompt como artefato versionado.

Tres grupos de teste, na ordem de importancia:

1. **Trava de bytes.** O texto do prompt foi medido em 10/10 pelo promptfoo
   (Fase 12). A DA-53 extraiu o texto de f-strings locais para
   `app/agent/prompts.py`; se um unico byte mudar, a medicao deixa de
   descrever o que roda. Os hashes abaixo sao o contrato.
2. **Sensibilidade do digest.** O digest tem que mudar quando - e so
   quando - o artefato muda, inclusive quando a mudanca esta num
   `Field(description=)` do `DiagnosisModel`, que o LangChain injeta no
   schema de tool-calling sem ninguem passar pelo modulo de prompt.
3. **Nao-contamina.** O digest NAO pode variar com conteudo por incidente,
   e a proveniencia nao pode ser gravada quando o rule engine encerrou
   sem chamar o LLM.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

from app.agent import prompts
from app.agent.nodes import (
    _ENTERPRISE_SPECIALIST_PERSONA,
    _GENERIC_INTEGRATION_PERSONA,
    _SAP_SPECIALIST_PERSONA,
    _build_diagnosis_prompt,
)
from app.agent.state import DiagnosisModel

#: sha256 dos prompts renderizados, capturados ANTES da DA-53 existir, com o
#: texto ainda em f-string dentro de nodes.py. Se estes numeros mudarem, o
#: prompt mudou e o promptfoo precisa ser reexecutado -- nao o teste.
GOLDEN_SHA256 = {
    "sap": "500aefa147007cf4",
    "saas": "09981ab5b53f345f",
    "generic": "fd930398ca018397",
    "vazio": "aab61d2d16c53e7f",
}

BASE_STATE: dict[str, Any] = {
    "description": "IDoc stuck no CPI",
    "logs": "2026-01-01 ERROR timeout",
    "payload": '{"key": "1"}',
    "interface_type": "odata",
    "identifier": "I_Message/001",
    "retrieved_context": [
        {"source": "cpi_http_401.md", "text": "conteudo", "score": 0.9},
        {"source": "b.md", "text": "outro", "score": 0.5},
    ],
    "graph_history": [],
    "web_search_results": [],
    "connector_data": None,
    "matched_source": None,
    "confidence": 0.5,
}


def _sha8(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _case(nome: str) -> tuple[dict[str, Any], str]:
    if nome == "vazio":
        return dict(BASE_STATE, retrieved_context=[], logs=None, payload=None), (
            _SAP_SPECIALIST_PERSONA
        )
    return BASE_STATE, {
        "sap": _SAP_SPECIALIST_PERSONA,
        "saas": _ENTERPRISE_SPECIALIST_PERSONA,
        "generic": _GENERIC_INTEGRATION_PERSONA,
    }[nome]


class TestTravaDeBytes:
    """O texto medido nao pode mudar sem passar pelo promptfoo."""

    @pytest.mark.parametrize("nome", ["sap", "saas", "generic", "vazio"])
    def test_prompt_renderizado_e_byte_identico(self, nome: str):
        state, persona = _case(nome)
        rendered = _build_diagnosis_prompt(state, persona)
        assert _sha8(rendered) == GOLDEN_SHA256[nome], (
            f"o prompt '{nome}' mudou: {_sha8(rendered)} != {GOLDEN_SHA256[nome]}. "
            "O 10/10 do promptfoo mede o texto ANTERIOR; reexecute o promptfoo e "
            "regrave o digest (--write-prompt-baseline) se a mudanca for intencional."
        )

    def test_json_instruction_preservada(self):
        # A instrucao de saida e concatenada ao prompt, nao faz parte dele.
        assert prompts.JSON_INSTRUCTION.startswith("\n\nApos sua analise")
        assert prompts.JSON_INSTRUCTION.endswith('"next_steps": ["passo 1", "passo 2"]\n}')
        for campo in ('"matched_source"', '"probable_root_cause"', '"confidence"', '"next_steps"'):
            assert campo in prompts.JSON_INSTRUCTION

    def test_aliases_em_nodes_apontam_para_o_artefato(self):
        # Evitar que alguém edite o alias achando que é a fonte.
        assert _SAP_SPECIALIST_PERSONA is prompts.SAP_SPECIALIST_PERSONA
        assert _ENTERPRISE_SPECIALIST_PERSONA is prompts.ENTERPRISE_SPECIALIST_PERSONA
        assert _GENERIC_INTEGRATION_PERSONA is prompts.GENERIC_INTEGRATION_PERSONA


class TestRender:
    def _slots(self, **over: str) -> dict[str, str]:
        base = dict.fromkeys(prompts.VARIABLE_SLOTS, "")
        base.update({"persona": "P", "description": "D"})
        base.update(over)
        return base

    def test_render_substitui_todos_os_slots(self):
        out = prompts.render(**self._slots(description="descricao X"))
        assert "descricao X" in out
        assert "{" not in out and "}" not in out

    def test_slot_faltando_falha_alto(self):
        # Falha alto de proposito: um slot esquecido tiraria um bloco de
        # contexto do prompt em silencio, e o digest nao mudaria.
        slots = self._slots()
        del slots["web_block"]
        with pytest.raises(ValueError, match="faltando"):
            prompts.render(**slots)

    def test_slot_desconhecido_falha_alto(self):
        with pytest.raises(ValueError, match="sobrando"):
            prompts.render(**self._slots(slot_inventado="x"))

    def test_valor_com_chaves_nao_e_reinterpretado(self):
        # `.format()` nao reescaneia valores: um log com {incident_id} tem
        # que passar intacto para o LLM.
        out = prompts.render(**self._slots(description="ID {incident_id} nao formatou"))
        assert "ID {incident_id} nao formatou" in out


class TestPersonas:
    @pytest.mark.parametrize("dominio", ["sap", "saas", "generic"])
    def test_persona_conhecida(self, dominio: str):
        assert prompts.persona_for(dominio) == prompts.PERSONAS[dominio]

    @pytest.mark.parametrize("dominio", [None, "", "desconhecido", "SAP"])
    def test_dominio_desconhecido_cai_na_generic(self, dominio: str | None):
        # Fail-closed em espaco de texto nao importa, mas um `KeyError` aqui
        # derrubaria o incidente inteiro por um rotulo novo do supervisor.
        assert prompts.persona_for(dominio) == prompts.GENERIC_INTEGRATION_PERSONA

    def test_persona_generic_nao_assume_fornecedor(self):
        # DA-22: a persona generic tem que ser agnostica, senao o supervisor
        # classificou errado e o prompt empurra o modelo para o fornecedor errado.
        lowered = prompts.GENERIC_INTEGRATION_PERSONA.lower()
        for fornecedor in ("sap", "servicenow", "salesforce", "workday", "ariba"):
            assert f"especialista em {fornecedor}" not in lowered


class TestDigest:
    def test_estavel_dentro_do_processo(self):
        assert prompts.compute_digest() == prompts.compute_digest()

    def test_estavel_entre_processos(self):
        # Digest que muda entre processos tornaria a coluna incidents
        # inagrupavel sem que ninguem tivesse mexido no prompt.
        codigo = "from app.agent.prompts import compute_digest; print(compute_digest())"
        saidas = {
            subprocess.run(
                [sys.executable, "-c", codigo], capture_output=True, text=True, check=True
            ).stdout.strip()
            for _ in range(2)
        }
        assert saidas == {prompts.compute_digest()}

    def test_especifico_do_artefato(self, monkeypatch: pytest.MonkeyPatch):
        original = prompts.compute_digest()
        monkeypatch.setattr(prompts, "DIAGNOSIS_TEMPLATE", prompts.DIAGNOSIS_TEMPLATE + " extra")
        assert prompts.compute_digest() != original

    def test_muda_com_uma_palavra_no_template(self, monkeypatch: pytest.MonkeyPatch):
        original = prompts.compute_digest()
        monkeypatch.setattr(
            prompts, "DIAGNOSIS_TEMPLATE", prompts.DIAGNOSIS_TEMPLATE.replace("EXATAMENTE", "EXATO")
        )
        assert prompts.compute_digest() != original

    def test_muda_com_persona(self, monkeypatch: pytest.MonkeyPatch):
        original = prompts.compute_digest()
        alterado = dict(prompts.PERSONAS, sap=prompts.SAP_SPECIALIST_PERSONA + "!")
        monkeypatch.setattr(prompts, "PERSONAS", alterado)
        assert prompts.compute_digest() != original

    def test_muda_com_instrucao_de_saida(self, monkeypatch: pytest.MonkeyPatch):
        original = prompts.compute_digest()
        monkeypatch.setattr(prompts, "JSON_INSTRUCTION", prompts.JSON_INSTRUCTION + "!")
        assert prompts.compute_digest() != original

    def test_muda_com_a_descricao_de_um_field_do_schema(self, monkeypatch: pytest.MonkeyPatch):
        """O caso traicoeiro: mudar `Field(description=)` sem tocar no modulo de prompt.

        O LangChain injeta essas descricoes no schema de tool-calling
        (app/agent/state.py:18-24), entao elas SAO prompt. Se o digest
        ignorasse o schema, editar um Field invalidaria a medicao do
        promptfoo sem o gate perceber -- que e' o bug que ja aconteceu uma
        vez neste repositorio.
        """
        original = prompts.compute_digest()
        info = DiagnosisModel.model_fields["next_steps"]
        # Guarda o VALOR, nao o FieldInfo: `model_fields[...]` devolve o
        # proprio objeto mutavel, entao salvar a referencia nao salva o
        # texto -- o `finally` devolveria o valor novo e o digest ficaria
        # poluido para os testes seguintes do mesmo processo.
        descricao_original = info.description
        try:
            object.__setattr__(info, "description", "texto completamente novo")
            assert info.description == "texto completamente novo"
            assert prompts.compute_digest() != original
        finally:
            object.__setattr__(info, "description", descricao_original)
            assert info.description == descricao_original
            assert prompts.compute_digest() == original

    def test_muda_com_a_lista_de_slots(self, monkeypatch: pytest.MonkeyPatch):
        # Um slot novo que ninguem renderiza nao muda o texto final, mas
        # muda a estrutura do prompt -- e precisa reprovar.
        original = prompts.compute_digest()
        monkeypatch.setattr(prompts, "VARIABLE_SLOTS", prompts.VARIABLE_SLOTS + ("novo",))
        assert prompts.compute_digest() != original

    def test_nao_varia_com_conteudo_por_incidente(self):
        """O digest NAO pode depender do que foi enviado.

        Se dependesse, cada incidente teria um digest proprio e a coluna
        incidents.prompt_digest nao serviria para atribuir nada.
        """
        renderizado_a = _build_diagnosis_prompt(BASE_STATE, _SAP_SPECIALIST_PERSONA)
        outro = dict(BASE_STATE, description="outro incidente", logs="log totalmente diferente")
        renderizado_b = _build_diagnosis_prompt(outro, _SAP_SPECIALIST_PERSONA)
        assert renderizado_a != renderizado_b
        assert prompts.compute_digest() == prompts.compute_digest()

    def test_spec_expoe_proveniencia(self):
        spec = prompts.get_spec()
        assert spec.name == "diagnosis"
        assert spec.version == prompts.PROMPT_VERSION
        assert spec.slots == prompts.VARIABLE_SLOTS
        assert spec.schema_fields == tuple(DiagnosisModel.model_fields)
        assert spec.provenance() == {
            "prompt_version": prompts.PROMPT_VERSION,
            "prompt_digest": spec.digest,
        }

    def test_spec_e_memoizada(self):
        assert prompts.get_spec() is prompts.get_spec()


class TestProvenienciaNoState:
    def test_llm_registra_a_proveniencia(self, monkeypatch: pytest.MonkeyPatch):
        from app.agent import nodes

        monkeypatch.setattr(nodes.settings, "rule_engine_enabled", False)
        capturado: dict[str, Any] = {}

        class _Msg:
            # O caminho real le `react_result["messages"][-1].content` e
            # depois cai no parsing de JSON (structured_response ausente),
            # entao o fake precisa ter uma mensagem com conteudo plausivel.
            type = "ai"
            content = json.dumps(
                {
                    "probable_root_cause": "timeout do CPI",
                    "confidence": 0.8,
                    "matched_source": None,
                    "next_steps": ["verificar a fila"],
                }
            )

        def fake_gateway(*args: Any, **kwargs: Any) -> tuple[Any, str]:
            return {"messages": [_Msg()]}, "ollama"

        monkeypatch.setattr(nodes, "invoke_via_gateway", fake_gateway)
        monkeypatch.setattr(nodes, "_recover_matched_source_from_raw", lambda raw: None)

        state = dict(BASE_STATE)
        out = nodes._run_diagnosis_agent(state, _SAP_SPECIALIST_PERSONA)
        capturado.update(out)
        assert capturado["prompt_digest"] == prompts.compute_digest()
        assert capturado["prompt_version"] == prompts.PROMPT_VERSION

    def test_rule_engine_nao_registra_prompt(self, monkeypatch: pytest.MonkeyPatch):
        """O path mais importante: rule engine encerra sem LLM.

        Registrar prompt aqui seria afirmar uma origem que nao existe - o
        mesmo erro de coagir `diagnosis_correct` para True (invariante 13).
        Por isso a coluna e' anulavel e nao tem default.
        """
        from app.agent import nodes

        monkeypatch.setattr(nodes.settings, "rule_engine_enabled", True)
        monkeypatch.setattr(
            nodes,
            "match_known_error",
            lambda text, has_connector_data=False: {
                "probable_root_cause": "regra deterministica",
                "model_confidence": 0.9,
                "diagnosis_confidence": 0.9,
                "next_steps": [],
                "llm_provider_used": "rule_engine",
            },
        )
        out = nodes._run_diagnosis_agent(
            dict(BASE_STATE, description="HTTP 401 Unauthorized no CPI"), _SAP_SPECIALIST_PERSONA
        )
        assert out["llm_provider_used"].startswith("rule_engine")
        assert "prompt_digest" not in out
        assert "prompt_version" not in out


class TestPersistencia:
    def _request(self) -> Any:
        from app.models import IncidentRequest

        return IncidentRequest(
            description="IDoc stuck",
            interface_type="odata",
            identifier="I_Message/001",
        )

    def _response(self, **over: Any) -> Any:
        from app.models import DiagnosisResponse

        base = {
            "probable_root_cause": "timeout",
            "model_confidence": 0.5,
            "diagnosis_confidence": 0.4,
            "next_steps": [],
            "report_markdown": "## Diagnostico",
        }
        base.update(over)
        return DiagnosisResponse(**base)

    def _row(self, response: Any) -> dict[str, Any]:
        from app.services.incident_recorder import build_incident_row

        return build_incident_row(
            incident_id="00000000-0000-0000-0000-000000000001",
            request=self._request(),
            response=response,
            final_state={"llm_model": "qwen3-coder-next:latest"},
            latency_ms=10,
        )

    def test_linha_carrega_modelo_e_prompt(self):
        row = self._row(
            self._response(
                llm_model="qwen3-coder-next:latest",
                prompt_version=prompts.PROMPT_VERSION,
                prompt_digest=prompts.compute_digest(),
            )
        )
        assert row["llm_model"] == "qwen3-coder-next:latest"
        assert row["prompt_version"] == prompts.PROMPT_VERSION
        assert row["prompt_digest"] == prompts.compute_digest()

    def test_rule_engine_deixa_as_colunas_nulas(self):
        # NULL = "nao sei/nenhum", nunca "desconhecido" como default.
        row = self._row(self._response(llm_model="qwen3-coder-next:latest"))
        assert row["prompt_version"] is None
        assert row["prompt_digest"] is None

    def test_orm_tem_as_colunas(self):
        from app.services.incident_repository import Incident

        assert hasattr(Incident, "prompt_digest")
        assert Incident.prompt_digest.property.columns[0].type.length == 64
        assert Incident.llm_model.property.columns[0].type.length == 128
        assert all(
            c.nullable
            for c in (
                Incident.prompt_digest.property.columns[0],
                Incident.prompt_version.property.columns[0],
                Incident.llm_model.property.columns[0],
            )
        )


class TestGate:
    def _root(self, tmp_path: Any, baseline: Any) -> Any:
        (tmp_path / "data" / "eval").mkdir(parents=True, exist_ok=True)
        if baseline is not None:
            (tmp_path / "data" / "eval" / "prompt_baseline.json").write_text(
                json.dumps(baseline, ensure_ascii=False), encoding="utf-8"
            )
        return tmp_path

    def test_passa_com_digest_igual(self, tmp_path: Any):
        from app.evaluation.gates import check_prompt_digest

        root = self._root(
            tmp_path,
            {"prompt_version": prompts.PROMPT_VERSION, "prompt_digest": prompts.compute_digest()},
        )
        findings = check_prompt_digest(root)
        assert [f.severity for f in findings] == ["pass"]

    def test_falha_quando_o_prompt_muda(self, tmp_path: Any):
        from app.evaluation.gates import check_prompt_digest

        root = self._root(tmp_path, {"prompt_version": "1.0.0", "prompt_digest": "a" * 64})
        findings = check_prompt_digest(root)
        assert findings[0].is_failure
        assert "promptfoo" in findings[0].message

    def test_avisa_quando_o_baseline_ausenta(self, tmp_path: Any):
        from app.evaluation.gates import check_prompt_digest

        findings = check_prompt_digest(self._root(tmp_path, None))
        assert [f.severity for f in findings] == ["warn"]
        assert not findings[0].is_failure

    def test_falha_com_baseline_malformado(self, tmp_path: Any):
        from app.evaluation.gates import check_prompt_digest

        for baseline in ({}, {"prompt_digest": "curto"}, {"prompt_digest": 12345}, []):
            findings = check_prompt_digest(self._root(tmp_path, baseline))
            assert findings[0].is_failure, baseline

    def test_registrado_no_mapa_de_gates(self):
        from app.evaluation.gates import GATES

        assert GATES["prompt_digest_measured"].__name__ == "check_prompt_digest"

    def test_cli_grava_o_digest_corrente(self, tmp_path: Any):
        from scripts.quality_gate import main

        assert main(["--root", str(tmp_path), "--write-prompt-baseline"]) == 0
        gravado = json.loads(
            (tmp_path / "data" / "eval" / "prompt_baseline.json").read_text(encoding="utf-8")
        )
        assert gravado["prompt_digest"] == prompts.compute_digest()
        assert gravado["prompt_version"] == prompts.PROMPT_VERSION

    def test_cli_preserva_o_contexto_do_baseline(self, tmp_path: Any):
        # Regravar o digest nao pode apagar de ONDE a medicao veio: um
        # baseline sem `measurement` nao diz mais nada.
        from scripts.quality_gate import main

        root = self._root(tmp_path, {"prompt_digest": "a" * 64, "measurement": "10/10 (Fase 12)"})
        assert main(["--root", str(root), "--write-prompt-baseline"]) == 0
        gravado = json.loads((root / "data" / "eval" / "prompt_baseline.json").read_text("utf-8"))
        assert gravado["measurement"] == "10/10 (Fase 12)"
        assert gravado["prompt_digest"] == prompts.compute_digest()


class TestLinhaBateComOORM:
    """`build_incident_row` e' funcao pura; o insert e' o unico lugar onde uma
    chave errada aparece -- e so em producao, com `DATABASE_URL` ligado. Esta
    checagem e' mais barata e mais cedo que um e2e de insert."""

    def _row(self, **over: Any) -> dict[str, Any]:
        from app.models import DiagnosisResponse, IncidentRequest
        from app.services.incident_recorder import build_incident_row

        return build_incident_row(
            incident_id="00000000-0000-0000-0000-000000000001",
            request=IncidentRequest(
                description="IDoc stuck", interface_type="odata", identifier="X"
            ),
            response=DiagnosisResponse(
                probable_root_cause="timeout",
                model_confidence=0.5,
                diagnosis_confidence=0.4,
                next_steps=[],
                report_markdown="## Diagnostico",
                **over,
            ),
            final_state={"llm_model": "qwen3-coder-next:latest"},
            latency_ms=10,
        )

    def test_toda_chave_da_linha_e_coluna_do_orm(self):
        from app.services.incident_repository import Incident

        colunas = set(Incident.__table__.columns.keys())
        sem_coluna = sorted(set(self._row()) - colunas)
        assert sem_coluna == [], (
            f"build_incident_row produz chaves que o ORM nao tem: {sem_coluna}. "
            "O insert vai estourar em runtime, e so com DATABASE_URL ligado."
        )

    def test_a_reverse_tambem_importa(self):
        from app.services.incident_repository import Incident

        # Coluna nova NOT NULL sem valor na row = bug silencioso (estoura no
        # insert, ou pior, fica com default errado). Colunas COM default
        # (`created_at`, `id`) sao excluidas: quem preenche e' o proprio ORM,
        # e a row nao precisa carrega-las.
        required = {
            c.name
            for c in Incident.__table__.columns
            if not c.nullable and c.default is None and c.server_default is None
        }
        faltando = sorted(required - set(self._row()))
        assert faltando == [], f"colunas NOT NULL sem default e sem valor na row: {faltando}"

    def test_proveniencia_sobe_para_a_linha(self):
        row = self._row(
            llm_model="qwen3-coder-next:latest",
            prompt_version=prompts.PROMPT_VERSION,
            prompt_digest=prompts.compute_digest(),
        )
        assert (row["llm_model"], row["prompt_version"], row["prompt_digest"]) == (
            "qwen3-coder-next:latest",
            prompts.PROMPT_VERSION,
            prompts.compute_digest(),
        )
