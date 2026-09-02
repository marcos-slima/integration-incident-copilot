"""Configuracao centralizada do SAP Integration Copilot.

Toda configuracao (URLs, modelos, credenciais) vem daqui - nunca
hardcoded espalhado pelo codigo. Le do .env automaticamente.

Para VER exatamente o que esta configurado agora (sem precisar ler
codigo Python), rode:

    uv run python -m app.config

Isso imprime a configuracao efetiva (valores do .env + defaults),
mascarando senhas/chaves - util pra depurar "por que esta apontando
pro lugar errado" sem depender de mais ninguem.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM provider - "ollama" (default, local-first, sem custo de API)
    # ou "openai"/"azure_openai" (para clientes que ja tem essa assinatura,
    # ou como fallback de capacidade quando o hardware local nao aguenta
    # um modelo maior). Ver docs/ARCHITECTURE.md e a Decisao de
    # Arquitetura #10 no README sobre por que isso e plugavel em vez de
    # hardcoded: o modelo de negocio do projeto (viabilizar IA para quem
    # nao pode/nao quer pagar SAP AI Core) exige rodar tanto 100% local
    # quanto, quando fizer sentido para o cliente, sobre um provedor que
    # ele ja tenha contratado - sem reescrever o grafo.
    llm_provider: Literal["ollama", "openai", "azure_openai"] = "ollama"

    # Ollama (default local-first)
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5-coder:32b"
    embedding_model: str = "nomic-embed-text"

    # OpenAI / compativel com OpenAI (inclui endpoints locais tipo
    # vLLM/LM Studio que implementam a mesma API) - so relevante se
    # llm_provider="openai"
    openai_api_key: str = ""
    openai_base_url: str = ""  # vazio = API oficial da OpenAI

    # Azure OpenAI - so relevante se llm_provider="azure_openai"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"

    # SAP RFC (conexao direta via pyrfc) - so relevante para
    # RFCConnector(use_real=True); modo demo/mock (default) nao le
    # nenhum destes campos
    sap_ashost: str = ""
    sap_sysnr: str = "00"
    sap_client: str = "100"
    sap_user: str = ""
    sap_password: str = ""

    # Qdrant
    qdrant_url: str = "http://127.0.0.1:6333"

    # Neo4j - reservado para uso futuro (GraphRAG entre
    # interfaces/documentos, ver docs/ARCHITECTURE.md); nao e lido pelo
    # pipeline de RAG atual (app/rag/ingest.py e app/rag/retriever.py
    # usam so Qdrant hoje)
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # Langfuse - opcional; se as chaves ficarem vazias o SDK nao envia
    # trace nenhum (nao quebra), entao rodar sem observabilidade
    # completa (ex: docker-compose.yml deste repo, que nao sobe o stack
    # completo do Langfuse) continua funcional
    langfuse_host: str = "http://127.0.0.1:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ServiceNow - conector opcional para cenarios que envolvem ITSM
    # nao-SAP (ver app/connectors/servicenow_connector.py); se
    # servicenow_instance_url ficar vazio, o conector roda em modo
    # demo (mock), do mesmo jeito que os conectores SAP
    servicenow_instance_url: str = ""
    servicenow_username: str = ""
    servicenow_password: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # nao quebra se o .env tiver variaveis extras
    )


settings = Settings()


def _mask(value: str) -> str:
    if not value:
        return "(vazio)"
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-3:]}"


_SENSITIVE_KEYWORDS = ("password", "secret", "key")


def _print_effective_config() -> None:
    print("Configuracao efetiva (env_file=.env + defaults do codigo):\n")
    for field_name in settings.__class__.model_fields:
        value = str(getattr(settings, field_name))
        is_sensitive = any(kw in field_name for kw in _SENSITIVE_KEYWORDS)
        display = _mask(value) if is_sensitive else (value or "(vazio)")
        print(f"  {field_name:22} = {display}")
    print(
        "\nSe algum valor nao bater com o esperado, confira o arquivo "
        "'.env' na raiz do projeto - essa e a unica fonte que este "
        "comando le, alem dos defaults acima."
    )


if __name__ == "__main__":
    _print_effective_config()
