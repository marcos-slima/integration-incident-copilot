"""Superficie admin (DA-46/47/48/49) — modelos, credenciais, metering, sistemas.

O que vive aqui (plano de manutencao via interface, em vez de editar o .env):

    DA-46  Registro de modelos em banco: `llm_models` (origem, model_id,
           base_url, precos por 1M in/out, limite mensal de tokens,
           enabled/is_default). Rotas /admin/models* + UI admin.
    DA-47  Credenciais em repouso: `llm_credentials` cifradas com Fernet
           (app/admin/crypto.py). A unica master key continua no .env
           (LLM_CREDENTIALS_MASTER_KEY) — nunca trafega plaintext na API.
    DA-48  Metering real de tokens: app/admin/metering.py captura `usage`
           das respostas LLM (OpenAI-compatible / Ollama) e acumula em
           `llm_usage` (tokens in/out, requests, failures, custo USD,
           percentual consumido vs. limite mensal).
    DA-49  Catalogo de sistemas integrados (Fase B): `integration_systems`
           com system_key unico, vendor, `connector_type` = mesmo Literal
           fechado do pipeline (odata/rfc/servicenow/salesforce/workday/
           ariba/cap/apim), ambiente, status e base_url — a ponte para
           correlacionar o registro ao `interface_type` de um incidente.
           Página /admin/systems + rotas /admin/api/systems.

Quando LLM_REGISTRY_DB=true (opt-in, default OFF), o runtime
(app/llm/factory.py::get_chat_model) resolve modelo/base_url/credencial a
partir do registro em vez do .env — FAIL-CLOSED: registro vazio para a
origem em uso rejeita a chamada, nunca cai silenciosamente para o .env.

Eixo seguinte (Fase C issues/observabilidade Grafana) e planejado; Fases A
(modelos/credenciais/metering) e B (sistemas) estao implementadas.
"""

from __future__ import annotations

_APP_VERSION = "admin/fase-b"  # marca de evolucao para dashboards/relatorios
