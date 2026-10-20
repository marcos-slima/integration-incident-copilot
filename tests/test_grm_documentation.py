"""DA-90: Testes de documentação GRC (ISO 27001, SOC2).

Arquivos criados:
- docs/GRM_MAPPING.md (mapeamento ISO 27001 + SOC2)
- docs/GRM_AUDIT.md (guia de auditoria)
- docs/GRM_CHECKLIST.md (checklist)
"""

import os


class TestGRMDocumentationFiles:
    """Testes de existência e estrutura dos documentos GRC (DA-90)."""

    def test_grm_mapping_exists(self):
        """Verifica que docs/GRM_MAPPING.md existe."""
        mapping_path = "docs/GRM_MAPPING.md"
        assert os.path.exists(mapping_path), f"{mapping_path} não encontrado"

    def test_grm_audit_exists(self):
        """Verifica que docs/GRM_AUDIT.md existe."""
        audit_path = "docs/GRM_AUDIT.md"
        assert os.path.exists(audit_path), f"{audit_path} não encontrado"

    def test_grm_checklist_exists(self):
        """Verifica que docs/GRM_CHECKLIST.md existe."""
        checklist_path = "docs/GRM_CHECKLIST.md"
        assert os.path.exists(checklist_path), f"{checklist_path} não encontrado"

    def test_grm_mapping_has_iso27001_mapping(self):
        """GRM_MAPPING.md deve conter mapeamento para ISO 27001."""
        with open("docs/GRM_MAPPING.md") as f:
            content = f.read()

        assert "ISO 27001" in content or "ISO27001" in content
        assert "A.5" in content or "Section A" in content or "A.6" in content

    def test_grm_mapping_has_soc2_mapping(self):
        """GRM_MAPPING.md deve conter mapeamento para SOC2."""
        with open("docs/GRM_MAPPING.md") as f:
            content = f.read()

        assert "SOC2" in content or "SOC 2" in content
        assert "CC6" in content or "CC6.1" in content or "CC6.6" in content

    def test_grm_audit_has_audience_section(self):
        """GRM_AUDIT.md deve descrever o público-alvo da auditoria."""
        with open("docs/GRM_AUDIT.md") as f:
            content = f.read()

        assert "auditor" in content.lower() or "audience" in content.lower()
        assert "preparação" in content.lower() or "preparation" in content.lower()

    def test_grm_audit_has_procedure_section(self):
        """GRM_AUDIT.md deve descrever procedimentos de auditoria."""
        with open("docs/GRM_AUDIT.md") as f:
            content = f.read()

        assert "procedure" in content.lower() or "passo" in content.lower()
        assert "evidência" in content.lower() or "evidence" in content.lower()

    def test_grm_checklist_has_controls(self):
        """GRM_CHECKLIST.md deve conter lista de controles."""
        with open("docs/GRM_CHECKLIST.md") as f:
            content = f.read()

        assert "control" in content.lower() or "controle" in content.lower()
        assert "checklist" in content.lower() or "verificação" in content.lower()

    def test_grm_checklist_has_yes_no_format(self):
        """GRM_CHECKLIST.md deve usar formato Sim/Não para cada item."""
        with open("docs/GRM_CHECKLIST.md") as f:
            content = f.read()

        # Deve ter linhas com [ ] ou [x] (checkboxes)
        assert "[" in content and "]" in content

    def test_grm_mapping_documents_sources(self):
        """GRM_MAPPING.md deve referenciar fontes documentadas."""
        with open("docs/GRM_MAPPING.md") as f:
            content = f.read()

        # Deve referenciar documentos do projeto ou frameworks
        assert "documentação" in content.lower() or "source" in content.lower()

    def test_all_grm_docs_have_front_matter(self):
        """Todos os documentos GRC devem ter cabeçalho (front matter)."""
        docs = ["docs/GRM_MAPPING.md", "docs/GRM_AUDIT.md", "docs/GRM_CHECKLIST.md"]

        for doc in docs:
            with open(doc) as f:
                content = f.read()

            # Deve ter título (# header)
            assert content.startswith(("#", "<", "---")), (
                f"{doc} deve começar com título HTML ou Markdown"
            )


class TestGRMDocumentationContent:
    """Testes de conteúdo dos documentos GRC."""

    def test_grm_mapping_has_mapping_table(self):
        """GRM_MAPPING.md deve conter tabela de mapeamento."""
        with open("docs/GRM_MAPPING.md") as f:
            content = f.read()

        # Deve ter tabela Markdown (|)
        assert "| " in content and " |" in content

    def test_grm_audit_has_timeline(self):
        """GRM_AUDIT.md deve conter cronograma estimado."""
        with open("docs/GRM_AUDIT.md") as f:
            content = f.read()

        assert "semana" in content.lower() or "week" in content.lower()
        assert "duração" in content.lower() or "duration" in content.lower()

    def test_grm_checklist_is_actionable(self):
        """GRM_CHECKLIST.md deve ser passível de ação (verificável)."""
        with open("docs/GRM_CHECKLIST.md") as f:
            content = f.read()

        # Deve ter instruções claras
        assert "verifique" in content.lower() or "confirm" in content.lower()
        assert "documente" in content.lower() or "document" in content.lower()

    def test_grm_mapping_covers_confidentiality(self):
        """GRM_MAPPING.md deve mapear controles de confidencialidade."""
        with open("docs/GRM_MAPPING.md") as f:
            content = f.read()

        assert "confidencialidade" in content.lower() or "confidentiality" in content.lower()

    def test_grm_mapping_covers_integrity(self):
        """GRM_MAPPING.md deve mapear controles de integridade."""
        with open("docs/GRM_MAPPING.md") as f:
            content = f.read()

        assert "integridade" in content.lower() or "integrity" in content.lower()

    def test_grm_mapping_covers_availability(self):
        """GRM_MAPPING.md deve mapear controles de disponibilidade."""
        with open("docs/GRM_MAPPING.md") as f:
            content = f.read()

        assert "disponibilidade" in content.lower() or "availability" in content.lower()


class TestGRMDocumentationIntegration:
    """Testes de integração entre documentos GRC."""

    def test_grm_docs_are_consistent_on_controls(self):
        """Todos os documentos devem referenciar os mesmos controles principais."""
        with open("docs/GRM_MAPPING.md") as f:
            mapping = f.read()
        with open("docs/GRM_CHECKLIST.md") as f:
            checklist = f.read()

        # Se GRM_MAPPING.md tem controles ISO 27001, GRM_CHECKLIST.md deve refleti-los
        if "A.5" in mapping:
            assert "A.5" in checklist or "segurança" in checklist.lower()
        if "A.6" in mapping:
            assert "A.6" in checklist or "acesso" in checklist.lower()

    def test_all_grm_docs_reference_project_name(self):
        """Todos os documentos devem referenciar o nome do projeto."""
        docs = ["docs/GRM_MAPPING.md", "docs/GRM_AUDIT.md", "docs/GRM_CHECKLIST.md"]

        project_name_variants = [
            "Integration Incident Copilot",
            "IIC",
            "integration-incident-copilot",
        ]

        for doc in docs:
            with open(doc) as f:
                content = f.read()

            found = any(variant.lower() in content.lower() for variant in project_name_variants)
            assert found, f"{doc} deve referenciar o nome do projeto"
