"""Tests for DLP/NER validation (DA-80)."""

from app.validation.pii import PIIValidator


class TestPIIValidator:
    """Testa PIIValidator (regex fallback)."""

    def test_email_detection(self) -> None:
        """Detecção de e-mail."""
        validator = PIIValidator()
        text = "Contato: usuario@exemplo.com"
        redacted, findings = validator.validate_and_redact(text)

        assert "[EMAIL_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "EMAIL"
        assert findings[0].text == "usuario@exemplo.com"

    def test_cpf_detection(self) -> None:
        """Detecção de CPF formatado."""
        validator = PIIValidator()
        text = "CPF: 123.456.789-01"
        redacted, findings = validator.validate_and_redact(text)

        assert "[CPF_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "CPF"
        assert findings[0].text == "123.456.789-01"

    def test_cnpj_detection(self) -> None:
        """Detecção de CNPJ."""
        validator = PIIValidator()
        text = "CNPJ: 12.345.678/0001-99"
        redacted, findings = validator.validate_and_redact(text)

        assert "[CNPJ_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "CNPJ"
        assert findings[0].text == "12.345.678/0001-99"

    def test_idoc_detection(self) -> None:
        """Detecção de número de IDoc."""
        validator = PIIValidator()
        text = "IDoc: 0000000012345678"
        redacted, findings = validator.validate_and_redact(text)

        assert "[IDOC_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "IDOC"
        assert findings[0].text == "0000000012345678"

    def test_password_json_detection(self) -> None:
        """Detecção de senha em JSON."""
        validator = PIIValidator()
        text = '{"password": "minha_senha_secreta"}'
        redacted, findings = validator.validate_and_redact(text)

        assert "[PASSWORD_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "PASSWORD"

    def test_password_yaml_detection(self) -> None:
        """Detecção de senha em YAML."""
        validator = PIIValidator()
        text = "password: minha_senha_secreta"
        redacted, findings = validator.validate_and_redact(text)

        assert "[PASSWORD_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "PASSWORD"

    def test_password_env_detection(self) -> None:
        """Detecção de senha em env-var."""
        validator = PIIValidator()
        text = "API_KEY=minha_chave_secreta"
        redacted, findings = validator.validate_and_redact(text)

        assert "[PASSWORD_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "PASSWORD"

    def test_token_detection(self) -> None:
        """Detecção de bearer token."""
        validator = PIIValidator()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        redacted, findings = validator.validate_and_redact(text)

        assert "[TOKEN_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "TOKEN"

    def test_multiple_findings(self) -> None:
        """Múltiplas entidades no mesmo texto."""
        validator = PIIValidator()
        text = "CPF: 111.222.333-44, Email: test@test.com, password: 'segredo'"
        redacted, findings = validator.validate_and_redact(text)

        assert "[CPF_REDACTED]" in redacted
        assert "[EMAIL_REDACTED]" in redacted
        assert "[PASSWORD_REDACTED]" in redacted
        assert len(findings) == 3

    def test_empty_text(self) -> None:
        """Texto vazio."""
        validator = PIIValidator()
        redacted, findings = validator.validate_and_redact("")

        assert redacted == ""
        assert findings == []

    def test_no_pii(self) -> None:
        """Texto sem PII não é modificado."""
        validator = PIIValidator()
        text = "Este texto não contém PII"
        redacted, findings = validator.validate_and_redact(text)

        assert redacted == text
        assert findings == []

    def test_has_sensitive_data(self) -> None:
        """Método has_sensitive_data."""
        validator = PIIValidator()
        assert validator.has_sensitive_data("CPF: 123.456.789-01") is True
        assert validator.has_sensitive_data("Texto limpo") is False

    def test_api_key_keyword(self) -> None:
        """Detecção de api_key como palavra-chave."""
        validator = PIIValidator()
        # Yaml sem aspas
        text = "api_key: minha_chave_aqui"
        redacted, _ = validator.validate_and_redact(text)
        assert "[PASSWORD_REDACTED]" in redacted

    def test_client_secret_yaml(self) -> None:
        """Detecção de client_secret no YAML."""
        validator = PIIValidator()
        text = "client_secret: valor_do_secreto"
        redacted, findings = validator.validate_and_redact(text)
        assert "[PASSWORD_REDACTED]" in redacted
        assert len(findings) == 1
        assert findings[0].type_ == "PASSWORD"

    def test_password_field_single_quote_json(self) -> None:
        """Detecção de senha com aspas simples em JSON."""
        validator = PIIValidator()
        text = "{'senha': 'minha_senha'}"
        redacted, _ = validator.validate_and_redact(text)
        assert "[PASSWORD_REDACTED]" in redacted
