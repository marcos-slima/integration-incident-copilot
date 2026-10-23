"""Diagnostico de configuracao (`python -m app.config`): segredos mascarados."""


def test_print_effective_config_mascara_senha_em_url(monkeypatch, capsys):
    """`python -m app.config` nao pode imprimir a senha de DATABASE_URL/
    REDIS_URL (nao batem em _SENSITIVE_KEYWORDS) nem os hashes de
    WEB_UI_USERS."""
    from app import config

    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://iic:fake-senha-banco@127.0.0.1:5432/iic",
            redis_url="redis://:fake-senha-redis@127.0.0.1:6379/0",
            web_ui_users="marcos:pbkdf2_sha256.600000.aabbccdd.eeff0011",
        ),
    )
    config._print_effective_config()
    saida = capsys.readouterr().out
    assert "fake-senha-banco" not in saida and "fake-senha-redis" not in saida
    assert "postgresql+asyncpg://iic:***@127.0.0.1:5432/iic" in saida
    assert "aabbccdd" not in saida


def test_mask_url_credentials_preserva_url_sem_senha():
    from app.config import _mask_url_credentials

    assert _mask_url_credentials("http://127.0.0.1:6333") == "http://127.0.0.1:6333"
    assert _mask_url_credentials("bolt://neo4j@host:7687") == "bolt://neo4j@host:7687"
    assert _mask_url_credentials("") == ""
