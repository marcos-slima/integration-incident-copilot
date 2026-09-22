"""Testes da camada HTTP (FastAPI)."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app
from app.models import DiagnosisResponse

client = TestClient(app)


def _stub_diagnosis(request):
    return DiagnosisResponse(
        probable_root_cause="Causa raiz de teste (stub)",
        confidence=0.75,
        next_steps=["Passo 1"],
        report_markdown="## Diagnostico\n\nCausa raiz de teste (stub)",
        matched_source="doc_teste.md",
    )


def test_index_returns_404_json_when_frontend_not_built(monkeypatch, tmp_path):
    """Avaliacao externa (nova revisao, P0): GET / nao pode mais
    quebrar com FileNotFoundError quando o bundle do frontend
    (static/dist/) nao foi buildado - deve devolver 404 com uma
    mensagem acionavel."""
    monkeypatch.setattr(main_module, "_STATIC_DIST_INDEX", tmp_path / "nao-existe.html")

    response = client.get("/")

    assert response.status_code == 404
    assert "frontend" in response.json()["detail"].lower()


def test_index_serves_built_frontend_when_present(monkeypatch, tmp_path):
    fake_index = tmp_path / "index.html"
    fake_index.write_text("<html><body>ok</body></html>")
    monkeypatch.setattr(main_module, "_STATIC_DIST_INDEX", fake_index)

    response = client.get("/")

    assert response.status_code == 200
    assert "ok" in response.text


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    # DA-35: status agora pode ser "ok" ou "degraded" dependendo da
    # disponibilidade real de Qdrant/Ollama — em testes unitarios os
    # servicos nao estao no ar, entao o valor esperado e "degraded".
    # O importante aqui e que o campo existe e tem um valor valido.
    assert body["status"] in {"ok", "degraded"}
    assert set(body["connectors"]) == {
        "odata",
        "rfc",
        "servicenow",
        "salesforce",
        "workday",
        "ariba",
        "cap",
        "apim",
    }
    for connector_info in body["connectors"].values():
        assert connector_info["status"] in {"real", "mock", "misconfigured"}
        assert isinstance(connector_info["note"], str) and connector_info["note"]
    assert set(body["infra"]) == {
        "llm_provider",
        "graph_rag_enabled",
        "langfuse_enabled",
        "async_queue_enabled",
        "auth_required",
    }
    # DA-35: campo "services" com probe real de Qdrant/Ollama
    assert "services" in body
    for svc_status in body["services"].values():
        assert svc_status in {"ok", "degraded", "not_configured"}


def test_health_endpoint_reflects_connector_config(monkeypatch):
    """Prova que /health e derivado do .env atual, nao hardcoded - ao
    configurar ODATA_SERVICE_URL, o conector odata deve aparecer como
    "real", e sem ele como "mock"."""
    monkeypatch.setattr(
        "app.connectors.settings.odata_service_url", "https://sap.example.com/odata"
    )
    assert client.get("/health").json()["connectors"]["odata"]["status"] == "real"

    monkeypatch.setattr("app.connectors.settings.odata_service_url", "")
    assert client.get("/health").json()["connectors"]["odata"]["status"] == "mock"


def test_health_endpoint_is_rate_limited_by_global_default():
    """Avaliacao externa (medio prazo, item 1): /health nunca teve seu
    proprio @limiter.limit(...) (nem precisa) - antes de
    SlowAPIMiddleware ser registrado (app/main.py), isso significava
    NENHUM rate limit, apesar do Limiter ter default_limits=["10/minute"].
    Este teste prova que o default global agora vale mesmo para rotas
    sem decorator proprio."""
    for _ in range(10):
        assert client.get("/health").status_code == 200

    assert client.get("/health").status_code == 429


@pytest.mark.integration
def test_diagnose_endpoint_known_case():
    response = client.post("/diagnose", json={"description": "iFlow falhando com erro 401"})
    assert response.status_code == 200
    body = response.json()
    assert body["matched_source"] == "cpi_http_401.md"
    assert body["confidence"] >= 0.5
    assert "report_markdown" in body


@pytest.mark.integration
def test_diagnose_endpoint_rejects_oversized_description():
    huge_description = "x" * 10_000
    response = client.post("/diagnose", json={"description": huge_description})
    assert response.status_code == 422


def test_diagnose_requires_api_key_when_configured(monkeypatch):
    """DA-18: com API_KEY configurada (seja via .env, seja porque
    _ensure_api_keys_configured gerou uma no startup real), /diagnose
    deve exigir X-API-Key valido - sem header, com header errado, e
    so entao com o header correto."""
    monkeypatch.setattr(main_module, "settings", Settings(api_key="secret-diagnose"))
    monkeypatch.setattr(main_module, "run_diagnosis", _stub_diagnosis)
    payload = {"description": "iFlow falhando com erro 401"}

    unauthorized = client.post("/diagnose", json=payload)
    assert unauthorized.status_code == 401

    wrong_key = client.post("/diagnose", json=payload, headers={"X-API-Key": "chave-errada"})
    assert wrong_key.status_code == 401

    authorized = client.post("/diagnose", json=payload, headers={"X-API-Key": "secret-diagnose"})
    assert authorized.status_code == 200
    assert authorized.json()["matched_source"] == "doc_teste.md"


def test_diagnose_async_returns_503_when_redis_not_configured(monkeypatch):
    """Avaliacao externa (medio prazo, item 6): sem REDIS_URL
    configurada, POST /diagnose/async deve falhar explicito (503),
    nao tentar enfileirar silenciosamente ou travar - ver
    app.queue.AsyncQueueUnavailableError."""
    from app.queue import AsyncQueueUnavailableError

    def _raise_unavailable(request_data):
        raise AsyncQueueUnavailableError(
            "Fila assincrona de diagnostico requer REDIS_URL configurada."
        )

    monkeypatch.setattr(main_module, "enqueue_diagnosis", _raise_unavailable)

    response = client.post("/diagnose/async", json={"description": "iFlow travando"})

    assert response.status_code == 503


def test_diagnose_async_returns_202_with_job_id_when_queued(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_diagnosis", lambda request_data: "job-abc")

    response = client.post("/diagnose/async", json={"description": "iFlow travando"})

    assert response.status_code == 202
    assert response.json() == {"job_id": "job-abc", "status": "queued"}


def test_diagnose_async_status_returns_404_when_job_not_found(monkeypatch):
    monkeypatch.setattr(main_module, "get_job_status", lambda job_id: None)

    response = client.get("/diagnose/async/nao-existe")

    assert response.status_code == 404


def test_diagnose_async_status_returns_503_when_redis_not_configured(monkeypatch):
    from app.queue import AsyncQueueUnavailableError

    def _raise_unavailable(job_id):
        raise AsyncQueueUnavailableError(
            "Fila assincrona de diagnostico requer REDIS_URL configurada."
        )

    monkeypatch.setattr(main_module, "get_job_status", _raise_unavailable)

    response = client.get("/diagnose/async/job-abc")

    assert response.status_code == 503


def test_diagnose_async_status_returns_finished_job_result(monkeypatch):
    fake_status = {
        "job_id": "job-abc",
        "status": "finished",
        "result": {"probable_root_cause": "Certificado expirado"},
        "error": None,
    }
    monkeypatch.setattr(main_module, "get_job_status", lambda job_id: fake_status)

    response = client.get("/diagnose/async/job-abc")

    assert response.status_code == 200
    assert response.json() == fake_status


def test_diagnose_returns_504_on_diagnosis_timeout(monkeypatch):
    """Avaliacao externa (curto prazo, item 4): o watchdog global de
    settings.diagnosis_timeout_seconds (app/agent/graph.py) precisa
    virar um 504 Gateway Timeout explicito na API, nao um 500 generico -
    ver app/main.py::_diagnosis_timeout_handler."""
    from app.exceptions import DiagnosisTimeoutError

    def _raise_timeout(request):
        raise DiagnosisTimeoutError("Diagnostico excedeu o timeout de 180.0s")

    monkeypatch.setattr(main_module, "run_diagnosis", _raise_timeout)

    response = client.post("/diagnose", json={"description": "iFlow travando"})

    assert response.status_code == 504
    assert "timeout" in response.json()["detail"].lower()


def test_ensure_api_keys_configured_generates_missing_keys(monkeypatch):
    """DA-18: se API_KEY/A2A_API_KEY nao vierem do .env, o startup deve
    gerar uma chave aleatoria por processo para cada uma - nenhum dos
    dois endpoints protegidos pode ficar sem NENHUMA chave em memoria."""
    fresh_settings = Settings(api_key="", a2a_api_key="")
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    main_module._ensure_api_keys_configured()

    assert fresh_settings.api_key != ""
    assert fresh_settings.a2a_api_key != ""
    assert fresh_settings.api_key != fresh_settings.a2a_api_key


def test_ensure_api_keys_configured_preserves_configured_keys(monkeypatch):
    """DA-18: se o operador ja configurou API_KEY/A2A_API_KEY no .env,
    o startup nao deve sobrescrever - senao a chave documentada/usada
    pelo operador pararia de funcionar a cada restart."""
    configured_settings = Settings(api_key="ja-configurada", a2a_api_key="tambem-configurada")
    monkeypatch.setattr(main_module, "settings", configured_settings)

    main_module._ensure_api_keys_configured()

    assert configured_settings.api_key == "ja-configurada"
    assert configured_settings.a2a_api_key == "tambem-configurada"


def test_ensure_api_keys_configured_rejects_missing_keys_when_require_auth(monkeypatch):
    """Avaliacao externa (curto prazo, item 1): com REQUIRE_AUTH=true,
    o startup deve RECUSAR gerar chaves efemeras - o operador pediu
    uma falha alta e imediata, nao um log de warning que pode passar
    despercebido num deploy real."""
    fresh_settings = Settings(require_auth=True, api_key="", a2a_api_key="", event_mesh_api_key="")
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    with pytest.raises(main_module.MissingRequiredAuthError) as exc_info:
        main_module._ensure_api_keys_configured()

    assert "API_KEY" in str(exc_info.value)
    assert "A2A_API_KEY" in str(exc_info.value)
    assert "EVENT_MESH_API_KEY" in str(exc_info.value)
    # nenhuma chave efemera deve ter sido gerada antes da excecao
    assert fresh_settings.api_key == ""


def test_ensure_api_keys_configured_passes_when_require_auth_and_all_keys_set(monkeypatch):
    fresh_settings = Settings(
        require_auth=True,
        api_key="chave-1",
        a2a_api_key="chave-2",
        event_mesh_api_key="chave-3",
    )
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    main_module._ensure_api_keys_configured()  # nao deve levantar

    assert fresh_settings.api_key == "chave-1"
    assert fresh_settings.a2a_api_key == "chave-2"
    assert fresh_settings.event_mesh_api_key == "chave-3"


def test_ensure_api_keys_configured_reports_only_the_missing_keys(monkeypatch):
    fresh_settings = Settings(
        require_auth=True,
        api_key="chave-configurada",
        a2a_api_key="",
        event_mesh_api_key="tambem-configurada",
    )
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    with pytest.raises(main_module.MissingRequiredAuthError) as exc_info:
        main_module._ensure_api_keys_configured()

    message = str(exc_info.value)
    assert "A2A_API_KEY" in message
    assert "EVENT_MESH_API_KEY" not in message
    # so A2A_API_KEY deveria aparecer na lista de chaves faltando -
    # remove essa ocorrencia e confirma que nenhum outro "API_KEY"
    # solto (isolado, nao dentro de outro nome) sobra na mensagem
    assert "API_KEY" not in message.replace("A2A_API_KEY", "")


def test_ensure_graph_rag_password_configured_rejects_empty_password(monkeypatch):
    """Avaliacao externa (curto prazo, item 6): com GRAPH_RAG_ENABLED=true,
    o startup deve recusar subir se NEO4J_PASSWORD nao foi configurada -
    mesmo motivo do docker-compose.yml nao ter mais um default fraco
    ("changeme123") para o Neo4j do perfil "graphrag"."""
    fresh_settings = Settings(graph_rag_enabled=True, neo4j_password="")
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    with pytest.raises(main_module.MissingGraphRagCredentialsError) as exc_info:
        main_module._ensure_graph_rag_password_configured()

    assert "NEO4J_PASSWORD" in str(exc_info.value)


def test_ensure_graph_rag_password_configured_passes_when_password_set(monkeypatch):
    fresh_settings = Settings(graph_rag_enabled=True, neo4j_password="uma-senha-forte")
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    main_module._ensure_graph_rag_password_configured()  # nao deve levantar


def test_ensure_graph_rag_password_configured_ignored_when_graph_rag_disabled(monkeypatch):
    """Sem GRAPH_RAG_ENABLED, uma NEO4J_PASSWORD vazia e irrelevante -
    o Neo4j nem e usado."""
    fresh_settings = Settings(graph_rag_enabled=False, neo4j_password="")
    monkeypatch.setattr(main_module, "settings", fresh_settings)

    main_module._ensure_graph_rag_password_configured()  # nao deve levantar


def test_verify_incident_returns_400_when_nothing_to_record(monkeypatch):
    """Avaliacao externa (medio prazo, item 5): sem GraphRAG ligado E
    sem trace_id/correct informados, a chamada nao tem NENHUMA
    pre-condicao atendida - nada seria gravado em lugar nenhum, entao
    400 (erro do cliente), nao um 200 silencioso nem um 404 generico."""
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=False))
    response = client.post(
        "/incidents/i1/verify",
        json={"root_cause": "causa confirmada", "verified_by": "human"},
    )
    assert response.status_code == 400
    assert "GraphRAG" in response.json()["detail"]


def test_verify_incident_scores_langfuse_even_with_graph_rag_disabled(monkeypatch):
    """O segundo efeito (score no Langfuse) e independente do primeiro
    (grafo) - um cliente que so tem trace_id (GraphRAG desligado) ainda
    consegue registrar feedback."""
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=False))
    captured = {}

    class _FakeLangfuseClient:
        def create_score(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(main_module, "get_client", lambda: _FakeLangfuseClient())

    response = client.post(
        "/incidents/i1/verify",
        json={
            "root_cause": "causa confirmada",
            "correct": False,
            "trace_id": "trace-abc123",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["graph_updated"] is False
    assert body["langfuse_scored"] is True
    assert captured["trace_id"] == "trace-abc123"
    assert captured["value"] is False
    assert captured["name"] == "diagnosis_correct"
    assert captured["data_type"] == "BOOLEAN"


def test_verify_incident_langfuse_failure_does_not_break_endpoint(monkeypatch):
    """Feedback no Langfuse e best-effort - uma falha ao gravar o score
    (Langfuse fora do ar, por exemplo) nao pode derrubar o endpoint
    inteiro quando o grafo (se aplicavel) ja foi atualizado com sucesso."""
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    monkeypatch.setattr(main_module, "verify_incident", lambda **kwargs: True)

    class _BrokenLangfuseClient:
        def create_score(self, **kwargs):
            raise RuntimeError("Langfuse indisponivel")

    monkeypatch.setattr(main_module, "get_client", lambda: _BrokenLangfuseClient())

    response = client.post(
        "/incidents/i1/verify",
        json={
            "root_cause": "causa confirmada",
            "correct": True,
            "trace_id": "trace-abc123",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["graph_updated"] is True
    assert body["langfuse_scored"] is False


def test_verify_incident_without_correct_does_not_score_langfuse(monkeypatch):
    """trace_id sozinho, sem `correct`, nao gera score - mantem o
    comportamento de antes desta mudanca para quem so manda root_cause
    (so grafo, sem feedback de correto/incorreto)."""
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    monkeypatch.setattr(main_module, "verify_incident", lambda **kwargs: True)
    scored = {"called": False}

    class _FakeLangfuseClient:
        def create_score(self, **kwargs):
            scored["called"] = True

    monkeypatch.setattr(main_module, "get_client", lambda: _FakeLangfuseClient())

    response = client.post(
        "/incidents/i1/verify",
        json={"root_cause": "causa confirmada", "trace_id": "trace-abc123"},
    )
    assert response.status_code == 200
    assert response.json()["langfuse_scored"] is False
    assert scored["called"] is False


def test_verify_incident_returns_404_when_incident_not_found(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    monkeypatch.setattr(main_module, "verify_incident", lambda **kwargs: False)
    response = client.post(
        "/incidents/inexistente/verify",
        json={"root_cause": "causa confirmada", "verified_by": "human"},
    )
    assert response.status_code == 404
    assert "inexistente" in response.json()["detail"]


def test_verify_incident_returns_200_on_success(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    captured = {}

    def _fake_verify_incident(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(main_module, "verify_incident", _fake_verify_incident)
    response = client.post(
        "/incidents/i1/verify",
        json={"root_cause": "causa confirmada por Basis", "verified_by": "human"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "incident_id": "i1",
        "status": "verified",
        "graph_updated": True,
        "langfuse_scored": False,
    }
    assert captured["incident_id"] == "i1"
    assert captured["verified_root_cause"] == "causa confirmada por Basis"
    assert captured["verified_by"] == "human"


def test_verify_incident_defaults_verified_by_to_human(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(graph_rag_enabled=True))
    captured = {}

    def _fake_verify_incident(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(main_module, "verify_incident", _fake_verify_incident)
    response = client.post("/incidents/i1/verify", json={"root_cause": "causa confirmada"})
    assert response.status_code == 200
    assert captured["verified_by"] == "human"


def test_verify_incident_requires_api_key_when_configured(monkeypatch):
    """Mesma politica de autenticacao de /diagnose (DA-18) - X-API-Key
    tambem protege este endpoint mutante."""
    monkeypatch.setattr(
        main_module, "settings", Settings(api_key="secret-verify", graph_rag_enabled=True)
    )
    monkeypatch.setattr(main_module, "verify_incident", lambda **kwargs: True)
    payload = {"root_cause": "causa confirmada"}

    unauthorized = client.post("/incidents/i1/verify", json=payload)
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/incidents/i1/verify", json=payload, headers={"X-API-Key": "secret-verify"}
    )
    assert authorized.status_code == 200


# ── DA-35: _probe_infra_services — readiness real ────────────────────────────


def test_health_services_degraded_when_qdrant_unreachable(monkeypatch):
    """DA-35: quando Qdrant nao responde, /health retorna status=degraded."""
    import app.main as main_module
    from app.config import Settings

    def _probe_degraded():
        return {"qdrant": "degraded", "ollama": "not_configured"}

    monkeypatch.setattr(main_module, "_probe_infra_services", _probe_degraded)
    monkeypatch.setattr(main_module, "settings", Settings(qdrant_url="http://qdrant:6333"))

    result = main_module.health()
    assert result["services"]["qdrant"] == "degraded"
    assert result["status"] == "degraded"


def test_health_services_ok_when_qdrant_responds(monkeypatch):
    """DA-35: quando Qdrant responde 200, servico aparece como ok."""
    import app.main as main_module
    from app.config import Settings

    def _probe_ok():
        return {"qdrant": "ok", "ollama": "not_configured"}

    monkeypatch.setattr(main_module, "_probe_infra_services", _probe_ok)
    monkeypatch.setattr(main_module, "settings", Settings(qdrant_url="http://qdrant:6333"))

    result = main_module.health()
    assert result["services"]["qdrant"] == "ok"
    assert result["status"] == "ok"


def test_health_services_not_configured_when_no_qdrant_url(monkeypatch):
    """DA-35: sem QDRANT_URL configurado, status e 'not_configured'."""
    import app.main as main_module
    from app.config import Settings

    def _probe_not_configured():
        return {"qdrant": "not_configured", "ollama": "not_configured"}

    monkeypatch.setattr(main_module, "_probe_infra_services", _probe_not_configured)
    monkeypatch.setattr(main_module, "settings", Settings(qdrant_url="", ollama_host=""))

    result = main_module.health()
    assert result["services"]["qdrant"] == "not_configured"
    assert result["services"]["ollama"] == "not_configured"
    # sem servicos configurados, nao pode ser "degraded" (nao ha nada pra degradar)
    assert result["status"] == "ok"
