"""Hermetic installed-product HTTP checks using public environment configuration."""

import os
import secrets

os.environ.update(
    API_KEY=secrets.token_urlsafe(32),
    DATABASE_URL="",
    REDIS_URL="",
    GRAPH_RAG_ENABLED="false",
    CHECKPOINTING_ENABLED="false",
    PROMETHEUS_ENABLED="false",
    ONTOLOGY_ENRICHMENT_ENABLED="false",
    AMQP_ENABLED="false",
    WEB_SEARCH_ENABLED="false",
)
