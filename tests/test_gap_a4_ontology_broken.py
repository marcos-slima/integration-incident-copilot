"""Teste de regressão para gap A4: Ontologia quebrada.

Bug: retrieve_node executa state.get("connector_data", {}).get("error_code");
connector_data é um ConnectorResult (dataclass sem .get()). Isso causa
AttributeError quando ontology_enrichment_enabled=true e connector_data tem valor.

Correção: usar connector_data.error_code (atributo direto) ou getattr(connector_data, "error_code", None).
"""

from unittest.mock import patch

import pytest

from app.agent.nodes import retrieve_node
from app.agent.state import CopilotState
from app.config import settings
from app.connectors import ConnectorResult


class TestGapA4OntologyBroken:
    """Testes para o gap A4: Ontologia quebrada."""

    def test_retrieve_node_com_connector_result_dataclass(self):
        """Valida que o bug A4 foi corrigido: retrieve_node funciona com ConnectorResult."""
        # Cria um ConnectorResult (dataclass) com error_code
        connector_result = ConnectorResult(
            source_system="OData",
            status="error",
            error_code="HTTP401",
            message="Unauthorized",
            raw="{}",
            is_mock=False,
            is_fallback=False,
        )

        # Estado com connector_data como ConnectorResult (não dict)
        state: CopilotState = {
            "description": "Test description",
            "query": "test query",
            "connector_data": connector_result,  # Dataclass, não dict!
            "interface_type": "odata",
            "identifier": "test-id",
        }

        # Simula ontology_enrichment_enabled=True para forçar o caminho corrigido
        with patch("app.agent.nodes.settings") as mock_settings:
            mock_settings.ontology_enrichment_enabled = True
            # Mocka get_upper_categories_from_category para não causar erro de carregamento TTL
            with (
                patch("app.agent.nodes.get_upper_categories_from_category", return_value=None),
                patch("app.agent.nodes.retrieve", return_value=[]),
            ):
                # Após a correção, isso NÃO deve causar AttributeError
                result = retrieve_node(state)
                assert "retrieved_context" in result

    def test_retrieve_node_com_connector_result_dict_funciona(self):
        """Com connector_data como dict, o código falha porque espera ConnectorResult."""
        # Estado com connector_data como dict (não é o tipo esperado)
        state: CopilotState = {
            "description": "Test description",
            "query": "test query",
            "connector_data": {"error_code": "HTTP401"},  # Dict, não ConnectorResult!
            "interface_type": "odata",
            "identifier": "test-id",
        }

        # O código espera ConnectorResult (dataclass), não dict
        # Isso causará AttributeError: 'dict' object has no attribute 'message'
        with pytest.raises(AttributeError, match="'dict' object has no attribute 'message'"):
            retrieve_node(state)

    def test_retrieve_node_sem_connector_data(self):
        """Com connector_data ausente, o código deve funcionar (fallback para retrieval base)."""
        state: CopilotState = {
            "description": "Test description",
            "query": "test query",
            "interface_type": "odata",
            "identifier": "test-id",
        }

        result = retrieve_node(state)
        assert "retrieved_context" in result

    def test_retrieve_node_com_connector_result_none(self):
        """Com connector_data=None, o código deve funcionar."""
        state: CopilotState = {
            "description": "Test description",
            "query": "test query",
            "connector_data": None,
            "interface_type": "odata",
            "identifier": "test-id",
        }

        result = retrieve_node(state)
        assert "retrieved_context" in result

    @pytest.mark.skipif(
        not settings.ontology_enrichment_enabled,
        reason="Teste requer ontology_enrichment_enabled=true",
    )
    def test_retrieve_node_com_ontology_enrichment_e_connector_result(self):
        """Teste completo: ontology_enrichment_enabled=true + connector_data como ConnectorResult."""
        # Cria um ConnectorResult (dataclass) com error_code
        connector_result = ConnectorResult(
            source_system="OData",
            status="error",
            error_code="HTTP401",
            message="Unauthorized",
            raw="{}",
            is_mock=False,
            is_fallback=False,
        )

        # Estado com connector_data como ConnectorResult (não dict)
        state: CopilotState = {
            "description": "Test description",
            "query": "test query",
            "connector_data": connector_result,  # Dataclass, não dict!
            "interface_type": "odata",
            "identifier": "test-id",
        }

        # Com o código atual, isso causará AttributeError
        with pytest.raises(AttributeError, match="'ConnectorResult' object has no attribute 'get'"):
            retrieve_node(state)


class TestGapA4Correcao:
    """Testes para validar a correção do gap A4."""

    def test_getattr_funciona_com_connector_result(self):
        """Usar getattr(connector_data, 'error_code', None) funciona com dataclass."""
        connector_result = ConnectorResult(
            source_system="OData",
            status="error",
            error_code="HTTP401",
            message="Unauthorized",
            raw="{}",
            is_mock=False,
            is_fallback=False,
        )

        # getattr funciona com dataclass
        error_code = getattr(connector_result, "error_code", None)
        assert error_code == "HTTP401"

    def test_atributo_direto_funciona_com_connector_result(self):
        """Acesso direto connector_data.error_code funciona com dataclass."""
        connector_result = ConnectorResult(
            source_system="OData",
            status="error",
            error_code="HTTP401",
            message="Unauthorized",
            raw="{}",
            is_mock=False,
            is_fallback=False,
        )

        # Acesso direto funciona com dataclass
        error_code = connector_result.error_code
        assert error_code == "HTTP401"

    def test_getattr_com_default_none_funciona_com_connector_result_sem_error_code(self):
        """getattr com default=None funciona quando error_code não existe."""
        connector_result = ConnectorResult(
            source_system="OData",
            status="ok",
            error_code=None,  # error_code é None
            message="Success",
            raw="{}",
            is_mock=False,
            is_fallback=False,
        )

        error_code = getattr(connector_result, "error_code", None)
        assert error_code is None

    def test_retrieve_node_com_connector_result_dataclass_apos_correcao(self):
        """Após a correção, retrieve_node funciona com ConnectorResult (dataclass)."""
        # Cria um ConnectorResult (dataclass) com error_code
        connector_result = ConnectorResult(
            source_system="OData",
            status="error",
            error_code="HTTP401",
            message="Unauthorized",
            raw="{}",
            is_mock=False,
            is_fallback=False,
        )

        # Estado com connector_data como ConnectorResult (não dict)
        state: CopilotState = {
            "description": "Test description",
            "query": "test query",
            "connector_data": connector_result,  # Dataclass, não dict!
            "interface_type": "odata",
            "identifier": "test-id",
        }

        # Simula ontology_enrichment_enabled=True para forçar o caminho corrigido
        with patch("app.agent.nodes.settings") as mock_settings:
            mock_settings.ontology_enrichment_enabled = True
            # Mocka get_upper_categories_from_category para não causar erro de carregamento TTL
            with (
                patch("app.agent.nodes.get_upper_categories_from_category", return_value=None),
                patch("app.agent.nodes.retrieve", return_value=[]),
            ):
                # Após a correção, isso NÃO deve causar AttributeError
                result = retrieve_node(state)
                assert "retrieved_context" in result
                # O código deve ter usado o retrieval base (sem ontology_category)
                # pois get_upper_categories_from_category foi mockado para None
