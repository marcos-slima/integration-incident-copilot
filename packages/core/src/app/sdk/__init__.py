"""DA-95: superficie publica do core para produtos de dominio.

Um produto importa do core SO por aqui.
O gate `import_boundaries` (architecture/boundaries.toml) reprova import de
qualquer outro modulo `app.*` a partir do codigo de um produto. Assim, o que
o produto usa do core e' explicito, versionavel e estavel; o resto do core
pode mudar sem quebrar produto, e separar o produto em outro repositorio
vira trocar este pacote por uma dependencia publicada.

Regras desta fachada:
- so reexporta contratos estaveis (tipos, protocolos, funcoes de alto nivel);
- nada de estado mutavel do core (settings, caches) para escrita;
- dependencias pesadas sao importadas sob demanda (o produto que so usa o
  protocolo de plugin nao carrega o grafo).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent.domain_plugins import (
    DOMAIN_BLOCK_TEMPLATE,
    DomainPlugin,
    DomainPrompt,
    domain_prompt_digest,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from qdrant_client import QdrantClient

    from app.models import DiagnosisResponse, IncidentRequest

__all__ = [
    "DOMAIN_BLOCK_TEMPLATE",
    "DomainPlugin",
    "DomainPrompt",
    "asgi_app",
    "base_prompt_digest",
    "dense_embedder",
    "domain_prompt_digest",
    "domain_prompt_provenance",
    "incident_request",
    "plugin_catalog",
    "qdrant_client",
    "run_diagnosis",
    "sparse_vector",
]


# --- API e diagnostico --------------------------------------------------------


def asgi_app() -> FastAPI:
    """App ASGI do core. Configure o ambiente (DOMAIN_PLUGINS, UI_DIST_DIR)
    ANTES de chamar: o app le a configuracao na importacao."""
    from app.main import app

    return app


def incident_request(**fields: Any) -> IncidentRequest:
    """Constroi o `IncidentRequest` do core (contrato de entrada de /diagnose)."""
    from app.models import IncidentRequest

    return IncidentRequest(**fields)


def run_diagnosis(request: IncidentRequest, **kwargs: Any) -> DiagnosisResponse:
    """Pipeline de diagnostico do core (o mesmo de POST /diagnose)."""
    from app.agent.graph import run_diagnosis as _run

    return _run(request, **kwargs)


def plugin_catalog() -> list[dict[str, Any]]:
    """O mesmo catalogo de GET /domain-plugins, para os plugins configurados."""
    from app.agent.domain_plugins import describe_plugins, load_plugins
    from app.config import settings

    return describe_plugins(load_plugins(settings.domain_plugins))


# --- Proveniencia de prompt (DA-53) -------------------------------------------


def base_prompt_digest(locale: str | None = None) -> str:
    """Digest do prompt do core sobre o qual o prompt de dominio se apoia."""
    from app.agent import prompts

    return prompts.get_spec(locale).digest


def domain_prompt_provenance(prompt: DomainPrompt, locale: str | None = None) -> dict[str, str]:
    """`prompt_version`/`prompt_digest` que o core grava para o prompt do plugin."""
    from app.agent.domain_plugins import domain_provenance

    return domain_provenance(prompt, base_prompt_digest(locale))


# --- Infraestrutura de busca (base de conhecimento do produto) ----------------


def qdrant_client() -> QdrantClient:
    """Cliente Qdrant configurado no core (QDRANT_URL/QDRANT_HOST_PORT)."""
    from app.rag.retriever import _get_qdrant_client

    return _get_qdrant_client()


def dense_embedder():
    """Funcao `list[str] -> list[list[float]]` com o modelo de embeddings do core
    (mesma identidade de embedding da DA-45)."""
    from app.rag.retriever import _get_embeddings

    return _get_embeddings().embed_documents


def sparse_vector(text: str):
    """Vetor esparso BM25 no formato do core (busca hibrida, DA-1/DA-29)."""
    from app.rag.ingest import _sparse_vector_for

    return _sparse_vector_for(text)
