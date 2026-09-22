"""Testes DA-33 — Rule Engine deterministico.

Cobertura:
- match_known_error: cada categoria de regra retorna diagnóstico sem LLM
- Texto sem padrão conhecido retorna None (pipeline segue para LLM)
- Integração em _run_diagnosis_agent: LLM NÃO é chamado quando rule engine bate
- Flag rule_engine_enabled=False desabilita o desvio e cai no LLM normalmente
- apply_confidence_guardrails é aplicado sobre o resultado do rule engine
- Campos obrigatórios presentes no resultado
- Caso com mensagem do conector (complementa a descrição)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent.rules import KNOWN_ERROR_RULES, ErrorRule, match_known_error

# ---------------------------------------------------------------------------
# Testes de match_known_error
# ---------------------------------------------------------------------------


class TestMatchKnownError:
    def test_oauth_token_expired(self):
        result = match_known_error("oauth token expired during call to SAP gateway")
        assert result is not None
        assert result["rule_engine_category"] == "auth_oauth_expired"
        assert result["confidence"] == 0.90
        assert result["llm_provider_used"] == "rule_engine"
        assert "matched_source" in result
        assert "rule_engine:auth_oauth_expired" in result["matched_source"]

    def test_401_unauthorized(self):
        result = match_known_error("HTTP 401 Unauthorized response from OData endpoint")
        assert result is not None
        assert result["rule_engine_category"] == "auth_oauth_expired"

    def test_403_forbidden(self):
        result = match_known_error("403 Forbidden — authorization failed for user RFC_BG")
        assert result is not None
        assert result["rule_engine_category"] == "auth_forbidden"

    def test_material_lock_m8082(self):
        result = match_known_error("SAP error M8082: material is locked by another process")
        assert result is not None
        assert result["rule_engine_category"] == "sap_material_lock"
        assert "SM12" in " ".join(result["next_steps"])

    def test_pricing_condition_vk041(self):
        result = match_known_error("VK041 condition record missing for material 100-200")
        assert result is not None
        assert result["rule_engine_category"] == "sap_pricing_condition_missing"

    def test_idoc_status_51(self):
        result = match_known_error("IDoc 0000000012345 received status 51 APPLICATION ERROR")
        assert result is not None
        assert result["rule_engine_category"] == "sap_idoc_status_51"
        assert "BD87" in " ".join(result["next_steps"])

    def test_idoc_status_26(self):
        result = match_known_error("IDoc status 26 - Error During Syntax Check on segment E1MARAM")
        assert result is not None
        assert result["rule_engine_category"] == "sap_idoc_status_26"

    def test_http_503(self):
        result = match_known_error("HTTP 503 Service Unavailable from backend system")
        assert result is not None
        assert result["rule_engine_category"] == "http_503_unavailable"

    def test_http_504_timeout(self):
        result = match_known_error("504 Gateway Timeout reading from SAP S/4HANA")
        assert result is not None
        assert result["rule_engine_category"] == "http_timeout"

    def test_connection_refused(self):
        result = match_known_error("Connection refused: ECONNREFUSED 10.0.0.5:44300")
        assert result is not None
        assert result["rule_engine_category"] == "network_connection_refused"

    def test_mapping_fail_cpi(self):
        result = match_known_error("MAPPING_FAIL in iFlow ZORD_TO_IDOC — XSLT parse error")
        assert result is not None
        assert result["rule_engine_category"] == "cpi_mapping_error"

    def test_ssl_certificate_expired(self):
        result = match_known_error("SSL certificate expired: PKIX path build failed")
        assert result is not None
        assert result["rule_engine_category"] == "ssl_certificate_expired"

    def test_rfc_destination_not_found(self):
        result = match_known_error("RFC destination not found: DEST_PRD not configured in SM59")
        assert result is not None
        assert result["rule_engine_category"] == "rfc_destination_error"

    def test_rate_limit_429(self):
        result = match_known_error("429 Too Many Requests — rate limit exceeded on Salesforce API")
        assert result is not None
        assert result["rule_engine_category"] == "rate_limit_exceeded"

    def test_duplicate_document(self):
        result = match_known_error("Document already exists — unique constraint violated")
        assert result is not None
        assert result["rule_engine_category"] == "duplicate_document"

    def test_unknown_error_returns_none(self):
        result = match_known_error("Unexpected business logic issue in custom ABAP enhancement")
        assert result is None

    def test_empty_string_returns_none(self):
        assert match_known_error("") is None

    def test_none_returns_none(self):
        assert match_known_error(None) is None  # type: ignore[arg-type]

    def test_case_insensitive_matching(self):
        result = match_known_error("OAUTH TOKEN EXPIRED")
        assert result is not None
        assert result["rule_engine_category"] == "auth_oauth_expired"

    def test_result_has_required_fields(self):
        result = match_known_error("HTTP 503 backend unavailable")
        assert result is not None
        required = {
            "matched_source",
            "probable_root_cause",
            "confidence",
            "next_steps",
            "rule_engine_category",
            "llm_provider_used",
            "evidence_strength",
        }
        assert required.issubset(result.keys())
        assert isinstance(result["next_steps"], list)
        assert len(result["next_steps"]) >= 1



    def test_idoc_multiple_objects(self):
        result = match_known_error("IDOC_ERROR_MULTIPLE_OBJECTS raised for IDoc type ORDERS05")
        assert result is not None
        assert result["rule_engine_category"] == "sap_idoc_multiple_objects"

    def test_idoc_port_partner_status_68(self):
        result = match_known_error("IDoc status 68 — IDOC SYNTAX ERROR SENDER, port not found in partner profile")
        assert result is not None
        assert result["rule_engine_category"] == "sap_idoc_port_partner"
        assert "WE20" in " ".join(result["next_steps"])

    def test_badi_exception(self):
        result = match_known_error("BAdI exception raised in IF_EX_ME_PROCESS_PO_CUST=>PROCESS_ITEM: CX_BADI validation error")
        assert result is not None
        assert result["rule_engine_category"] == "sap_badi_exception"
        assert "ST22" in " ".join(result["next_steps"])

    def test_bapi_failure(self):
        result = match_known_error("BAPI_SALESORDER_CREATEFROMDAT2 RETURN TYPE E: Material 100-200 not found")
        assert result is not None
        assert result["rule_engine_category"] == "sap_bapi_failure"

    def test_serial_number_duplicate(self):
        result = match_known_error("Serial number SN-00123 duplicate — already assigned to another material")
        assert result is not None
        assert result["rule_engine_category"] == "sap_serial_number_duplicate"
        assert "IQ03" in " ".join(result["next_steps"])

    def test_sd_credit_block(self):
        result = match_known_error("VKM1 credit block: credit limit exceeded for customer 100001")
        assert result is not None
        assert result["rule_engine_category"] == "sap_sd_credit_block"
        assert "FD32" in " ".join(result["next_steps"])

    def test_mdg_mdi_lock(self):
        result = match_known_error("MDI error: master data integration replication failed — MDG lock on BP 0001234")
        assert result is not None
        assert result["rule_engine_category"] == "sap_mdg_mdi_lock"

# ---------------------------------------------------------------------------
# Testes de integração com _run_diagnosis_agent
# ---------------------------------------------------------------------------


class TestRuleEngineIntegration:
    """Verifica que _run_diagnosis_agent não chama o LLM quando rule engine bate."""

    def _make_state(self, description: str, connector_msg: str | None = None) -> dict:
        state = {
            "description": description,
            "connector_data": None,
            "retrieved_context": [],
            "graph_history": [],
            "web_search_results": [],
        }
        if connector_msg:
            cd = MagicMock()
            cd.message = connector_msg
            cd.is_mock = True
            cd.is_fallback = False
            cd.source_system = "mock"
            cd.status = "error"
            cd.error_code = None
            cd.raw = None
            state["connector_data"] = cd
        return state

    def test_rule_engine_skips_llm(self):
        """Quando o rule engine bate, invoke_via_gateway NÃO deve ser chamado."""
        state = self._make_state("oauth token expired during call")
        with (
            patch("app.agent.nodes.invoke_via_gateway") as mock_gw,
            patch("app.agent.nodes.settings") as mock_settings,
        ):
            mock_settings.rule_engine_enabled = True
            mock_settings.web_search_enabled = False
            from app.agent.nodes import _run_diagnosis_agent

            result = _run_diagnosis_agent(state, "persona")
        mock_gw.assert_not_called()
        assert result["llm_provider_used"] == "rule_engine"

    def test_rule_engine_disabled_calls_llm(self):
        """Com rule_engine_enabled=False, a execução deve tentar chamar o LLM."""
        state = self._make_state("oauth token expired during call")
        fake_result = {
            "messages": [
                MagicMock(
                    content='{"probable_root_cause":"x","confidence":0.5,"next_steps":[],"matched_source":null}'
                )
            ],
            "structured_response": None,
        }
        with (
            patch(
                "app.agent.nodes.invoke_via_gateway", return_value=(fake_result, "ollama")
            ) as mock_gw,
            patch("app.agent.nodes.settings") as mock_settings,
            patch("app.agent.nodes.create_react_agent"),
        ):
            mock_settings.rule_engine_enabled = False
            mock_settings.web_search_enabled = False
            mock_settings.react_agent_recursion_limit = 10
            mock_settings.llm_model = "test-model"
            from app.agent.nodes import _run_diagnosis_agent

            _run_diagnosis_agent(state, "persona")
        mock_gw.assert_called_once()

    def test_connector_message_included_in_rule_matching(self):
        """Mensagem do conector enriquece o texto avaliado pelo rule engine."""
        # Descrição vaga, mas conector retorna status 51 no campo message
        state = self._make_state(
            description="IDoc processing failed",
            connector_msg="Status 51 APPLICATION ERROR in IDoc 0000001234",
        )
        with (
            patch("app.agent.nodes.invoke_via_gateway") as mock_gw,
            patch("app.agent.nodes.settings") as mock_settings,
        ):
            mock_settings.rule_engine_enabled = True
            mock_settings.web_search_enabled = False
            from app.agent.nodes import _run_diagnosis_agent

            result = _run_diagnosis_agent(state, "persona")
        mock_gw.assert_not_called()
        assert result["rule_engine_category"] == "sap_idoc_status_51"


# ---------------------------------------------------------------------------
# Testes do catálogo de regras
# ---------------------------------------------------------------------------


class TestKnownErrorRules:
    def test_all_rules_have_patterns(self):
        for rule in KNOWN_ERROR_RULES:
            assert len(rule.patterns) >= 1, f"Regra {rule.category} sem patterns"

    def test_all_rules_have_next_steps(self):
        for rule in KNOWN_ERROR_RULES:
            assert len(rule.next_steps) >= 1, f"Regra {rule.category} sem next_steps"

    def test_all_rules_have_category(self):
        for rule in KNOWN_ERROR_RULES:
            assert rule.category, "Regra sem category"

    def test_all_categories_unique(self):
        cats = [r.category for r in KNOWN_ERROR_RULES]
        assert len(cats) == len(set(cats)), "Categorias duplicadas no catálogo"

    def test_error_rule_compiled_patterns(self):
        rule = ErrorRule(
            patterns=[r"test.*pattern"],
            probable_root_cause="Test cause",
            next_steps=["Step 1"],
            category="test_category",
        )
        assert rule.matches("this is a test pattern match")
        assert not rule.matches("nothing relevant here")
